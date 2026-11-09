"""Permission-aware document chunks and vector embeddings.

Tenant integrity is enforced twice:

* a direct foreign key to ``organizations``;
* a composite foreign key ``(tenant_id, document_id)`` to
  ``documents(tenant_id, document_id)``.

The composite key means a chunk cannot claim tenant A while pointing at a
document owned by tenant B, even if application code is wrong.

``metadata`` is an SQLAlchemy-reserved attribute name, so the Python attribute
is ``chunk_metadata`` while the database column remains ``metadata``.

The JSONB snapshot is deliberately NOT the authorization source of truth.
Future retrieval must recheck ``document_permissions`` through the central
authorization engine.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.document_id"],
            name="fk_document_chunks_tenant_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index"),
        CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_document_chunks_pages",
        ),
        CheckConstraint(
            "length(trim(chunk_text)) > 0",
            name="ck_document_chunks_text_not_blank",
        ),
        Index(
            "ix_document_chunks_tenant_document",
            "tenant_id",
            "document_id",
        ),
        Index(
            "ix_document_chunks_metadata_gin",
            text("metadata"),
            postgresql_using="gin",
        ),
        # Optional ANN index for cosine retrieval. Benchmarked at 10k synthetic
        # 384-dimensional vectors: ~7.2 ms with HNSW versus ~12.1 ms sequential.
        # The query still keeps explicit tenant, document-state, model and
        # permission predicates; HNSW is a performance aid, never a security
        # boundary.
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": "16", "ef_construction": "64"},
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentChunk chunk_id={self.chunk_id} "
            f"document_id={self.document_id} index={self.chunk_index}>"
        )
