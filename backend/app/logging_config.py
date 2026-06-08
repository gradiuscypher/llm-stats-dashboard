"""Logging configuration for LLM Stats Dashboard.

Sets up:
  - Console handler  — INFO+ always, colourised in dev
  - Rotating file handler — written to LOG_FILE when configured (default logs/lsd.log)

Call configure_logging() once at startup (main.py).  Uvicorn's own access log
continues to work normally alongside this config.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

from app.config import settings

# ── Format ────────────────────────────────────────────────────────────────────

_CONSOLE_FMT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-8s %(name)s [%(filename)s:%(lineno)d]  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Loggers we always want at DEBUG regardless of the global level — these are the
# ones most useful for diagnosing the proxy / auth issues.
_VERBOSE_LOGGERS = [
    "app.security.api_key_auth",
    "app.routers.proxy",
    "app.proxy.pipeline",
    "app.proxy.plugins.logging",
]


def configure_logging() -> None:
    """Wire up console + optional file logging.  Safe to call multiple times."""

    numeric_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # ── Root logger ───────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let handlers filter; root must be lowest

    # Don't add duplicate handlers if already configured (e.g. during tests)
    if root.handlers:
        return

    # ── Console handler ───────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATE_FMT))
    root.addHandler(console)

    # ── File handler ──────────────────────────────────────────────────────────
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        # File always captures DEBUG so you can grep the full picture
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATE_FMT))
        root.addHandler(file_handler)

    # ── Verbose loggers (always DEBUG to file / console if level allows) ──────
    for name in _VERBOSE_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    # ── Silence noisy third-party loggers ─────────────────────────────────────
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.app_env == "development" else logging.WARNING
    )

    logging.getLogger(__name__).info(
        "Logging configured: level=%s file=%s",
        settings.log_level.upper(),
        settings.log_file or "(none)",
    )
