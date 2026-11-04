"""Application configuration loaded from environment variables / .env.

Security notes
--------------
* Secrets are never hardcoded. ``JWT_SECRET_KEY`` is required and wrapped in
  ``SecretStr`` so it is not accidentally printed in logs or tracebacks.
* Production startup refuses placeholder secrets and short secrets.
* The database URL must use the PostgreSQL psycopg driver; Milestone 1 relies on
  PostgreSQL composite foreign keys for tenant integrity (SQLite cannot do this
  reliably).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32
INSECURE_SECRET_PREFIXES = ("change_me", "changeme", "change-me", "replace-with", "secret")


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "VaultRAG"
    environment: Literal["development", "test", "production"] = "development"

    database_url: str = Field(
        ...,
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db",
    )
    test_database_url: str | None = Field(
        default=None,
        description="Database used by pytest. The test suite truncates it.",
    )

    jwt_secret_key: SecretStr = Field(..., description="HMAC signing secret (>= 32 chars)")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    cors_origins: list[str] = Field(default_factory=list)
    sql_echo: bool = False
    log_level: str = "INFO"

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _must_be_postgres(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith("postgresql+"):
            raise ValueError(
                "DATABASE_URL must use the PostgreSQL driver, "
                "e.g. postgresql+psycopg://..."
            )
        return value

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        secret = self.jwt_secret_key.get_secret_value()

        # A short symmetric key is brute-forceable offline. Refuse to start.
        if len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters."
            )

        # Never allow the example value to reach a production deployment.
        if self.environment == "production" and secret.lower().startswith(
            INSECURE_SECRET_PREFIXES
        ):
            raise ValueError("JWT_SECRET_KEY is still a placeholder; generate a real secret.")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached Settings instance per process."""
    return Settings()
