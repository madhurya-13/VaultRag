"""Milestone 10 phases 3-7: feedback, knowledge gaps and tenant settings.

Revision ID: f2c3d4e5a6b7
Revises: e1b2c3d4f506
Create Date: 2026-11-23

Six tenant-scoped tables. Every table has RLS enabled and forced, a
``tenant_isolation`` policy, tenant-safe foreign keys and least-privilege
grants. Feedback and gap rows are soft-deleted, never hard-deleted through the
runtime role. Settings history is append-only.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c3d4e5a6b7"
down_revision: Union[str, None] = "e1b2c3d4f506"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "vaultrag_app"

TABLES = (
    "answer_feedback",
    "knowledge_gap_signals",
    "knowledge_gaps",
    "knowledge_gap_events",
    "tenant_settings",
    "tenant_settings_history",
)

_FEEDBACK_CATEGORIES = (
    "'INCORRECT_ANSWER', 'MISSING_CITATION', 'WRONG_CITATION', "
    "'INCOMPLETE_ANSWER', 'IRRELEVANT_SOURCES', 'MISSING_DOCUMENT', "
    "'RETRIEVAL_PROBLEM', 'PERMISSION_PROBLEM', 'INGESTION_PROBLEM', 'OTHER'"
)
_GAP_CLASSIFICATIONS = (
    "'MISSING_KNOWLEDGE', 'POOR_RETRIEVAL', 'INSUFFICIENT_PERMISSIONS', "
    "'OUTDATED_KNOWLEDGE', 'CONFLICTING_KNOWLEDGE', 'UNANSWERABLE'"
)


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            AS PERMISSIVE FOR ALL TO PUBLIC
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
    # Feedback's tenant-safe FK needs a tenant-scoped key on the message table.
    op.create_unique_constraint(
        "uq_conversation_messages_tenant_message",
        "conversation_messages",
        ["tenant_id", "message_id"],
    )

    op.create_table(
        "answer_feedback",
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("sentiment", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(20), nullable=False, server_default=sa.text("'UNREVIEWED'")),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "feedback_id", name="uq_answer_feedback_tenant_feedback"),
        sa.ForeignKeyConstraint(["tenant_id", "user_id"], ["users.tenant_id", "users.user_id"], name="fk_answer_feedback_tenant_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "message_id"], ["conversation_messages.tenant_id", "conversation_messages.message_id"], name="fk_answer_feedback_tenant_message", ondelete="CASCADE"),
        sa.CheckConstraint(f"category IN ({_FEEDBACK_CATEGORIES})", name="ck_answer_feedback_category"),
        sa.CheckConstraint("sentiment IN ('POSITIVE', 'NEGATIVE')", name="ck_answer_feedback_sentiment"),
        sa.CheckConstraint("review_status IN ('UNREVIEWED', 'IN_REVIEW', 'RESOLVED', 'DISMISSED')", name="ck_answer_feedback_review"),
        sa.CheckConstraint("comment IS NULL OR (length(trim(comment)) > 0 AND length(comment) <= 2000)", name="ck_answer_feedback_comment"),
        sa.CheckConstraint("review_notes IS NULL OR length(review_notes) <= 2000", name="ck_answer_feedback_review_notes"),
    )
    op.create_index("ix_answer_feedback_tenant_created", "answer_feedback", ["tenant_id", "created_at"])
    op.create_index("ix_answer_feedback_tenant_review", "answer_feedback", ["tenant_id", "review_status"])
    op.create_index("ix_answer_feedback_tenant_message", "answer_feedback", ["tenant_id", "message_id"])
    op.create_index(
        "uq_answer_feedback_active_user_message",
        "answer_feedback",
        ["tenant_id", "user_id", "message_id"],
        unique=True,
        postgresql_where=sa.text("retracted_at IS NULL"),
    )

    op.create_table(
        "knowledge_gap_signals",
        sa.Column("signal_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_fingerprint", sa.String(64), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("topic_key", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("authorized_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("denied_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retrievable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"classification IN ({_GAP_CLASSIFICATIONS})", name="ck_gap_signal_classification"),
        sa.CheckConstraint("candidate_count >= 0 AND authorized_count >= 0 AND denied_count >= 0", name="ck_gap_signal_counts"),
    )
    op.create_index("ix_gap_signals_tenant_created", "knowledge_gap_signals", ["tenant_id", "created_at"])
    op.create_index("ix_gap_signals_tenant_fingerprint", "knowledge_gap_signals", ["tenant_id", "question_fingerprint"])

    op.create_table(
        "knowledge_gaps",
        sa.Column("gap_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_fingerprint", sa.String(64), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("topic_key", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default=sa.text("'MEDIUM'")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolution_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_verified", sa.Boolean(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "gap_id", name="uq_knowledge_gaps_tenant_gap"),
        sa.UniqueConstraint("tenant_id", "question_fingerprint", name="uq_knowledge_gaps_tenant_fingerprint"),
        sa.ForeignKeyConstraint(["tenant_id", "assigned_to"], ["users.tenant_id", "users.user_id"], name="fk_knowledge_gaps_tenant_assignee", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id", "resolution_document_id"], ["documents.tenant_id", "documents.document_id"], name="fk_knowledge_gaps_tenant_document", ondelete="SET NULL"),
        sa.CheckConstraint(f"classification IN ({_GAP_CLASSIFICATIONS})", name="ck_knowledge_gaps_classification"),
        sa.CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED')", name="ck_knowledge_gaps_status"),
        sa.CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_knowledge_gaps_severity"),
        sa.CheckConstraint("occurrence_count >= 0", name="ck_knowledge_gaps_occurrence"),
    )
    op.create_index("ix_knowledge_gaps_tenant_status", "knowledge_gaps", ["tenant_id", "status"])
    op.create_index("ix_knowledge_gaps_tenant_last_seen", "knowledge_gaps", ["tenant_id", "last_seen_at"])

    op.create_table(
        "knowledge_gap_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("gap_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "gap_id"], ["knowledge_gaps.tenant_id", "knowledge_gaps.gap_id"], name="fk_gap_events_gap", ondelete="CASCADE"),
        sa.CheckConstraint("action IN ('CREATED', 'ASSIGNED', 'STATUS_CHANGED', 'RESOLVED', 'RE_EVALUATED')", name="ck_gap_events_action"),
    )
    op.create_index("ix_gap_events_tenant_gap_created", "knowledge_gap_events", ["tenant_id", "gap_id", "created_at"])

    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.tenant_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id", "updated_by"], ["users.tenant_id", "users.user_id"], name="fk_tenant_settings_updated_by", ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 1", name="ck_tenant_settings_version"),
    )

    op.create_table(
        "tenant_settings_history",
        sa.Column("history_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tenant_settings_history_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant_settings.tenant_id"], name="fk_tenant_settings_history_settings", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "changed_by"], ["users.tenant_id", "users.user_id"], name="fk_tenant_settings_history_user", ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 1", name="ck_tenant_settings_history_version"),
    )
    op.create_index("ix_tenant_settings_history_tenant_changed", "tenant_settings_history", ["tenant_id", "changed_at"])

    for table in TABLES:
        _enable_rls(table)

    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON answer_feedback TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT ON knowledge_gap_signals TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON knowledge_gaps TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT ON knowledge_gap_events TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON tenant_settings TO {APP_ROLE}"))
    op.execute(sa.text(f"GRANT SELECT, INSERT ON tenant_settings_history TO {APP_ROLE}"))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(sa.text(f"REVOKE ALL ON {table} FROM {APP_ROLE}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index("ix_tenant_settings_history_tenant_changed", table_name="tenant_settings_history")
    op.drop_table("tenant_settings_history")
    op.drop_table("tenant_settings")
    op.drop_index("ix_gap_events_tenant_gap_created", table_name="knowledge_gap_events")
    op.drop_table("knowledge_gap_events")
    op.drop_index("ix_knowledge_gaps_tenant_last_seen", table_name="knowledge_gaps")
    op.drop_index("ix_knowledge_gaps_tenant_status", table_name="knowledge_gaps")
    op.drop_table("knowledge_gaps")
    op.drop_index("ix_gap_signals_tenant_fingerprint", table_name="knowledge_gap_signals")
    op.drop_index("ix_gap_signals_tenant_created", table_name="knowledge_gap_signals")
    op.drop_table("knowledge_gap_signals")
    op.drop_index("uq_answer_feedback_active_user_message", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_tenant_message", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_tenant_review", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_tenant_created", table_name="answer_feedback")
    op.drop_table("answer_feedback")
    op.drop_constraint("uq_conversation_messages_tenant_message", "conversation_messages", type_="unique")
