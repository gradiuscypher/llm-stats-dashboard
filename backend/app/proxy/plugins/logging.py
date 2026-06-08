"""DEPRECATED — logging has been moved to app.proxy.logging_sink.

The LoggingPlugin class is no longer used. The proxy router now calls
persist_log / persist_error_log from logging_sink.py directly.

This file is kept as a stub to avoid import errors from any external
references, and will be removed in a future cleanup.
"""

import logging

logger = logging.getLogger(__name__)


# The old LoggingPlugin class. Not imported by registry anymore.
class LoggingPlugin:  # type: ignore[no-redef]
    """Deprecated. Use logging_sink.persist_log instead."""

    name = "logging"
