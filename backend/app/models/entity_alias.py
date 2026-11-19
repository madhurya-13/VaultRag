"""Alternative surface forms attached to one canonical node.

An alias is the only automatic merge signal besides an explicit anchor. Exact
alias equality inside a tenant and node type is safe; fuzzy similarity only ever
creates a review row, never a merge (see ``knowledge_entity_reviews``).

``normalized_alias`` is unique per node and is what lookup uses. Two different
nodes may hold the same normalized alias (that is the ambiguity an administrator
resolves), so the constraint is per node rather than per tenant.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EntityAlias(Base):
    __tablename__ = "entity_aliases"

    alias_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[UUID] = mapped_column(nullable=False)

    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["knowledge_nodes.tenant_id", "knowledge_nodes.node_id"],
            name="fk_entity_aliases_tenant_node",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "node_id",
            "normalized_alias",
            name="uq_entity_aliases_node_alias",
        ),
        CheckConstraint(
            "length(trim(alias)) > 0 AND length(alias) <= 255",
            name="ck_entity_aliases_alias",
        ),
        CheckConstraint(
            "length(trim(normalized_alias)) > 0",
            name="ck_entity_aliases_normalized",
        ),
        CheckConstraint(
            "source IN ('EXTRACTION', 'MANUAL', 'LLM')",
            name="ck_entity_aliases_source",
        ),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_entity_aliases_confidence",
        ),
        Index(
            "ix_entity_aliases_tenant_normalized",
            "tenant_id",
            "normalized_alias",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<EntityAlias alias_id={self.alias_id} node_id={self.node_id} "
            f"alias={self.alias!r}>"
        )
