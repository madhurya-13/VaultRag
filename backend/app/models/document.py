"""Document metadata for permission-aware retrieval.

Milestone 2 stores metadata only. Document contents, embeddings and vector
search are deliberately out of scope.

Tenant integrity
----------------
``tenant_id`` is NOT NULL and references the tenant root. The uploader is
referenced with a composite foreign key so a document cannot claim an uploader
from another tenant. ``UNIQUE (tenant_id, document_id)`` lets child tables use
the same composite tenant-safe pattern as ``user_roles``.
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.security_constants import DOC_STATUS_UPLOADED


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str] = mapped_column(String(50), nullable=False)
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    required_clearance_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )

    # Milestone 3 file and ingestion metadata. These are nullable for legacy
    # metadata-only documents created by the Milestone 2 API.
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DOC_STATUS_UPLOADED,
        server_default=text("'UPLOADED'"),
    )
    total_pages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_chunks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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
        # Needed by document_permissions' composite foreign key.
        UniqueConstraint("tenant_id", "document_id", name="uq_documents_tenant_document"),
        # The uploader must belong to the same tenant as the document.
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by"],
            ["users.tenant_id", "users.user_id"],
            name="fk_documents_tenant_uploader",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="ck_documents_classification",
        ),
        CheckConstraint(
            "required_clearance_level BETWEEN 0 AND 5",
            name="ck_documents_clearance_range",
        ),
        # A document cannot advertise less clearance than its classification
        # floor, even if inserted by a script that bypasses the API.
        CheckConstraint(
            "required_clearance_level >= "
            "CASE classification "
            "WHEN 'PUBLIC' THEN 0 "
            "WHEN 'INTERNAL' THEN 1 "
            "WHEN 'CONFIDENTIAL' THEN 3 "
            "WHEN 'RESTRICTED' THEN 4 "
            "END",
            name="ck_documents_clearance_floor",
        ),
        CheckConstraint("length(trim(document_name)) > 0", name="ck_documents_name_not_blank"),
        CheckConstraint("length(trim(document_type)) > 0", name="ck_documents_type_not_blank"),
        CheckConstraint("length(trim(department)) > 0", name="ck_documents_department_not_blank"),
        CheckConstraint(
            "processing_status IN "
            "('UPLOADED', 'PROCESSING', 'COMPLETED', 'FAILED', 'ARCHIVED')",
            name="ck_documents_processing_status",
        ),
        CheckConstraint(
            "file_size IS NULL OR file_size >= 0",
            name="ck_documents_file_size",
        ),
        CheckConstraint("total_pages >= 0", name="ck_documents_total_pages"),
        CheckConstraint("total_chunks >= 0", name="ck_documents_total_chunks"),
        CheckConstraint(
            "file_hash IS NULL OR file_hash ~ '^[0-9a-f]{64}$'",
            name="ck_documents_file_hash_format",
        ),
        CheckConstraint(
            "storage_path IS NULL OR length(trim(storage_path)) > 0",
            name="ck_documents_storage_path_not_blank",
        ),
        Index("ix_documents_tenant_active", "tenant_id", "is_active"),
        Index("ix_documents_tenant_department", "tenant_id", "department"),
        Index("ix_documents_tenant_classification", "tenant_id", "classification"),
        Index("ix_documents_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_documents_tenant_uploaded_by", "tenant_id", "uploaded_by"),
        Index(
            "ix_documents_tenant_processing_status",
            "tenant_id",
            "processing_status",
        ),
        Index(
            "uq_documents_tenant_file_hash",
            "tenant_id",
            "file_hash",
            unique=True,
            postgresql_where=text("file_hash IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Document document_id={self.document_id} "
            f"tenant_id={self.tenant_id} name={self.document_name!r}>"
        )
