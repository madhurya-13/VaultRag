"""ORM models for Milestone 9: integrations, external documents, versions.

Tenancy rules mirror the rest of the schema and are enforced twice:

* SQLAlchemy metadata declares ``tenant_id`` on every table plus composite
  foreign keys that include it, so a cross-tenant reference is impossible.
* The migration enables ``FORCE ROW LEVEL SECURITY`` with the shared
  ``security.current_tenant_id()`` policy, so even a buggy query cannot read
  another tenant's rows.

The sync layer is the only writer of these tables. Routes never construct them
from request payloads without the authenticated tenant id.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.security_constants import (
    DOCUMENT_VERSION_STATUS_PENDING,
    EVENT_STATUS_PENDING,
    EXTERNAL_PERMISSION_STATE_UNRESOLVED,
    EXTERNAL_SYNC_STATUS_PENDING,
    INTEGRATION_STATUS_DISCONNECTED,
    PERMISSION_SYNC_MODE_VAULTRAG,
    SYNC_STATUS_PENDING,
    SYNC_TYPE_MANUAL,
)

# CHECK constraint vocabulary. Literal, ordered strings (not frozensets) so the
# model metadata and the migration produce byte-identical DDL.
_PROVIDERS_SQL = "'local_storage', 'google_drive', 'sharepoint'"
_INTEGRATION_STATUS_SQL = "'CONNECTED', 'DISCONNECTED', 'REAUTH_REQUIRED', 'ERROR'"
_PERMISSION_SYNC_MODES_SQL = "'VAULTRAG_MANAGED', 'EXTERNAL_MIRROR', 'ADMIN_MAPPED'"
_SYNC_STATUS_SQL = (
    "'PENDING', 'DISCOVERING', 'DOWNLOADING', 'PROCESSING', 'INDEXING', "
    "'COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED', 'CANCELLED'"
)
_SYNC_TYPES_SQL = "'FULL', 'INCREMENTAL', 'MANUAL', 'WEBHOOK'"
_EXTERNAL_SYNC_STATUS_SQL = (
    "'PENDING', 'SYNCED', 'DUPLICATE_CONTENT', 'FAILED', 'DELETED'"
)
_PERMISSION_STATE_SQL = "'RESOLVED', 'UNRESOLVED', 'NOT_APPLICABLE'"
_VERSION_STATUS_SQL = "'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'"
_EVENT_STATUS_SQL = "'PENDING', 'PROCESSED', 'DUPLICATE', 'FAILED', 'IGNORED'"
_PRINCIPAL_TYPES_SQL = "'USER', 'GROUP', 'ROLE', 'DOMAIN', 'ANYONE'"
_REPOSITORY_KINDS_SQL = "'DRIVE', 'FOLDER', 'LIBRARY', 'SITE', 'LOCAL'"
_SYNC_MODES_SQL = "'FULL', 'INCREMENTAL'"
_PERMISSION_ORIGINS_SQL = "'MANUAL', 'EXTERNAL'"
_ACTIVE_SYNC_STATUS_SQL = (
    "sync_status IN ('PENDING', 'DISCOVERING', 'DOWNLOADING', 'PROCESSING', "
    "'INDEXING')"
)
_CHECKSUM_SQL = "^[0-9a-f]{64}$"


class Integration(Base):
    """A tenant's connection to one external knowledge source."""

    __tablename__ = "integrations"

    integration_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    integration_name: Mapped[str] = mapped_column(String(120), nullable=False)
    connection_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=INTEGRATION_STATUS_DISCONNECTED,
        server_default=text(f"'{INTEGRATION_STATUS_DISCONNECTED}'"),
    )
    permission_sync_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PERMISSION_SYNC_MODE_VAULTRAG,
        server_default=text(f"'{PERMISSION_SYNC_MODE_VAULTRAG}'"),
    )
    sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default=text("60")
    )
    next_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Non-secret provider settings only (site id, drive id, folder id, ...).
    # Credentials live encrypted in integration_credentials and never here.
    configuration: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            name="uq_integrations_tenant_integration",
        ),
        UniqueConstraint(
            "tenant_id", "integration_name", name="uq_integrations_tenant_name"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integrations_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})", name="ck_integrations_provider"
        ),
        CheckConstraint(
            f"connection_status IN ({_INTEGRATION_STATUS_SQL})",
            name="ck_integrations_status",
        ),
        CheckConstraint(
            f"permission_sync_mode IN ({_PERMISSION_SYNC_MODES_SQL})",
            name="ck_integrations_permission_sync_mode",
        ),
        CheckConstraint(
            "sync_interval_minutes BETWEEN 1 AND 10080",
            name="ck_integrations_interval",
        ),
        CheckConstraint(
            "length(trim(integration_name)) > 0 AND length(integration_name) <= 120",
            name="ck_integrations_name",
        ),
        Index("ix_integrations_tenant_provider", "tenant_id", "provider"),
        Index("ix_integrations_tenant_status", "tenant_id", "connection_status"),
        Index(
            "ix_integrations_tenant_next_sync",
            "tenant_id",
            "next_sync_at",
            postgresql_where=text("sync_enabled"),
        ),
    )


