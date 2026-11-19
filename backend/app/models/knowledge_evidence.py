"""Source evidence for a node mention or an edge assertion.

This table is the *entire* authorization surface of the knowledge graph. A node,
edge, alias or aggregate count is visible to a caller only while at least one
evidence row pointing at a currently readable document exists. Nothing is
cached: revocation, archival, expiry and ABAC changes take effect on the next
request because visibility is recomputed from the row state each time.

Rows are never hard-deleted by the runtime role. When a document is archived or
re-processed, evidence is invalidated (``is_valid = false`` plus
``invalidated_at``) and archived nodes/edges are recomputed. That keeps the
audit trail intact while removing the claim from every future answer.

``quote`` is a verbatim fragment of ``document_chunks.chunk_text``. The
extraction validator rejects anything that is not a normalized substring, which
is what makes the quote usable as a citation anchor.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"

    evidence_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )

    document_id: Mapped[UUID] = mapped_column(nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(nullable=False)

    node_id: Mapped[UUID | None] = mapped_column(nullable=True)
    edge_id: Mapped[UUID | None] = mapped_column(nullable=True)

    quote: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)

    is_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The composite key is what makes tenant isolation a database rule and
        # not only an application convention.
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "chunk_id"],
            [
                "document_chunks.tenant_id",
                "document_chunks.document_id",
                "document_chunks.chunk_id",
            ],
            name="fk_knowledge_evidence_tenant_chunk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_evidence_tenant_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "edge_id"],
            ["knowledge_edges.tenant_id", "knowledge_edges.edge_id"],
            name="fk_knowledge_evidence_tenant_edge",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(node_id IS NOT NULL AND edge_id IS NULL) "
            "OR (node_id IS NULL AND edge_id IS NOT NULL)",
            name="ck_knowledge_evidence_subject",
        ),
        CheckConstraint(
            "length(trim(quote)) > 0 AND length(quote) <= 2000",
            name="ck_knowledge_evidence_quote",
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_evidence_confidence",
        ),
        CheckConstraint(
            "extraction_method IN ('RULES', 'RULES+SPACY', 'RULES+SPACY+LLM')",
            name="ck_knowledge_evidence_method",
        ),
        CheckConstraint(
            "(is_valid AND invalidated_at IS NULL) "
            "OR (NOT is_valid AND invalidated_at IS NOT NULL)",
            name="ck_knowledge_evidence_validity",
        ),
        Index(
            "ix_knowledge_evidence_tenant_node",
            "tenant_id",
            "node_id",
            "is_valid",
        ),
        Index(
            "ix_knowledge_evidence_tenant_edge",
            "tenant_id",
            "edge_id",
            "is_valid",
        ),
        Index(
            "ix_knowledge_evidence_tenant_document",
            "tenant_id",
            "document_id",
            "is_valid",
        ),
        Index(
            "ix_knowledge_evidence_tenant_chunk",
            "tenant_id",
            "chunk_id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeEvidence evidence_id={self.evidence_id} "
            f"document_id={self.document_id} node_id={self.node_id} "
            f"edge_id={self.edge_id}>"
        )
