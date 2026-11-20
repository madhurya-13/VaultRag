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
from urllib.parse import urlsplit
from sqlalchemy.engine import make_url

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32
INSECURE_SECRET_PREFIXES = ("change_me", "changeme", "change-me", "replace-with", "secret")


def _is_valid_production_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    if (
        parsed.hostname is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}


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
    jwt_issuer: str = Field(default="vaultrag", min_length=1, max_length=200)
    jwt_audience: str = Field(default="vaultrag-api", min_length=1, max_length=200)
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)

    cors_origins: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=300)
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
    max_pdf_pages: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum pages accepted for PDF extraction and ingestion.",
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

    # ------------------------------------------------------------------
    # Milestone 4: permission-aware retrieval and local generation
    # ------------------------------------------------------------------
    rag_top_k_default: int = Field(default=10, ge=1, le=50)
    rag_top_k_max: int = Field(default=50, ge=1, le=50)
    rag_context_top_n: int = Field(default=5, ge=1, le=50)
    rag_min_similarity: float = Field(default=0.20, ge=-1.0, le=1.0)
    rag_max_question_chars: int = Field(default=4000, ge=1, le=20000)
    rag_max_context_tokens: int = Field(default=3000, ge=100, le=100000)
    rag_rerank_enabled: bool = False
    rag_reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        min_length=1,
        max_length=255,
    )

    # ------------------------------------------------------------------
    # Milestone 7: hybrid retrieval, query intelligence, reranking, memory
    # ------------------------------------------------------------------
    rag_search_mode_default: Literal[
        "semantic", "keyword", "hybrid", "graph", "graph_hybrid"
    ] = "hybrid"
    rag_semantic_candidates: int = Field(default=30, ge=1, le=100)
    rag_keyword_candidates: int = Field(default=30, ge=1, le=100)
    rag_rrf_k: int = Field(default=60, ge=1, le=1000)
    rag_hybrid_candidates: int = Field(default=40, ge=1, le=200)
    rag_rerank_candidates: int = Field(default=24, ge=1, le=200)
    rag_rerank_min_score: float | None = Field(
        default=None,
        description=(
            "Optional cross-encoder score floor. Unset keeps reranking as a "
            "pure ordering step because logits are not calibrated similarity."
        ),
    )
    rag_max_chunks_per_document: int = Field(default=3, ge=1, le=20)
    rag_merge_adjacent_chunks: bool = True

    rag_query_understanding_enabled: bool = True
    rag_query_expansion_enabled: bool = True
    rag_query_max_expansions: int = Field(default=2, ge=0, le=3)
    rag_query_decomposition_enabled: bool = True
    rag_query_max_subqueries: int = Field(default=4, ge=1, le=6)
    rag_query_max_chars: int = Field(default=400, ge=50, le=2000)
    rag_query_llm_enabled: bool = Field(
        default=False,
        description=(
            "Optional Ollama-assisted query rewriting. Deterministic rewriting "
            "is the default; generated text is only ever a search string."
        ),
    )

    rag_conversation_history_turns: int = Field(default=6, ge=0, le=20)
    rag_conversation_summary_turns: int = Field(default=12, ge=2, le=50)
    rag_conversation_title_max_chars: int = Field(default=120, ge=10, le=120)

    rag_analytics_enabled: bool = True
    rag_citation_min_coverage: float = Field(default=0.25, ge=0.0, le=1.0)
    rag_evidence_strong_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    rag_evidence_moderate_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    rag_evidence_limited_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # Milestone 8: enterprise knowledge graph
    # ------------------------------------------------------------------
    # Local, deterministic extraction is the default. No paid API is required:
    # rules always run, spaCy is used when the model is installed, and Ollama
    # extraction is opt-in because a generated triple is untrusted input.
    knowledge_graph_enabled: bool = True
    knowledge_graph_extraction_method: Literal[
        "rules", "rules_spacy", "rules_spacy_llm"
    ] = "rules_spacy"
    knowledge_graph_spacy_model: str = Field(
        default="en_core_web_sm", min_length=1, max_length=100
    )
    knowledge_graph_llm_enabled: bool = False
    knowledge_graph_min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    knowledge_graph_auto_merge_threshold: float = Field(
        default=0.95, ge=0.5, le=1.0
    )
    knowledge_graph_review_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    knowledge_graph_max_quote_chars: int = Field(default=600, ge=20, le=2000)
    knowledge_graph_max_aliases_per_node: int = Field(default=16, ge=1, le=64)
    knowledge_graph_max_nodes_per_chunk: int = Field(default=12, ge=1, le=50)
    knowledge_graph_max_edges_per_chunk: int = Field(default=16, ge=0, le=100)

    # Traversal is always bounded: depth, node, edge and evidence caps are
    # enforced in the service layer, never by the caller.
    knowledge_graph_default_depth: int = Field(default=2, ge=1, le=5)
    knowledge_graph_max_depth: int = Field(default=3, ge=1, le=5)
    knowledge_graph_max_nodes: int = Field(default=200, ge=10, le=500)
    knowledge_graph_max_edges: int = Field(default=400, ge=10, le=1000)
    knowledge_graph_max_evidence: int = Field(default=100, ge=1, le=500)
    knowledge_graph_max_seeds: int = Field(default=8, ge=1, le=50)
    knowledge_graph_max_documents_per_query: int = Field(
        default=200, ge=1, le=1000
    )
    knowledge_graph_findings: int = Field(default=6, ge=1, le=20)
    knowledge_graph_context_share: float = Field(default=0.25, ge=0.0, le=1.0)
    knowledge_graph_analytics_enabled: bool = True

    # Background extraction job runtime.
    knowledge_graph_worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    knowledge_graph_job_max_attempts: int = Field(default=3, ge=1, le=10)
    knowledge_graph_job_stale_seconds: int = Field(default=900, ge=30, le=86400)

    # Local Ollama runtime. No paid API key is required.
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        min_length=1,
        max_length=2048,
    )
    ollama_model: str = Field(default="qwen2.5:3b", min_length=1, max_length=255)
    ollama_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    ollama_max_output_tokens: int = Field(default=512, ge=1, le=8192)
    ollama_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ollama_context_window: int = Field(default=4096, ge=512, le=131072)
    ollama_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    ollama_model_cache_seconds: float = Field(default=30.0, ge=0, le=600)

    # ------------------------------------------------------------------
    # Milestone 9: enterprise integrations and synchronization
    # ------------------------------------------------------------------
    # Integrations can be left switched off on a deployment that does not use
    # them; the routes then refuse with INTEGRATION_NOT_CONFIGURED.
    integrations_enabled: bool = True
    integration_token_encryption_key: SecretStr | None = Field(
        default=None,
        description=(
            "Base64url-encoded 32-byte AES-256-GCM key protecting stored "
            "provider credentials. Required before any provider is connected."
        ),
    )
    # Exact public API origin used to build OAuth redirect URIs, e.g.
    # https://vaultrag.example.com. Never derived from a request Host header.
    integration_oauth_redirect_base: str | None = Field(
        default=None,
        description="Public API origin for provider OAuth redirect URIs.",
    )
    integration_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    integration_http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    integration_http_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    # Hard ceiling for any provider download, enforced by the connector while
    # streaming rather than by trusting a Content-Length header.
    integration_max_download_bytes: int = Field(
        default=25 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024
    )
    integration_webhook_max_body_bytes: int = Field(
        default=64 * 1024, ge=1024, le=1024 * 1024
    )
    integration_sync_worker_poll_seconds: float = Field(default=5.0, gt=0, le=300)
    integration_sync_max_attempts: int = Field(default=3, ge=1, le=10)
    integration_sync_backoff_base_seconds: int = Field(default=30, ge=1, le=3600)
    # A job left PROCESSING for longer than this is reclaimable by the worker.
    integration_sync_job_stale_seconds: int = Field(default=1800, ge=60, le=86400)
    integration_sync_default_interval_minutes: int = Field(
        default=60, ge=1, le=10080
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

        if self.environment == "production":
            if any("*" in host for host in self.allowed_hosts):
                raise ValueError("ALLOWED_HOSTS must not contain a wildcard in production.")
            if not self.allowed_hosts:
                raise ValueError("ALLOWED_HOSTS must be configured in production.")
            if not self.cors_origins or "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS must list explicit production origins.")
            if any(not _is_valid_production_origin(origin) for origin in self.cors_origins):
                raise ValueError(
                    "Production CORS_ORIGINS must be valid HTTPS origins except local demonstration hosts."
                )
            if self.sql_echo:
                raise ValueError("SQL_ECHO must be disabled in production.")
            if make_url(self.database_url).username != "vaultrag_app":
                raise ValueError(
                    "Production DATABASE_URL must use the least-privilege vaultrag_app role."
                )

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

    @model_validator(mode="after")
    def _validate_rag_settings(self) -> "Settings":
        if self.rag_top_k_default > self.rag_top_k_max:
            raise ValueError("RAG_TOP_K_DEFAULT must not exceed RAG_TOP_K_MAX.")
        if self.rag_context_top_n > self.rag_top_k_max:
            raise ValueError("RAG_CONTEXT_TOP_N must not exceed RAG_TOP_K_MAX.")
        if not self.ollama_base_url.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://.")
        if self.rag_context_top_n > self.rag_rerank_candidates:
            raise ValueError(
                "RAG_CONTEXT_TOP_N must not exceed RAG_RERANK_CANDIDATES."
            )
        if self.rag_query_max_expansions > 3:
            raise ValueError("RAG_QUERY_MAX_EXPANSIONS must not exceed 3.")
        if self.rag_conversation_summary_turns < self.rag_conversation_history_turns:
            raise ValueError(
                "RAG_CONVERSATION_SUMMARY_TURNS must be at least "
                "RAG_CONVERSATION_HISTORY_TURNS."
            )
        if not (
            self.rag_evidence_strong_threshold
            > self.rag_evidence_moderate_threshold
            > self.rag_evidence_limited_threshold
            >= 0.0
        ):
            raise ValueError(
                "Evidence thresholds must satisfy STRONG > MODERATE > LIMITED >= 0."
            )
        return self

    @model_validator(mode="after")
    def _validate_knowledge_graph_settings(self) -> "Settings":
        if self.knowledge_graph_default_depth > self.knowledge_graph_max_depth:
            raise ValueError(
                "KNOWLEDGE_GRAPH_DEFAULT_DEPTH must not exceed "
                "KNOWLEDGE_GRAPH_MAX_DEPTH."
            )
        if not (
            self.knowledge_graph_review_threshold
            <= self.knowledge_graph_auto_merge_threshold
        ):
            raise ValueError(
                "KNOWLEDGE_GRAPH_REVIEW_THRESHOLD must not exceed "
                "KNOWLEDGE_GRAPH_AUTO_MERGE_THRESHOLD."
            )
        if self.knowledge_graph_context_share > 0.6:
            raise ValueError(
                "KNOWLEDGE_GRAPH_CONTEXT_SHARE must not exceed 0.6 so document "
                "evidence always keeps the majority of the context budget."
            )
        if self.ollama_max_output_tokens < 1:
            raise ValueError("OLLAMA_MAX_OUTPUT_TOKENS must be positive.")
        return self

    @model_validator(mode="after")
    def _validate_integration_settings(self) -> "Settings":
        # The key is validated when present and read lazily at connect time, so
        # a deployment that does not use integrations still boots. A malformed
        # key always fails startup rather than failing mid-OAuth.
        configured = self.integration_token_encryption_key
        if configured is not None:
            raw = configured.get_secret_value().strip()
            if raw:
                import base64
                import binascii

                padded = raw.encode("ascii", "ignore") + b"=" * (-len(raw) % 4)
                try:
                    decoded = base64.urlsafe_b64decode(padded)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(
                        "INTEGRATION_TOKEN_ENCRYPTION_KEY must be base64url-encoded."
                    ) from exc
                if len(decoded) != 32:
                    raise ValueError(
                        "INTEGRATION_TOKEN_ENCRYPTION_KEY must decode to exactly "
                        "32 bytes (AES-256)."
                    )

        redirect_base = self.integration_oauth_redirect_base
        if redirect_base is not None:
            parsed = urlsplit(redirect_base)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "INTEGRATION_OAUTH_REDIRECT_BASE must be an http(s) origin "
                    "without a path, query or fragment."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one cached Settings instance per process."""
    return Settings()
