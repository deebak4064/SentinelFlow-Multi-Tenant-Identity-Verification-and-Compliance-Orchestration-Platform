from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (12-factor). No secrets in code."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="SF_", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinelflow"
    redis_url: str = "redis://localhost:6379/0"

    # 32-byte urlsafe-base64 key used for AES-GCM field encryption of PII at rest.
    field_encryption_key: str = Field(default="", repr=False)

    session_ttl_minutes: int = 480
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    rate_limit_per_minute: int = 600
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Workflow execution: "inline" runs in the API process; "queue" hands off to Redis workers.
    default_execution_mode: str = "inline"

    # Optional external LLM. When unset the AI analyst uses a deterministic fallback.
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str = "https://api.anthropic.com"
    llm_timeout_seconds: float = 20.0

    db_pool_size: int = 20
    db_max_overflow: int = 20

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and not s.field_encryption_key:
        raise RuntimeError("SF_FIELD_ENCRYPTION_KEY must be set in production")
    return s
