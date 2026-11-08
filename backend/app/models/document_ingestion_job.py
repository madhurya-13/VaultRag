"""Ingestion job tracking for document processing.

Jobs are tenant-owned and composite-FK bound to the parent document. The partial
unique index on ``(tenant_id, document_id)`` for PENDING/PROCESSING rows is the
database-backed guard against two concurrent processing attempts.

Error fields contain only safe codes/messages. Document text must never be
written here.
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
from app.core.security_constants import INGESTION_STATUS_PENDING


class DocumentIngestionJob(Base):
    __tablename__ = "document_ingestion_jobs"

    job_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=INGESTION_STATUS_PENDING,
        server_default=text("'PENDING'"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    chunks_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_ingestion_jobs_tenant_document",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_ingestion_jobs_status",
        ),
        CheckConstraint(
            "pages_processed >= 0 AND chunks_created >= 0 AND retry_count >= 0",
            name="ck_ingestion_jobs_counters",
        ),
        Index(
            "uq_ingestion_jobs_active_document",
            "tenant_id",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'PROCESSING')"),
        ),
        Index(
            "ix_ingestion_jobs_tenant_document",
            "tenant_id",
            "document_id",
            "created_at",
        ),
        Index(
            "ix_ingestion_jobs_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentIngestionJob job_id={self.job_id} "
            f"document_id={self.document_id} status={self.status!r}>"
        )
