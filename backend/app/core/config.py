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
from pathlib import Path
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
    migration_database_url: str | None = Field(
        default=None,
        description=(
            "DDL-capable URL used by Alembic. Falls back to DATABASE_URL for "
            "local development; production should use a separate migrator role."
        ),
    )
    test_migration_database_url: str | None = Field(
        default=None,
        description="DDL-capable test database URL used by pytest's Alembic setup.",
    )

    jwt_secret_key: SecretStr = Field(..., description="HMAC signing secret (>= 32 chars)")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    cors_origins: list[str] = Field(default_factory=list)
    sql_echo: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Milestone 3: private document storage and ingestion
    # ------------------------------------------------------------------
    document_storage_dir: Path = Field(
        default=Path("private_documents"),
        description="Private local storage root. Never serve this directory publicly.",
    )
    max_upload_size_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        description="Maximum accepted PDF upload size in bytes.",
    )
    embedding_model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        min_length=1,
        max_length=255,
    )
    embedding_model_version: str = Field(default="main", min_length=1, max_length=100)
    embedding_dimension: int = Field(default=384, ge=1, le=8192)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_device: str = Field(default="cpu", min_length=1, max_length=32)
    chunk_size_tokens: int = Field(default=500, ge=1, le=4096)
    chunk_overlap_tokens: int = Field(default=100, ge=0, le=4096)
    chunk_min_tokens: int = Field(default=50, ge=1, le=4096)
    chunk_max_tokens: int = Field(default=700, ge=1, le=4096)
    pdf_min_extractable_chars: int = Field(
        default=50,
        ge=1,
        description="Below this many non-whitespace characters, a PDF is treated as image-only.",
    )

    @field_validator(
        "database_url",
        "test_database_url",
        "migration_database_url",
        "test_migration_database_url",
    )
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

    @model_validator(mode="after")
    def _validate_ingestion_settings(self) -> "Settings":
        # The Milestone 3 vector column is fixed at 384 dimensions. A model
        # change with a different dimension requires a schema migration and a
        # versioned re-embedding plan, not a silent runtime switch.
        if self.embedding_dimension != 384:
            raise ValueError(
                "EMBEDDING_DIMENSION must be 384 in Milestone 3 because "
                "document_chunks.embedding is VECTOR(384)."
            )
        if not (
            self.chunk_min_tokens
            <= self.chunk_size_tokens
            <= self.chunk_max_tokens
        ):
            raise ValueError(
                "CHUNK_MIN_TOKENS <= CHUNK_SIZE_TOKENS <= CHUNK_MAX_TOKENS is required."
            )
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError(
                "CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_SIZE_TOKENS."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached Settings instance per process."""
    return Settings()
