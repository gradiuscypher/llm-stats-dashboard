"""Application configuration via environment variables."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://lsd_user:lsd_pass@localhost:5432/lsd_dev"
    test_database_url: str = "postgresql+psycopg://lsd_user:lsd_pass@localhost:5432/lsd_test"

    # Security
    secret_key: str = "CHANGE_ME_in_production_at_least_32_chars_random"
    session_max_age_seconds: int = 60 * 60 * 8  # 8 hours
    csrf_token_max_age_seconds: int = 60 * 60 * 8

    # App
    app_env: str = "development"
    allowed_origins: list[str] = ["http://localhost:5173"]
    max_log_body_bytes: int = 1024 * 1024  # 1 MB

    # Logging
    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR
    log_file: str = "logs/lsd.log"  # path relative to backend/; empty = no file

    # OpenRouter proxy
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_referer: str = ""
    openrouter_app_title: str = "LLM Stats Dashboard"
    proxy_plugins: str = "compression,logging"  # ordered, comma-separated plugin names
    proxy_upstream_timeout_s: int = 120
    proxy_stream_idle_timeout_s: int = 120

    # Compression (Headroom) — Tier 1: CompressConfig knobs
    compression_protect_analysis_context: bool = True
    compression_protect_recent: int = 4
    compression_compress_user_messages: bool = False
    compression_compress_system_messages: bool = True
    compression_target_ratio: float | None = None
    compression_min_tokens: int = 250
    compression_kompress_model: str = (
        ""  # "" -> Headroom default ONNX model; "disabled" -> ML text off
    )
    compression_optimize: bool = True  # A/B kill-switch: False = passthrough
    compression_model_limit: int = 200000  # context window override for token pressure

    # Compression (Headroom) — Tier 2: per-transform pipeline config
    compression_cache_aligner_enabled: bool = False
    compression_intercept_tools: bool = False
    compression_ccr_enabled: bool = True
    compression_ccr_inject_marker: bool = True
    compression_smartcrusher_max_items: int = 15
    compression_smartcrusher_min_tokens: int = 200
    compression_smartcrusher_profile: str = "moderate"  # "conservative"|"moderate"|"aggressive"
    compression_read_compress_superseded: bool = False

    headroom_telemetry: bool = False  # if False, set HEADROOM_TELEMETRY=off at startup

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()

# Disable Headroom telemetry unless explicitly enabled.
if not settings.headroom_telemetry:
    os.environ.setdefault("HEADROOM_TELEMETRY", "off")
