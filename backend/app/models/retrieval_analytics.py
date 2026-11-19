"""Structured, privacy-limited retrieval analytics.

One row describes one RAG query. It intentionally contains **no** question
text, chunk text, document names, prompts, tokens or JWT values. It exists so
administrators can aggregate retrieval quality and latency without parsing log
strings or reading confidential content.

The runtime role receives SELECT and INSERT only: analytics are append-only.
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


class RetrievalAnalytics(Base):
    __tablename__ = "retrieval_analytics"

    query_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)

    search_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    authorized_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    denied_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    retrieval_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    rerank_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    llm_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    citation_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    evidence_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="insufficient",
        server_default=text("'insufficient'"),
    )
    insufficient_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
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
            "search_mode IN ('semantic', 'keyword', 'hybrid', 'graph', "
            "'graph_hybrid')",
            name="ck_retrieval_analytics_mode",
        ),
        CheckConstraint(
            "evidence_level IN ('strong', 'moderate', 'limited', 'insufficient')",
            name="ck_retrieval_analytics_evidence",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND authorized_count >= 0 AND denied_count >= 0",
            name="ck_retrieval_analytics_counts",
        ),
        CheckConstraint(
            "retrieval_ms >= 0 AND rerank_ms >= 0 AND llm_ms >= 0 AND total_ms >= 0",
            name="ck_retrieval_analytics_latency",
        ),
        Index(
            "ix_retrieval_analytics_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_retrieval_analytics_tenant_mode_created",
            "tenant_id",
            "search_mode",
            "created_at",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RetrievalAnalytics query_id={self.query_id} "
            f"tenant_id={self.tenant_id} mode={self.search_mode!r}>"
        )