class IntegrationCredential(Base):
    """Encrypted provider credentials. One active row per provider per integration."""

    __tablename__ = "integration_credentials"

    credential_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    # Space-separated granted scopes, for display and drift detection only.
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "credential_id",
            name="uq_integration_credentials_tenant_credential",
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "provider",
            name="uq_integration_credentials_tenant_integration_provider",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_credentials_tenant_integration",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})",
            name="ck_integration_credentials_provider",
        ),
        CheckConstraint("key_version >= 1", name="ck_integration_credentials_version"),
        CheckConstraint(
            "octet_length(nonce) = 12", name="ck_integration_credentials_nonce"
        ),
        CheckConstraint(
            "octet_length(ciphertext) > 0",
            name="ck_integration_credentials_ciphertext",
        ),
        Index(
            "ix_integration_credentials_tenant_integration",
            "tenant_id",
            "integration_id",
        ),
    )


class IntegrationRepository(Base):
    """A provider container the tenant explicitly selected for synchronization."""

    __tablename__ = "integration_repositories"

    repository_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    external_repository_id: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    repository_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="FOLDER", server_default=text("'FOLDER'")
    )
    is_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sync_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="INCREMENTAL", server_default=text("'INCREMENTAL'")
    )
    # Opaque provider delta token (Drive pageToken, Graph delta link). Bounded
    # by the provider; never trusted as authorization.
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "repository_id",
            name="uq_integration_repositories_tenant_repository",
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_repository_id",
            name="uq_integration_repositories_tenant_external",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_repositories_tenant_integration",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"repository_kind IN ({_REPOSITORY_KINDS_SQL})",
            name="ck_integration_repositories_kind",
        ),
        CheckConstraint(
            f"sync_mode IN ({_SYNC_MODES_SQL})",
            name="ck_integration_repositories_sync_mode",
        ),
        CheckConstraint(
            "length(trim(name)) > 0 AND length(name) <= 255",
            name="ck_integration_repositories_name",
        ),
        Index(
            "ix_integration_repositories_tenant_selected",
            "tenant_id",
            "integration_id",
            "is_selected",
        ),
    )


