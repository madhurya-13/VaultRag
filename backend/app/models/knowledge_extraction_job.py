"""Durable, idempotent graph-extraction jobs.

Graph extraction runs *after* ingestion completes and in its own status domain.
A graph failure therefore never marks a document FAILED and never blocks
retrieval: the document keeps working while its graph enrichment is retried.

The partial unique index mirrors ``document_ingestion_jobs``: at most one
PENDING/PROCESSING row per ``(tenant, document)``. Claiming is an atomic
``UPDATE ... WHERE status = 'PENDING'`` so two workers cannot process the same
job.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeExtractionJob(Base):
    __tablename__ = "knowledge_extraction_jobs"

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )

    chunks_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    nodes_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    edges_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    evidence_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    evidence_invalidated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
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
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_kg_jobs_tenant_document",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'SKIPPED')",
            name="ck_kg_jobs_status",
        ),
        CheckConstraint(
            "extraction_method IN ('RULES', 'RULES+SPACY', 'RULES+SPACY+LLM')",
            name="ck_kg_jobs_method",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1",
            name="ck_kg_jobs_attempts",
        ),
        CheckConstraint(
            "chunks_processed >= 0 AND nodes_created >= 0 AND edges_created >= 0 "
            "AND evidence_created >= 0 AND evidence_invalidated >= 0",
            name="ck_kg_jobs_counters",
        ),
        CheckConstraint(
            "error_message IS NULL OR length(error_message) <= 500",
            name="ck_kg_jobs_error_message",
        ),
        Index(
            "uq_kg_jobs_active_document",
            "tenant_id",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'PROCESSING')"),
        ),
        Index(
            "ix_kg_jobs_tenant_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_kg_jobs_tenant_document",
            "tenant_id",
            "document_id",
            "created_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeExtractionJob job_id={self.job_id} "
            f"document_id={self.document_id} status={self.status!r}>"
        )
