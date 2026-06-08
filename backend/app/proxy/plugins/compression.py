"""CompressionPlugin — Headroom-backed request compression transform.

Runs on the proxy request path, before forwarding to OpenRouter. Uses
Headroom's pure synchronous compress() to reduce token usage in the
request message list, and records token-savings metrics for the dashboard.

CPU-only: Headroom's proxy+code extras include ONNX INT8 Kompress for
ML text compression — no GPU/PyTorch required.
"""

import logging

from app.config import settings
from app.proxy.interceptor import TransformContext

logger = logging.getLogger(__name__)


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

        try:
            from headroom import CompressConfig, compress  # lazy import

            cfg = CompressConfig(
                compress_user_messages=settings.compression_compress_user_messages,
                compress_system_messages=settings.compression_compress_system_messages,
                protect_recent=settings.compression_protect_recent,
                target_ratio=settings.compression_target_ratio,
                min_tokens_to_compress=settings.compression_min_tokens,
                kompress_model=settings.compression_kompress_model or None,
            )
            result = compress(messages, model=ctx.model, config=cfg)

            # Hand metrics back to the proxy via the writable context dict.
            ctx.request_metadata["compression"] = {
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "tokens_saved": result.tokens_saved,
                "compression_ratio": result.compression_ratio,
                "transforms_applied": result.transforms_applied,
            }
            return result.messages
        except Exception:
            logger.exception("CompressionPlugin.transform_request failed")
            return messages  # fail-open
