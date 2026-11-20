"""Milestone 9: enterprise integrations, external documents and versions.

Revision ID: d9a3f1b7c2e4
Revises: c8f1d2e3a4b5
Create Date: 2026-11-20

What this migration adds
------------------------
1. Nine tenant-scoped tables for the integration framework: connections,
   encrypted credentials, selected repositories, external-document provenance,
   immutable document versions, sync jobs, de-duplicated webhook events,
   identity mappings and single-use OAuth state.
2. ``document_permissions`` gains ``managed_by_integration_id`` and
   ``permission_origin`` so a mirrored external ACL can be revoked without ever
   touching a manually granted permission.
3. ``GRANT DELETE ON document_chunks`` for the runtime role. Replacing a
   document's live version must delete its chunks before re-ingesting; the grant
   is limited to chunks. ``documents`` still has no DELETE grant, so a document
   or its audit trail cannot be destroyed by the application role.

Why versions are stored as files + metadata
-------------------------------------------
Chunks, embeddings, evidence and graph nodes stay scoped to the *live* document
row. An old version keeps its stored bytes, checksum and storage path so a
rollback can re-ingest exactly those bytes through the existing ingestion
pipeline. That avoids adding a ``version_id`` predicate to every retrieval,
authorization and graph query (and its RLS policy) while still never
overwriting history. See ``docs/integrations/DOCUMENT_VERSIONING.md``.

Every new table carries ``tenant_id``, ``ENABLE`` + ``FORCE ROW LEVEL SECURITY``
and the same ``security.current_tenant_id()`` policy as the rest of the schema.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9a3f1b7c2e4"
down_revision: Union[str, None] = "c8f1d2e3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TENANT_TABLES = (
    "integrations",
    "integration_credentials",
    "integration_repositories",
    "external_documents",
    "document_versions",
    "integration_sync_jobs",
    "integration_sync_events",
    "integration_identity_mappings",
    "integration_oauth_states",
)

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
_ACTIVE_SYNC_STATUS_SQL = (
    "sync_status IN ('PENDING', 'DISCOVERING', 'DOWNLOADING', 'PROCESSING', "
    "'INDEXING')"
)
_CHECKSUM_SQL = "^[0-9a-f]{64}$"


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING (
                security.current_tenant_id() IS NOT NULL
                AND tenant_id = security.current_tenant_id()
            )
            WITH CHECK (
                security.current_tenant_id() IS NOT NULL
                AND tenant_id = security.current_tenant_id()
            )
            """
        )
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Integrations
    # ------------------------------------------------------------------
    op.create_table(
        "integrations",
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("integration_name", sa.String(length=120), nullable=False),
        sa.Column(
            "connection_status",
            sa.String(length=24),
            server_default=sa.text("'DISCONNECTED'"),
            nullable=False,
        ),
        sa.Column(
            "permission_sync_mode",
            sa.String(length=32),
            server_default=sa.text("'VAULTRAG_MANAGED'"),
            nullable=False,
        ),
        sa.Column(
            "sync_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "sync_interval_minutes",
            sa.Integer(),
            server_default=sa.text("60"),
            nullable=False,
        ),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_integrations_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integrations_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("integration_id", name=op.f("pk_integrations")),
        sa.UniqueConstraint(
            "tenant_id", "integration_id", name="uq_integrations_tenant_integration"
        ),
        sa.UniqueConstraint(
            "tenant_id", "integration_name", name="uq_integrations_tenant_name"
        ),
        sa.CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})", name="ck_integrations_provider"
        ),
        sa.CheckConstraint(
            f"connection_status IN ({_INTEGRATION_STATUS_SQL})",
            name="ck_integrations_status",
        ),
        sa.CheckConstraint(
            f"permission_sync_mode IN ({_PERMISSION_SYNC_MODES_SQL})",
            name="ck_integrations_permission_sync_mode",
        ),
        sa.CheckConstraint(
            "sync_interval_minutes BETWEEN 1 AND 10080",
            name="ck_integrations_interval",
        ),
        sa.CheckConstraint(
            "length(trim(integration_name)) > 0 AND length(integration_name) <= 120",
            name="ck_integrations_name",
        ),
    )
    op.create_index(
        "ix_integrations_tenant_provider",
        "integrations",
        ["tenant_id", "provider"],
    )
    op.create_index(
        "ix_integrations_tenant_status",
        "integrations",
        ["tenant_id", "connection_status"],
    )
    op.create_index(
        "ix_integrations_tenant_next_sync",
        "integrations",
        ["tenant_id", "next_sync_at"],
        postgresql_where=sa.text("sync_enabled"),
    )

    # ------------------------------------------------------------------
    # 2. Encrypted credentials
    # ------------------------------------------------------------------
    op.create_table(
        "integration_credentials",
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column(
            "key_version",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_credentials_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "credential_id", name=op.f("pk_integration_credentials")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "credential_id",
            name="uq_integration_credentials_tenant_credential",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "integration_id",
            "provider",
            name="uq_integration_credentials_tenant_integration_provider",
        ),
        sa.CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})",
            name="ck_integration_credentials_provider",
        ),
        sa.CheckConstraint(
            "key_version >= 1", name="ck_integration_credentials_version"
        ),
        sa.CheckConstraint(
            "octet_length(nonce) = 12", name="ck_integration_credentials_nonce"
        ),
        sa.CheckConstraint(
            "octet_length(ciphertext) > 0",
            name="ck_integration_credentials_ciphertext",
        ),
    )
    op.create_index(
        "ix_integration_credentials_tenant_integration",
        "integration_credentials",
        ["tenant_id", "integration_id"],
    )

    # ------------------------------------------------------------------
    # 3. Selected repositories
    # ------------------------------------------------------------------
    op.create_table(
        "integration_repositories",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("external_repository_id", sa.String(length=512), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "repository_kind",
            sa.String(length=32),
            server_default=sa.text("'FOLDER'"),
            nullable=False,
        ),
        sa.Column(
            "is_selected",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "sync_mode",
            sa.String(length=24),
            server_default=sa.text("'INCREMENTAL'"),
            nullable=False,
        ),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("cursor_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_repositories_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "repository_id", name=op.f("pk_integration_repositories")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "repository_id",
            name="uq_integration_repositories_tenant_repository",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_repository_id",
            name="uq_integration_repositories_tenant_external",
        ),
        sa.CheckConstraint(
            f"repository_kind IN ({_REPOSITORY_KINDS_SQL})",
            name="ck_integration_repositories_kind",
        ),
        sa.CheckConstraint(
            f"sync_mode IN ({_SYNC_MODES_SQL})",
            name="ck_integration_repositories_sync_mode",
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0 AND length(name) <= 255",
            name="ck_integration_repositories_name",
        ),
    )
    op.create_index(
        "ix_integration_repositories_tenant_selected",
        "integration_repositories",
        ["tenant_id", "integration_id", "is_selected"],
    )

    # ------------------------------------------------------------------
    # 4. External document provenance
    # ------------------------------------------------------------------
    op.create_table(
        "external_documents",
        sa.Column("external_document_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("internal_document_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_file_id", sa.String(length=512), nullable=False),
        sa.Column("external_repository_id", sa.String(length=512), nullable=False),
        sa.Column("external_version_id", sa.String(length=512), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_checksum", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("last_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=24),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "permission_state",
            sa.String(length=24),
            server_default=sa.text("'UNRESOLVED'"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_external_documents_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "internal_document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_external_documents_tenant_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "external_document_id", name=op.f("pk_external_documents")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "external_document_id",
            name="uq_external_documents_tenant_external_document",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_file_id",
            name="uq_external_documents_tenant_integration_file",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "internal_document_id",
            name="uq_external_documents_tenant_internal_document",
        ),
        sa.CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})", name="ck_external_documents_provider"
        ),
        sa.CheckConstraint(
            f"sync_status IN ({_EXTERNAL_SYNC_STATUS_SQL})",
            name="ck_external_documents_sync_status",
        ),
        sa.CheckConstraint(
            f"permission_state IN ({_PERMISSION_STATE_SQL})",
            name="ck_external_documents_permission_state",
        ),
        sa.CheckConstraint(
            f"content_checksum IS NULL OR content_checksum ~ '{_CHECKSUM_SQL}'",
            name="ck_external_documents_checksum",
        ),
        sa.CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_external_documents_file_size",
        ),
    )
    op.create_index(
        "ix_external_documents_tenant_integration_status",
        "external_documents",
        ["tenant_id", "integration_id", "sync_status"],
    )
    op.create_index(
        "ix_external_documents_tenant_document",
        "external_documents",
        ["tenant_id", "internal_document_id"],
    )

    # ------------------------------------------------------------------
    # 5. Immutable document versions
    # ------------------------------------------------------------------
    op.create_table(
        "document_versions",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("external_version_id", sa.String(length=512), nullable=True),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingestion_status",
            sa.String(length=24),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_document_versions_tenant_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_document_versions_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "document_version_id", name=op.f("pk_document_versions")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_version_id",
            name="uq_document_versions_tenant_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "document_id",
            "version_number",
            name="uq_document_versions_tenant_document_number",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_versions_number"),
        sa.CheckConstraint("file_size >= 0", name="ck_document_versions_size"),
        sa.CheckConstraint(
            f"content_checksum ~ '{_CHECKSUM_SQL}'",
            name="ck_document_versions_checksum",
        ),
        sa.CheckConstraint(
            f"ingestion_status IN ({_VERSION_STATUS_SQL})",
            name="ck_document_versions_status",
        ),
        sa.CheckConstraint(
            "length(trim(storage_path)) > 0",
            name="ck_document_versions_storage_path",
        ),
    )
    op.create_index(
        "uq_document_versions_current",
        "document_versions",
        ["tenant_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_document_versions_tenant_document_created",
        "document_versions",
        ["tenant_id", "document_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 6. Synchronization jobs
    # ------------------------------------------------------------------
    op.create_table(
        "integration_sync_jobs",
        sa.Column("sync_job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column(
            "sync_type",
            sa.String(length=16),
            server_default=sa.text("'MANUAL'"),
            nullable=False,
        ),
        sa.Column(
            "sync_status",
            sa.String(length=24),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "files_discovered",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "files_processed", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "files_failed", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "versions_created",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "documents_deleted",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_sync_jobs_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_sync_jobs_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "sync_job_id", name=op.f("pk_integration_sync_jobs")
        ),
        sa.UniqueConstraint(
            "tenant_id", "sync_job_id", name="uq_integration_sync_jobs_tenant_job"
        ),
        sa.CheckConstraint(
            f"sync_type IN ({_SYNC_TYPES_SQL})", name="ck_integration_sync_jobs_type"
        ),
        sa.CheckConstraint(
            f"sync_status IN ({_SYNC_STATUS_SQL})",
            name="ck_integration_sync_jobs_status",
        ),
        sa.CheckConstraint(
            "files_discovered >= 0 AND files_processed >= 0 AND files_failed >= 0 "
            "AND versions_created >= 0 AND documents_deleted >= 0",
            name="ck_integration_sync_jobs_counters",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10",
            name="ck_integration_sync_jobs_attempts",
        ),
        sa.CheckConstraint(
            "error_summary IS NULL OR length(error_summary) <= 1000",
            name="ck_integration_sync_jobs_error_summary",
        ),
    )
    op.create_index(
        "uq_integration_sync_jobs_active",
        "integration_sync_jobs",
        ["tenant_id", "integration_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_SYNC_STATUS_SQL),
    )
    op.create_index(
        "ix_integration_sync_jobs_tenant_integration_created",
        "integration_sync_jobs",
        ["tenant_id", "integration_id", "created_at"],
    )
    op.create_index(
        "ix_integration_sync_jobs_tenant_status_next",
        "integration_sync_jobs",
        ["tenant_id", "sync_status", "next_attempt_at"],
    )

    # ------------------------------------------------------------------
    # 7. De-duplicated webhook events
    # ------------------------------------------------------------------
    op.create_table(
        "integration_sync_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=24),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_sync_events_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_integration_sync_events")),
        sa.UniqueConstraint(
            "tenant_id", "event_id", name="uq_integration_sync_events_tenant_event"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_event_id",
            name="uq_integration_sync_events_tenant_external",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "processing_status",
            name="uq_integration_sync_events_tenant_status",
        ),
        sa.CheckConstraint(
            f"processing_status IN ({_EVENT_STATUS_SQL})",
            name="ck_integration_sync_events_status",
        ),
        sa.CheckConstraint(
            f"payload_digest IS NULL OR payload_digest ~ '{_CHECKSUM_SQL}'",
            name="ck_integration_sync_events_digest",
        ),
    )
    op.create_index(
        "ix_integration_sync_events_tenant_integration_received",
        "integration_sync_events",
        ["tenant_id", "integration_id", "received_at"],
    )

    # ------------------------------------------------------------------
    # 8. Identity mappings
    # ------------------------------------------------------------------
    op.create_table(
        "integration_identity_mappings",
        sa.Column("mapping_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("external_principal_id", sa.String(length=512), nullable=False),
        sa.Column("external_principal_type", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_identity_mappings_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_identity_mappings_tenant_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.role_id"],
            name="fk_integration_identity_mappings_tenant_role",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_identity_mappings_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "mapping_id", name=op.f("pk_integration_identity_mappings")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "mapping_id",
            name="uq_integration_identity_mappings_tenant_mapping",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "integration_id",
            "external_principal_type",
            "external_principal_id",
            name="uq_integration_identity_mappings_tenant_principal",
        ),
        sa.CheckConstraint(
            "num_nonnulls(user_id, role_id) = 1",
            name="ck_integration_identity_mappings_one_target",
        ),
        sa.CheckConstraint(
            f"external_principal_type IN ({_PRINCIPAL_TYPES_SQL})",
            name="ck_integration_identity_mappings_principal_type",
        ),
    )
    op.create_index(
        "ix_integration_identity_mappings_tenant_active",
        "integration_identity_mappings",
        ["tenant_id", "integration_id", "is_active"],
    )

    # ------------------------------------------------------------------
    # 9. Single-use OAuth state
    # ------------------------------------------------------------------
    op.create_table(
        "integration_oauth_states",
        sa.Column("state_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("verifier_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("verifier_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "integration_id"],
            ["integrations.tenant_id", "integrations.integration_id"],
            name="fk_integration_oauth_states_tenant_integration",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_integration_oauth_states_tenant_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("state_id", name=op.f("pk_integration_oauth_states")),
        sa.UniqueConstraint(
            "tenant_id", "state_id", name="uq_integration_oauth_states_tenant_state"
        ),
        sa.UniqueConstraint("state_hash", name="uq_integration_oauth_states_hash"),
        sa.CheckConstraint(
            f"provider IN ({_PROVIDERS_SQL})",
            name="ck_integration_oauth_states_provider",
        ),
        sa.CheckConstraint(
            f"state_hash ~ '{_CHECKSUM_SQL}'",
            name="ck_integration_oauth_states_hash",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_integration_oauth_states_expiry"
        ),
        sa.CheckConstraint(
            "octet_length(verifier_nonce) = 12",
            name="ck_integration_oauth_states_nonce",
        ),
    )
    op.create_index(
        "ix_integration_oauth_states_tenant_integration",
        "integration_oauth_states",
        ["tenant_id", "integration_id"],
    )

    # ------------------------------------------------------------------
    # 10. Permission provenance for mirrored external ACLs
    # ------------------------------------------------------------------
    op.add_column(
        "document_permissions",
        sa.Column("managed_by_integration_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "document_permissions",
        sa.Column(
            "permission_origin",
            sa.String(length=16),
            server_default=sa.text("'MANUAL'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_document_permissions_tenant_integration",
        "document_permissions",
        "integrations",
        ["tenant_id", "managed_by_integration_id"],
        ["tenant_id", "integration_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_document_permissions_origin",
        "document_permissions",
        "permission_origin IN ('MANUAL', 'EXTERNAL')",
    )
    op.create_check_constraint(
        "ck_document_permissions_external_owner",
        "document_permissions",
        "permission_origin <> 'EXTERNAL' OR managed_by_integration_id IS NOT NULL",
    )
    op.create_index(
        "ix_document_permissions_tenant_integration",
        "document_permissions",
        ["tenant_id", "managed_by_integration_id"],
        postgresql_where=sa.text("managed_by_integration_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 11. Row level security + least-privilege grants
    # ------------------------------------------------------------------
    for table in TENANT_TABLES:
        _enable_rls(table)

    for table in TENANT_TABLES:
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}"))

    # Replacing a document's live version deletes its chunks and re-ingests.
    # Documents themselves remain undeletable by the application role.
    op.execute(sa.text(f"GRANT DELETE ON document_chunks TO {APP_ROLE}"))


def downgrade() -> None:
    op.execute(sa.text(f"REVOKE DELETE ON document_chunks FROM {APP_ROLE}"))

    op.drop_index(
        "ix_document_permissions_tenant_integration",
        table_name="document_permissions",
    )
    op.drop_constraint(
        "ck_document_permissions_external_owner",
        "document_permissions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_permissions_origin", "document_permissions", type_="check"
    )
    op.drop_constraint(
        "fk_document_permissions_tenant_integration",
        "document_permissions",
        type_="foreignkey",
    )
    op.drop_column("document_permissions", "permission_origin")
    op.drop_column("document_permissions", "managed_by_integration_id")

    for table in TENANT_TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {APP_ROLE}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index(
        "ix_integration_oauth_states_tenant_integration",
        table_name="integration_oauth_states",
    )
    op.drop_table("integration_oauth_states")

    op.drop_index(
        "ix_integration_identity_mappings_tenant_active",
        table_name="integration_identity_mappings",
    )
    op.drop_table("integration_identity_mappings")

    op.drop_index(
        "ix_integration_sync_events_tenant_integration_received",
        table_name="integration_sync_events",
    )
    op.drop_table("integration_sync_events")

    op.drop_index(
        "ix_integration_sync_jobs_tenant_status_next",
        table_name="integration_sync_jobs",
    )
    op.drop_index(
        "ix_integration_sync_jobs_tenant_integration_created",
        table_name="integration_sync_jobs",
    )
    op.drop_index(
        "uq_integration_sync_jobs_active", table_name="integration_sync_jobs"
    )
    op.drop_table("integration_sync_jobs")

    op.drop_index(
        "ix_document_versions_tenant_document_created",
        table_name="document_versions",
    )
    op.drop_index("uq_document_versions_current", table_name="document_versions")
    op.drop_table("document_versions")

    op.drop_index(
        "ix_external_documents_tenant_document", table_name="external_documents"
    )
    op.drop_index(
        "ix_external_documents_tenant_integration_status",
        table_name="external_documents",
    )
    op.drop_table("external_documents")

    op.drop_index(
        "ix_integration_repositories_tenant_selected",
        table_name="integration_repositories",
    )
    op.drop_table("integration_repositories")

    op.drop_index(
        "ix_integration_credentials_tenant_integration",
        table_name="integration_credentials",
    )
    op.drop_table("integration_credentials")

    op.drop_index("ix_integrations_tenant_next_sync", table_name="integrations")
    op.drop_index("ix_integrations_tenant_status", table_name="integrations")
    op.drop_index("ix_integrations_tenant_provider", table_name="integrations")
    op.drop_table("integrations")
