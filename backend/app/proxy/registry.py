"""Plugin registry — maps plugin names to classes and builds the pipeline.

Designed for later extension to per-user/per-key pipelines (resolve_pipeline
can swap the global list for a DB-backed lookup without changing the call site).
"""

import logging

from app.config import settings
from app.proxy.plugins.base import BasePlugin
from app.proxy.plugins.compression import CompressionPlugin
from app.proxy.plugins.logging import LoggingPlugin

logger = logging.getLogger(__name__)

# Registry of all available plugins by name.
PLUGIN_REGISTRY: dict[str, type[BasePlugin]] = {
    "logging": LoggingPlugin,
    "compression": CompressionPlugin,
}


def _build_pipeline_from_names(names: list[str]) -> list[BasePlugin]:
    """Instantiate plugins from a list of names in registration order."""
    pipeline: list[BasePlugin] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        cls = PLUGIN_REGISTRY.get(name)
        if cls is None:
            logger.warning("Unknown plugin %r — skipping", name)
            continue
        pipeline.append(cls())
    return pipeline


# Singleton pipeline built once at module load.
# In the future, replace this with `resolve_pipeline(user, api_key)`.
_pipeline: list[BasePlugin] | None = None


def get_pipeline() -> list[BasePlugin]:
    """Return the configured plugin pipeline, built lazily from config."""
    global _pipeline
    if _pipeline is None:
        names = [n.strip() for n in settings.proxy_plugins.split(",") if n.strip()]
        _pipeline = _build_pipeline_from_names(names)
        logger.info("Proxy pipeline: %s", [p.name for p in _pipeline])
    return _pipeline
