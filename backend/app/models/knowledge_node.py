"""Canonical knowledge-graph entities.

A node is *not* a document artifact: it is the tenant's resolved entity, merged
from evidence across documents. That is why a node carries no ``document_id``.
Every claim about the node lives in ``knowledge_evidence``, and a node is visible
to a caller only while at least one of those evidence rows points at a document
the caller may currently read.

``identity_key`` is the merge-safety boundary. It is derived from an *anchor*
(an employee ID, email address or explicit alias) when one exists, and from the
normalized name otherwise. Two people called "Alice Smith" therefore never
collapse into one node unless a shared anchor proves they are the same person.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

NODE_TYPES_SQL = (
    "'PERSON', 'ORGANIZATION', 'LOCATION', 'POLICY', 'PROCESS', 'SYSTEM', "
    "'PRODUCT', 'DATE', 'ROLE', 'DEPARTMENT', 'OTHER'"
)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    node_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )

    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Lower-cased, punctuation-collapsed form used for exact alias lookup and
    # prefix search. Never used as a security input.
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(160), nullable=False)

    # Deterministically generated from visible evidence at read time. Stored
    # summaries are a convenience for the admin view only; the API regenerates
    # the description from currently authorized evidence before returning it.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    extraction_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    mention_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
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
        # Required by every composite tenant-safe child foreign key.
        UniqueConstraint(
            "tenant_id", "node_id", name="uq_knowledge_nodes_tenant_node"
        ),
        # One identity per tenant and type. Same display name with a different
        # anchor produces a different identity key, so homonyms stay separate.
        UniqueConstraint(
            "tenant_id",
            "node_type",
            "identity_key",
            name="uq_knowledge_nodes_identity",
        ),
        CheckConstraint(
            f"node_type IN ({NODE_TYPES_SQL})", name="ck_knowledge_nodes_type"
        ),
        CheckConstraint(
            "length(trim(canonical_name)) > 0 AND length(canonical_name) <= 255",
            name="ck_knowledge_nodes_canonical_name",
        ),
        CheckConstraint(
            "length(trim(display_name)) > 0 AND length(display_name) <= 255",
            name="ck_knowledge_nodes_display_name",
        ),
        CheckConstraint(
            "length(trim(normalized_name)) > 0", name="ck_knowledge_nodes_normalized"
        ),
        CheckConstraint(
            "length(trim(identity_key)) > 0", name="ck_knowledge_nodes_identity"
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_knowledge_nodes_confidence",
        ),
        CheckConstraint(
            "mention_count >= 0", name="ck_knowledge_nodes_mention_count"
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 4000",
            name="ck_knowledge_nodes_description",
        ),
        Index(
            "ix_knowledge_nodes_tenant_type",
            "tenant_id",
            "node_type",
            "is_archived",
        ),
        Index("ix_knowledge_nodes_tenant_normalized", "tenant_id", "normalized_name"),
        Index("ix_knowledge_nodes_tenant_updated", "tenant_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeNode node_id={self.node_id} type={self.node_type!r} "
            f"name={self.canonical_name!r}>"
        )