class ExternalDocument(Base):
    """Source identity of an ingested provider document.

    VaultRAG's own ``documents`` row stays the single canonical record used by
    the authorization engine, retrieval, and the knowledge graph. This table
    adds provenance and the external cursor without duplicating any of them,
    which is what prevents duplicate chunks, embeddings and graph nodes.
    """

    __tablename__ = "external_documents"

    external_document_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    internal_document_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_repository_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_version_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=EXTERNAL_SYNC_STATUS_PENDING,
        server_default=text(f"'{EXTERNAL_SYNC_STATUS_PENDING}'"),
    )
    permission_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=EXTERNAL_PERMISSION_STATE_UNRESOLVED,
        server_default=text(f"'{EXTERNAL_PERMISSION_STATE_UNRESOLVED}'"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "external_document_id",
            name="uq_external_documents_tenant_external_document",
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_file_id",
            name="uq_external_documents_tenant_integration_file",
        ),
        UniqueConstraint(
            "tenant_id",
            "internal_document_id",
            name="uq_external_documents_tenant_internal_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_external_documents_tenant_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "internal_document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_external_documents_tenant_document",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})", name="ck_external_documents_provider"
        ),
        CheckConstraint(
            f"sync_status IN ({_EXTERNAL_SYNC_STATUS_SQL})",
            name="ck_external_documents_sync_status",
        ),
        CheckConstraint(
            f"permission_state IN ({_PERMISSION_STATE_SQL})",
            name="ck_external_documents_permission_state",
        ),
        CheckConstraint(
            f"content_checksum IS NULL OR content_checksum ~ '{_CHECKSUM_SQL}'",
            name="ck_external_documents_checksum",
        ),
        CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_external_documents_file_size",
        ),
        Index(
            "ix_external_documents_tenant_integration_status",
            "tenant_id",
            "integration_id",
            "sync_status",
        ),
        Index(
            "ix_external_documents_tenant_document",
            "tenant_id",
            "internal_document_id",
        ),
    )


class DocumentVersion(Base):
    """An immutable stored artifact for one VaultRAG document revision.

    Only metadata is versioned. Chunks for the live version belong to the live
    ``documents`` row; older versions keep their bytes and checksum so a
    rollback can re-ingest exactly what was previously indexed. This keeps
    every existing retrieval query (and its RLS policy) unchanged.
    """

    __tablename__ = "document_versions"

    document_version_id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    external_version_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingestion_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=DOCUMENT_VERSION_STATUS_PENDING,
        server_default=text(f"'{DOCUMENT_VERSION_STATUS_PENDING}'"),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "document_version_id",
            name="uq_document_versions_tenant_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_number",
            name="uq_document_versions_tenant_document_number",
        ),
        Index(
            "uq_document_versions_current",
            "tenant_id",
            "document_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_document_versions_tenant_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_versions_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version_number >= 1", name="ck_document_versions_number"),
        CheckConstraint("file_size >= 0", name="ck_document_versions_size"),
        CheckConstraint(
            f"content_checksum ~ '{_CHECKSUM_SQL}'",
            name="ck_document_versions_checksum",
        ),
        CheckConstraint(
            f"ingestion_status IN ({_VERSION_STATUS_SQL})",
            name="ck_document_versions_status",
        ),
        CheckConstraint(
            "length(trim(storage_path)) > 0", name="ck_document_versions_storage_path"
        ),
        Index(
            "ix_document_versions_tenant_document_created",
            "tenant_id",
            "document_id",
            "created_at",
        ),
    )


class IntegrationSyncJob(Base):
    """One synchronization run. Counters are written from real work only."""

    __tablename__ = "integration_sync_jobs"

    sync_job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    sync_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SYNC_TYPE_MANUAL,
        server_default=text(f"'{SYNC_TYPE_MANUAL}'"),
    )
    sync_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=SYNC_STATUS_PENDING,
        server_default=text(f"'{SYNC_STATUS_PENDING}'"),
    )
    files_discovered: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    files_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    files_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    versions_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    documents_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "sync_job_id", name="uq_integration_sync_jobs_tenant_job"
        ),
        Index(
            "uq_integration_sync_jobs_active",
            "tenant_id",
            "integration_id",
            unique=True,
            postgresql_where=text(_ACTIVE_SYNC_STATUS_SQL),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_sync_jobs_tenant_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_sync_jobs_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"sync_type IN ({_SYNC_TYPES_SQL})", name="ck_integration_sync_jobs_type"
        ),
        CheckConstraint(
            f"sync_status IN ({_SYNC_STATUS_SQL})",
            name="ck_integration_sync_jobs_status",
        ),
        CheckConstraint(
            "files_discovered >= 0 AND files_processed >= 0 AND files_failed >= 0 "
            "AND versions_created >= 0 AND documents_deleted >= 0",
            name="ck_integration_sync_jobs_counters",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10",
            name="ck_integration_sync_jobs_attempts",
        ),
        CheckConstraint(
            "error_summary IS NULL OR length(error_summary) <= 1000",
            name="ck_integration_sync_jobs_error_summary",
        ),
        Index(
            "ix_integration_sync_jobs_tenant_integration_created",
            "tenant_id",
            "integration_id",
            "created_at",
        ),
        Index(
            "ix_integration_sync_jobs_tenant_status_next",
            "tenant_id",
            "sync_status",
            "next_attempt_at",
        ),
    )


