"""Privacy-limited knowledge-graph query metrics.

One row per graph or graph-hybrid query. Like ``retrieval_analytics`` it stores
counts and durations only: no question text, node names, quotes or document
names. Append-only for the runtime role.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeQueryAnalytics(Base):
    __tablename__ = "knowledge_query_analytics"

    query_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)

    search_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    seed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    node_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    edge_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    denied_documents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    traversal_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    retrieval_error: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "search_mode IN ('graph', 'graph_hybrid')",
            name="ck_kg_query_analytics_mode",
        ),
        CheckConstraint(
            "seed_count >= 0 AND node_count >= 0 AND edge_count >= 0 "
            "AND evidence_count >= 0 AND denied_documents >= 0",
            name="ck_kg_query_analytics_counts",
        ),
        CheckConstraint(
            "traversal_ms >= 0 AND total_ms >= 0",
            name="ck_kg_query_analytics_latency",
        ),
        Index(
            "ix_kg_query_analytics_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_kg_query_analytics_tenant_mode_created",
            "tenant_id",
            "search_mode",
            "created_at",
        ),
    )
