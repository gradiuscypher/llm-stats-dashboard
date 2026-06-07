"""Application configuration via environment variables."""

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
    log_level: str = "INFO"         # DEBUG | INFO | WARNING | ERROR
    log_file: str = "logs/lsd.log"  # path relative to backend/; empty = no file

    # OpenRouter proxy
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_referer: str = ""
    openrouter_app_title: str = "LLM Stats Dashboard"
    proxy_plugins: str = "logging"  # ordered, comma-separated plugin names
    proxy_upstream_timeout_s: int = 120
    proxy_stream_idle_timeout_s: int = 120

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