class IntegrationSyncEvent(Base):
    """A de-duplicated inbound webhook notification."""

    __tablename__ = "integration_sync_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=EVENT_STATUS_PENDING,
        server_default=text(f"'{EVENT_STATUS_PENDING}'"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_id", name="uq_integration_sync_events_tenant_event"
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_event_id",
            name="uq_integration_sync_events_tenant_external",
        ),
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "processing_status",
            name="uq_integration_sync_events_tenant_status",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_sync_events_tenant_integration",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"processing_status IN ({_EVENT_STATUS_SQL})",
            name="ck_integration_sync_events_status",
        ),
        CheckConstraint(
            f"payload_digest IS NULL OR payload_digest ~ '{_CHECKSUM_SQL}'",
            name="ck_integration_sync_events_digest",
        ),
        Index(
            "ix_integration_sync_events_tenant_integration_received",
            "tenant_id",
            "integration_id",
            "received_at",
        ),
    )


class IntegrationIdentityMapping(Base):
    """Maps an external principal onto an internal user or role.

    Unmapped principals produce no grant. Deny-by-default is the whole point:
    a mapping must be created deliberately by an administrator.
    """

    __tablename__ = "integration_identity_mappings"

    mapping_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    external_principal_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    role_id: Mapped[UUID | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "mapping_id",
            name="uq_integration_identity_mappings_tenant_mapping",
        ),
        UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_principal_type",
            "external_principal_id",
            name="uq_integration_identity_mappings_tenant_principal",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_identity_mappings_tenant_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_identity_mappings_tenant_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.role_id"],
            name="fk_integration_identity_mappings_tenant_role",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_identity_mappings_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "num_nonnulls(user_id, role_id) = 1",
            name="ck_integration_identity_mappings_one_target",
        ),
        CheckConstraint(
            f"external_principal_type IN ({_PRINCIPAL_TYPES_SQL})",
            name="ck_integration_identity_mappings_principal_type",
        ),
        Index(
            "ix_integration_identity_mappings_tenant_active",
            "tenant_id",
            "integration_id",
            "is_active",
        ),
    )


class IntegrationOAuthState(Base):
    """Single-use OAuth authorization state.

    The value sent to the provider is ``<state_id>.<tenant_id>.<expiry>.<mac>``
    signed with the deployment key, so the unauthenticated callback can derive
    the tenant without trusting any request parameter, then load this row under
    RLS. The PKCE verifier is stored encrypted and is never returned to the
    browser.
    """

    __tablename__ = "integration_oauth_states"

    state_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    integration_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    verifier_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verifier_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "state_id", name="uq_integration_oauth_states_tenant_state"
        ),
        UniqueConstraint("state_hash", name="uq_integration_oauth_states_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_oauth_states_tenant_integration",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_oauth_states_tenant_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})", name="ck_integration_oauth_states_provider"
        ),
        CheckConstraint(
            f"state_hash ~ '{_CHECKSUM_SQL}'",
            name="ck_integration_oauth_states_hash",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_integration_oauth_states_expiry"
        ),
        CheckConstraint(
            "octet_length(verifier_nonce) = 12",
            name="ck_integration_oauth_states_nonce",
        ),
        Index(
            "ix_integration_oauth_states_tenant_integration",
            "tenant_id",
            "integration_id",
        ),
    )
