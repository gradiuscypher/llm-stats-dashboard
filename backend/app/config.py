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

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
