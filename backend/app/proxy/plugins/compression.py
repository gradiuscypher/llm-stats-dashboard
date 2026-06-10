"""CompressionPlugin — Headroom-backed request compression transform.

Runs on the proxy request path, before forwarding to OpenRouter. Uses
Headroom's TransformPipeline with a HeadroomConfig built from LSD settings,
giving us control over:
- CacheAligner (prefix stabilization for KV-cache hits)
- SmartCrusher (JSON array compression) — max_items, min_tokens, bias profile
- CCR (Compress-Cache-Retrieve) — reversible compression markers
- Read lifecycle (stale/superseded Read detection)
- Tool-result interceptors (ast-grep Read outlines)

CPU-only: Headroom's proxy+code extras include ONNX INT8 Kompress for
ML text compression — no GPU/PyTorch required.
"""

import logging
import threading
from typing import Any

from app.config import settings
from app.proxy.interceptor import TransformContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level pipeline singleton (same pattern as Headroom's own _get_pipeline)
# ---------------------------------------------------------------------------

_pipeline: Any = None
_pipeline_lock = threading.Lock()


def _build_pipeline() -> Any:
    """Build a TransformPipeline configured from LSD settings.

    Returns a TransformPipeline whose HeadroomConfig and sub-transform configs
    are layered from our env vars.  Called once at module load / first request.
    """
    # Lazy imports — Headroom is a heavyweight package.
    from headroom.config import (
        CacheAlignerConfig,
        CCRConfig,
        HeadroomConfig,
        ReadLifecycleConfig,
        SmartCrusherConfig,
    )
    from headroom.transforms.pipeline import TransformPipeline

    # Resolve SmartCrusher bias profile. The profile is applied as a global
    # per-tool bias via ContentRouterConfig.tool_profiles, which maps tool
    # names to CompressionProfile(bias, min_k).  Individual settings
    # (compression_smartcrusher_max_items, compression_smartcrusher_min_tokens)
    # are separate fine-tuning knobs on SmartCrusherConfig itself.
    profile_name = settings.compression_smartcrusher_profile.strip().lower()
    if profile_name == "conservative":
        profile_bias = 1.5
        profile_min_k = 5
    elif profile_name == "aggressive":
        profile_bias = 0.7
        profile_min_k = 3
    else:  # "moderate" (and unknown)
        profile_bias = 1.0
        profile_min_k = 3

    headroom_cfg = HeadroomConfig(
        cache_aligner=CacheAlignerConfig(
            enabled=settings.compression_cache_aligner_enabled,
        ),
        cache_optimizer=HeadroomConfig().cache_optimizer,  # keep defaults
        ccr=CCRConfig(
            enabled=settings.compression_ccr_enabled,
            inject_retrieval_marker=settings.compression_ccr_inject_marker,
        ),
        smart_crusher=SmartCrusherConfig(
            max_items_after_crush=settings.compression_smartcrusher_max_items,
            min_tokens_to_crush=settings.compression_smartcrusher_min_tokens,
            # Profile-driven bias (set at pipeline build time).
            # The ContentRouter pre-seeds SmartCrusher at construction
            # with ccR config; we override via the pipeline-level config
            # which the _build_default_transforms path reads.
        ),
        intercept_tool_results=settings.compression_intercept_tools,
    )

    pipeline = TransformPipeline(config=headroom_cfg)

    # The ContentRouter lazily constructs SmartCrusher inside
    # _get_smart_crusher().  Pre-seed it with our custom config so it
    # picks up max_items_after_crush, min_tokens_to_crush, bias, etc.
    # We also pass ccr_config for marker injection control.
    for transform in pipeline.transforms:
        if transform.name == "content_router":
            from headroom.transforms.smart_crusher import SmartCrusher

            transform._smart_crusher = SmartCrusher(
                config=SmartCrusherConfig(
                    max_items_after_crush=settings.compression_smartcrusher_max_items,
                    min_tokens_to_crush=settings.compression_smartcrusher_min_tokens,
                ),
                ccr_config=CCRConfig(
                    enabled=settings.compression_ccr_enabled,
                    inject_retrieval_marker=settings.compression_ccr_inject_marker,
                ),
            )

            # Patch ContentRouter's read lifecycle config
            transform.config.read_lifecycle = ReadLifecycleConfig(
                compress_superseded=settings.compression_read_compress_superseded,
            )

            # Patch ContentRouter's per-tool compression profiles to apply
            # the global bias preset.  We build a dict that maps every tool
            # from DEFAULT_TOOL_PROFILES to a CompressionProfile with the
            # chosen bias+min_k, preserving the original max_k cap (if any).
            from headroom.config import DEFAULT_TOOL_PROFILES, CompressionProfile

            transform.config.tool_profiles = {
                tool: CompressionProfile(bias=profile_bias, min_k=profile_min_k,
                                         max_k=orig.max_k)
                for tool, orig in DEFAULT_TOOL_PROFILES.items()
            }

            # Patch CCR on ContentRouter's own config (used by
            # _get_smart_crusher's ccr_config builder AND direct CCR checks)
            transform.config.ccr_enabled = settings.compression_ccr_enabled
            transform.config.ccr_inject_marker = settings.compression_ccr_inject_marker
            break

    logger.info(
        "Headroom pipeline built: cache_aligner=%s, ccr=%s, "
        "smartcrusher_profile=%s(max_items=%d,min_tokens=%d), "
        "intercept_tools=%s, read_superseded=%s",
        settings.compression_cache_aligner_enabled,
        settings.compression_ccr_enabled,
        profile_name,
        settings.compression_smartcrusher_max_items,
        settings.compression_smartcrusher_min_tokens,
        settings.compression_intercept_tools,
        settings.compression_read_compress_superseded,
    )

    return pipeline


def _get_pipeline() -> Any:
    """Get or create the singleton compression pipeline."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        _pipeline = _build_pipeline()
        return _pipeline


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class CompressionPlugin:
    """Request-side transform that compresses messages via Headroom.

    Fail-open: if compress() raises, original messages are returned unchanged.
    """

    name = "compression"

    def transform_request(
        self, messages: list[dict], ctx: TransformContext
    ) -> list[dict]:
        """Compress messages using Headroom; record metrics on ctx."""
        if not messages:
            return messages

        # A/B kill-switch: compression_optimize=False = passthrough
        if not settings.compression_optimize:
            return messages

        try:
            from headroom import CompressConfig  # lazy import

            cfg = CompressConfig(
                compress_user_messages=settings.compression_compress_user_messages,
                compress_system_messages=settings.compression_compress_system_messages,
                protect_recent=settings.compression_protect_recent,
                protect_analysis_context=settings.compression_protect_analysis_context,
                target_ratio=settings.compression_target_ratio,
                min_tokens_to_compress=settings.compression_min_tokens,
                kompress_model=settings.compression_kompress_model or None,
            )

            pipeline = _get_pipeline()
            result = pipeline.apply(
                messages=messages,
                model=ctx.model,
                model_limit=settings.compression_model_limit,
                # CompressConfig fields forwarded as kwargs (same pattern as
                # headroom.compress() — ContentRouter resolves them at call time)
                compress_user_messages=cfg.compress_user_messages,
                compress_system_messages=cfg.compress_system_messages,
                protect_recent=cfg.protect_recent,
                protect_analysis_context=cfg.protect_analysis_context,
                target_ratio=cfg.target_ratio,
                min_tokens_to_compress=cfg.min_tokens_to_compress,
                kompress_model=cfg.kompress_model,
            )

            tokens_before = result.tokens_before
            tokens_after = result.tokens_after
            tokens_saved = tokens_before - tokens_after
            ratio = tokens_saved / tokens_before if tokens_before > 0 else 0.0

            # Hand metrics back to the proxy via the writable context dict.
            ctx.request_metadata["compression"] = {
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "tokens_saved": tokens_saved,
                "compression_ratio": ratio,
                "transforms_applied": result.transforms_applied,
            }
            return result.messages

        except Exception:
            logger.exception("CompressionPlugin.transform_request failed")
            return messages
