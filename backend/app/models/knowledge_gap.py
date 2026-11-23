"""Tenant-scoped knowledge-gap signals, aggregates and lifecycle events.

No retrieved document content and no user identity is copied into these
tables. A signal stores a bounded, redacted normalized question plus a
fingerprint and topic keywords. Aggregates only surface after a minimum group
size, and permission-denied queries are classified separately so they never
inflate the content-gap count.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

GAP_CLASSIFICATIONS = (
    "MISSING_KNOWLEDGE",
    "POOR_RETRIEVAL",
    "INSUFFICIENT_PERMISSIONS",
    "OUTDATED_KNOWLEDGE",
    "CONFLICTING_KNOWLEDGE",
    "UNANSWERABLE",
)
GAP_STATUSES = ("OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "DISMISSED")

_CLASSIFICATION_SQL = ", ".join(f"'{item}'" for item in GAP_CLASSIFICATIONS)
_STATUS_SQL = ", ".join(f"'{item}'" for item in GAP_STATUSES)


class KnowledgeGapSignal(Base):
    __tablename__ = "knowledge_gap_signals"

    signal_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    question_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    topic_key: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    authorized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    denied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"classification IN ({_CLASSIFICATION_SQL})", name="ck_gap_signal_classification"),
        CheckConstraint(
            "candidate_count >= 0 AND authorized_count >= 0 AND denied_count >= 0",
            name="ck_gap_signal_counts",
        ),
        Index("ix_gap_signals_tenant_created", "tenant_id", "created_at"),
        Index("ix_gap_signals_tenant_fingerprint", "tenant_id", "question_fingerprint"),
    )


class KnowledgeGap(Base):
    __tablename__ = "knowledge_gaps"

    gap_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    question_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    topic_key: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM", server_default=text("'MEDIUM'"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN", server_default=text("'OPEN'"))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    assigned_to: Mapped[UUID | None] = mapped_column(nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_document_id: Mapped[UUID | None] = mapped_column(nullable=True)
    resolution_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "gap_id", name="uq_knowledge_gaps_tenant_gap"),
        UniqueConstraint(
            "tenant_id", "question_fingerprint", name="uq_knowledge_gaps_tenant_fingerprint"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assigned_to"],
            ["users.tenant_id", "users.user_id"],
            name="fk_knowledge_gaps_tenant_assignee",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resolution_document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_knowledge_gaps_tenant_document",
            ondelete="SET NULL",
        ),
        CheckConstraint(f"classification IN ({_CLASSIFICATION_SQL})", name="ck_knowledge_gaps_classification"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_knowledge_gaps_status"),
        CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_knowledge_gaps_severity"),
        CheckConstraint("occurrence_count >= 0", name="ck_knowledge_gaps_occurrence"),
        Index("ix_knowledge_gaps_tenant_status", "tenant_id", "status"),
        Index("ix_knowledge_gaps_tenant_last_seen", "tenant_id", "last_seen_at"),
    )


class KnowledgeGapEvent(Base):
    __tablename__ = "knowledge_gap_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"), nullable=False
    )
    gap_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "gap_id"],
            ["knowledge_gaps.tenant_id", "knowledge_gaps.gap_id"],
            name="fk_gap_events_gap",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "action IN ('CREATED', 'ASSIGNED', 'STATUS_CHANGED', 'RESOLVED', 'RE_EVALUATED')",
            name="ck_gap_events_action",
        ),
        Index("ix_gap_events_tenant_gap_created", "tenant_id", "gap_id", "created_at"),
    )
