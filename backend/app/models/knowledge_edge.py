"""Typed, evidence-backed relationships between knowledge nodes.

An edge is unique per ``(tenant, source, target, relation_type)``, so repeated
mentions strengthen one edge instead of creating duplicates. Endpoints are
bound with composite tenant-safe foreign keys: an edge cannot connect a node
from another tenant even if application code is wrong.

Visibility is independent of its endpoints. An edge is shown to a caller only
when the edge itself has at least one currently authorized evidence row; two
visible nodes do not make the relationship between them visible.
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
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"

    edge_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )

    source_node_id: Mapped[UUID] = mapped_column(nullable=False)
    target_node_id: Mapped[UUID] = mapped_column(nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Normalized relation label used for grouping and conflict detection.
    relation_key: Mapped[str] = mapped_column(String(64), nullable=False)

    extraction_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    archived_at: Mapped[datetime | None] = mapped_column(
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
            "tenant_id", "edge_id", name="uq_knowledge_edges_tenant_edge"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_node_id",
            "target_node_id",
            "relation_key",
            name="uq_knowledge_edges_triple",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_edges_tenant_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_knowledge_edges_tenant_target",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_node_id <> target_node_id", name="ck_knowledge_edges_no_self_loop"
        ),
        CheckConstraint(
            "length(trim(relation_type)) > 0 AND length(relation_type) <= 64",
            name="ck_knowledge_edges_relation_type",
        ),
        CheckConstraint(
            "length(trim(relation_key)) > 0", name="ck_knowledge_edges_relation_key"
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_edges_confidence",
        ),
        CheckConstraint("evidence_count >= 0", name="ck_knowledge_edges_evidence_count"),
        Index(
            "ix_knowledge_edges_tenant_source",
            "tenant_id",
            "source_node_id",
            "is_archived",
        ),
        Index(
            "ix_knowledge_edges_tenant_target",
            "tenant_id",
            "target_node_id",
            "is_archived",
        ),
        Index("ix_knowledge_edges_tenant_relation", "tenant_id", "relation_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeEdge edge_id={self.edge_id} "
            f"{self.source_node_id}->{self.target_node_id} "
            f"relation={self.relation_type!r}>"
        )
