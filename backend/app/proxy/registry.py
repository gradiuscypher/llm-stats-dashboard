"""Plugin registry — maps plugin names to RequestTransform classes.

Supports per-user + per-conversation toggles via resolve_pipeline().
The logging "sink" is no longer a plugin — the router calls persist_log directly.
"""

import logging
import uuid

from sqlmodel import Session, select

from app.config import settings
from app.models.plugin_config import PluginConfig, PluginConfigConversation
from app.proxy.interceptor import RequestTransform
from app.proxy.plugins.compression import CompressionPlugin
from app.proxy.plugins.word_count import WordCountPlugin

logger = logging.getLogger(__name__)

# Registry of all available TRANSFORM plugins by name.
PLUGIN_REGISTRY: dict[str, type] = {
    "compression": CompressionPlugin,
    "word_count": WordCountPlugin,
}

# Plugins that cannot be disabled by users; logging is no longer a plugin.
LOCKED_PLUGINS: set[str] = set()

# Default enabled set — plugins in PROXY_PLUGINS env are on by default.
_DEFAULT_ENABLED: set[str] = set(
    n.strip() for n in settings.proxy_plugins.split(",") if n.strip() and n.strip() != "logging"
)


def resolve_pipeline(
    user_id: uuid.UUID,
    conversation_id: str | None,
    db: Session,
) -> list[RequestTransform]:
    """Build the ordered transform list for this user/conversation.

    Order is defined by PROXY_PLUGINS env.  A plugin is included iff it
    resolves to enabled for (user, conversation):
      per-conversation override  →  user-global  →  default.

    Plugins the user has explicitly enabled via the dashboard (PluginConfig
    rows) are included even if they are not in PROXY_PLUGINS — they are
    appended after the env-ordered plugins.
    """
    # Ordered names from env (base order) — filtered to only transform plugins.
    env_names = [
        n.strip()
        for n in settings.proxy_plugins.split(",")
        if n.strip() and n.strip() != "logging" and n.strip() in PLUGIN_REGISTRY
    ]

    # Batch-fetch ALL per-user global config for this user.
    global_rows = db.exec(
        select(PluginConfig).where(PluginConfig.user_id == user_id)
    ).all()
    global_map: dict[str, bool] = {r.plugin_name: r.enabled for r in global_rows}

    # Batch-fetch per-conversation overrides (if a conversation id is known).
    conv_map: dict[str, bool] = {}
    if conversation_id:
        conv_rows = db.exec(
            select(PluginConfigConversation).where(
                PluginConfigConversation.user_id == user_id,
                PluginConfigConversation.conversation_id == conversation_id,
            )
        ).all()
        conv_map = {r.plugin_name: r.enabled for r in conv_rows}

    # Build the full list of candidate plugin names:
    candidate_names: list[str] = list(dict.fromkeys(env_names))  # deduplicate, preserve order
    extra_enabled: set[str] = {
        n for n, enabled in global_map.items() if enabled
    } | {
        n for n, enabled in conv_map.items() if enabled
    }
    user_extra = sorted(
        n for n in extra_enabled
        if n not in candidate_names and n in PLUGIN_REGISTRY
    )
    candidate_names.extend(user_extra)

    # Resolve enabled state for each candidate
    pipeline: list[RequestTransform] = []
    for name in candidate_names:
        cls = PLUGIN_REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown plugin %r — skipping", name)
            continue

        if name in LOCKED_PLUGINS:
            enabled = True
        elif name in conv_map:
            enabled = conv_map[name]
        elif name in global_map:
            enabled = global_map[name]
        else:
            enabled = name in _DEFAULT_ENABLED

        if enabled:
            pipeline.append(cls())

    logger.debug(
        "Pipeline for user=%s conv=%s: %s",
        user_id,
        conversation_id,
        [p.name for p in pipeline],
    )
    return pipeline


# ---------------------------------------------------------------------------
# Legacy singleton (kept for health/migration compatibility)
# ---------------------------------------------------------------------------

def _build_pipeline_from_names(names: list[str]) -> list[RequestTransform]:
    """Instantiate transforms from a list of names in registration order."""
    pipeline: list[RequestTransform] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if name == "logging":
            continue
        cls = PLUGIN_REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown plugin %r — skipping", name)
            continue
        pipeline.append(cls())
    return pipeline


# Singleton pipeline built once at module load (legacy path for health endpoint).
_pipeline: list[RequestTransform] | None = None


def get_pipeline() -> list[RequestTransform]:
    """Return the configured transform pipeline, built lazily from config.

    Prefer resolve_pipeline() for per-request resolution.
    This legacy singleton is used by the health endpoint only.
    """
    global _pipeline
    if _pipeline is None:
        names = [n.strip() for n in settings.proxy_plugins.split(",") if n.strip()]
        _pipeline = _build_pipeline_from_names(names)
        logger.info("Proxy transforms: %s", [p.name for p in _pipeline])
    return _pipeline
