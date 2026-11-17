"""Milestone 7: full-text search, conversations and retrieval analytics.

Revision ID: b7d4c9e2a1f0
Revises: 4e8a1c2b7d90
Create Date: 2026-11-17

What this migration adds
------------------------
1. ``document_chunks.content_tsv`` as a generated tsvector plus a GIN index.
   ``to_tsvector('simple', ...)`` is immutable, so PostgreSQL can maintain it
   automatically. The vector is derived from the row and is never an
   authorization input.
2. ``conversations`` and ``conversation_messages`` with full ownership keys,
   soft deletion and forced RLS. Only citation references are stored, never
   chunk text.
3. ``retrieval_analytics``: append-only, privacy-limited query metrics with
   forced RLS.

All three new tables use the same ``security.current_tenant_id()`` policy as
the existing tenant tables and fail closed without a transaction-local tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d4c9e2a1f0"
down_revision: Union[str, None] = "4e8a1c2b7d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TENANT_TABLES = (
    "conversations",
    "conversation_messages",
    "retrieval_analytics",
)


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
    # 1. PostgreSQL full-text search over chunk text
    # ------------------------------------------------------------------
    op.add_column(
        "document_chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', chunk_text)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_document_chunks_content_tsv",
        "document_chunks",
        ["content_tsv"],
        unique=False,
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # 2. Conversations
    # ------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "summary_message_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_archived", sa.Boolean(), server_default=sa.text("false"), nullable=False
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
        sa.CheckConstraint(
            "title IS NULL OR (length(trim(title)) > 0 AND length(title) <= 120)",
            name="ck_conversations_title",
        ),
        sa.CheckConstraint(
            "summary_message_count >= 0",
            name="ck_conversations_summary_count",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_conversations_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.user_id"],
            name="fk_conversations_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            name="uq_conversations_tenant_conversation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            "user_id",
            name="uq_conversations_tenant_conversation_user",
        ),
    )
    op.create_index(
        "ix_conversations_tenant_user_updated",
        "conversations",
        ["tenant_id", "user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_tenant_deleted",
        "conversations",
        ["tenant_id", "deleted_at"],
        unique=False,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_level", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_conversation_messages_role",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_conversation_messages_content_not_blank",
        ),
        sa.CheckConstraint(
            "length(content) <= 20000",
            name="ck_conversation_messages_content_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_conversation_messages_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "user_id"],
            [
                "conversations.tenant_id",
                "conversations.conversation_id",
                "conversations.user_id",
            ],
            name="fk_conversation_messages_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_conversation_messages")),
    )
    op.create_index(
        "ix_conversation_messages_tenant_conversation_created",
        "conversation_messages",
        ["tenant_id", "conversation_id", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 3. Retrieval analytics
    # ------------------------------------------------------------------
    op.create_table(
        "retrieval_analytics",
        sa.Column("query_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("search_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "authorized_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "denied_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "retrieval_ms", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "rerank_ms", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("llm_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "citation_valid",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "evidence_level",
            sa.String(length=20),
            server_default=sa.text("'insufficient'"),
            nullable=False,
        ),
        sa.Column(
            "insufficient_evidence",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_error",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "search_mode IN ('semantic', 'keyword', 'hybrid')",
            name="ck_retrieval_analytics_mode",
        ),
        sa.CheckConstraint(
            "evidence_level IN ('strong', 'moderate', 'limited', 'insufficient')",
            name="ck_retrieval_analytics_evidence",
        ),
        sa.CheckConstraint(
            "candidate_count >= 0 AND authorized_count >= 0 AND denied_count >= 0",
            name="ck_retrieval_analytics_counts",
        ),
        sa.CheckConstraint(
            "retrieval_ms >= 0 AND rerank_ms >= 0 AND llm_ms >= 0 AND total_ms >= 0",
            name="ck_retrieval_analytics_latency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.tenant_id"],
            name=op.f("fk_retrieval_analytics_tenant_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("query_id", name=op.f("pk_retrieval_analytics")),
    )
    op.create_index(
        "ix_retrieval_analytics_tenant_created",
        "retrieval_analytics",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_analytics_tenant_mode_created",
        "retrieval_analytics",
        ["tenant_id", "search_mode", "created_at"],
        unique=False,
    )

    for table in TENANT_TABLES:
        _enable_rls(table)

    # Least-privilege runtime grants. Conversation deletion is soft, so no
    # DELETE grant is required. Analytics and audit history are append-only.
    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON conversations TO {APP_ROLE}"))
    op.execute(
        sa.text(f"GRANT SELECT, INSERT, UPDATE ON conversation_messages TO {APP_ROLE}")
    )
    op.execute(
        sa.text(f"GRANT SELECT, INSERT ON retrieval_analytics TO {APP_ROLE}")
    )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {APP_ROLE}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index(
        "ix_retrieval_analytics_tenant_mode_created",
        table_name="retrieval_analytics",
    )
    op.drop_index(
        "ix_retrieval_analytics_tenant_created", table_name="retrieval_analytics"
    )
    op.drop_table("retrieval_analytics")

    op.drop_index(
        "ix_conversation_messages_tenant_conversation_created",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")

    op.drop_index("ix_conversations_tenant_deleted", table_name="conversations")
    op.drop_index("ix_conversations_tenant_user_updated", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_document_chunks_content_tsv", table_name="document_chunks")
    op.drop_column("document_chunks", "content_tsv")
