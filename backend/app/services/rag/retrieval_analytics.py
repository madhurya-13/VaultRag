"""Tenant-scoped retrieval analytics.

Only numeric, operational signals are stored: mode, counts, durations, citation
validation status and an error code. The question, prompts, chunk text,
document names and tokens are never recorded.

Analytics are best-effort: a failure must not turn a successful answer into an
error. The audit log remains the security-critical record.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, set_tenant_context
from app.models import RetrievalAnalytics

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def record_retrieval_metrics(
    *,
    tenant_id: UUID,
    user_id: UUID,
    search_mode: str,
    query_id: UUID | None = None,
    candidate_count: int = 0,
    authorized_count: int = 0,
    denied_count: int = 0,
    retrieval_ms: int = 0,
    rerank_ms: int = 0,
    llm_ms: int = 0,
    total_ms: int = 0,
    citation_valid: bool = False,
    evidence_level: str = "insufficient",
    insufficient_evidence: bool = False,
    retrieval_error: bool = False,
    error_code: str | None = None,
    session_factory: SessionFactory | None = None,
) -> None:
    """Append one analytics row. Never raises to the caller."""
    factory = session_factory or SessionLocal
    try:
        with factory() as session:
            with session.begin():
                set_tenant_context(session, tenant_id)
                session.add(
                    RetrievalAnalytics(
                        query_id=query_id or uuid4(),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        search_mode=search_mode,
                        candidate_count=max(0, int(candidate_count)),
                        authorized_count=max(0, int(authorized_count)),
                        denied_count=max(0, int(denied_count)),
                        retrieval_ms=max(0, int(retrieval_ms)),
                        rerank_ms=max(0, int(rerank_ms)),
                        llm_ms=max(0, int(llm_ms)),
                        total_ms=max(0, int(total_ms)),
                        citation_valid=bool(citation_valid),
                        evidence_level=evidence_level
                        if evidence_level
                        in {"strong", "moderate", "limited", "insufficient"}
                        else "insufficient",
                        insufficient_evidence=bool(insufficient_evidence),
                        retrieval_error=bool(retrieval_error),
                        error_code=(error_code or None),
                    )
                )
    except Exception:
        # Analytics is observability, not a security control. Degrade to a log
        # entry instead of failing the user's answer.
        logger.exception("Failed to record retrieval analytics")


def retrieval_analytics_summary(
    db: Session,
    *,
    tenant_id: UUID,
    days: int = 7,
) -> dict:
    """Aggregate tenant-scoped retrieval metrics for the admin dashboard."""
    bounded_days = max(1, min(days, 90))
    since = datetime.now(timezone.utc) - timedelta(days=bounded_days)
    window = (
        RetrievalAnalytics.tenant_id == tenant_id,
        RetrievalAnalytics.created_at >= since,
    )

    total = int(
        db.scalar(
            select(func.count()).select_from(RetrievalAnalytics).where(*window)
        )
        or 0
    )

    mode_rows = db.execute(
        select(RetrievalAnalytics.search_mode, func.count())
        .where(*window)
        .group_by(RetrievalAnalytics.search_mode)
        .order_by(func.count().desc(), RetrievalAnalytics.search_mode)
    ).all()

    average_retrieval_ms = float(
        db.scalar(
            select(func.coalesce(func.avg(RetrievalAnalytics.retrieval_ms), 0)).where(
                *window
            )
        )
        or 0.0
    )
    average_total_ms = float(
        db.scalar(
            select(func.coalesce(func.avg(RetrievalAnalytics.total_ms), 0)).where(
                *window
            )
        )
        or 0.0
    )
    p95_total_ms = float(
        db.scalar(
            select(
                func.coalesce(
                    func.percentile_cont(0.95).within_group(
                        RetrievalAnalytics.total_ms
                    ),
                    0,
                )
            ).where(*window)
        )
        or 0.0
    )

    citation_valid = int(
        db.scalar(
            select(func.count())
            .select_from(RetrievalAnalytics)
            .where(*window, RetrievalAnalytics.citation_valid.is_(True))
        )
        or 0
    )
    insufficient = int(
        db.scalar(
            select(func.count())
            .select_from(RetrievalAnalytics)
            .where(*window, RetrievalAnalytics.insufficient_evidence.is_(True))
        )
        or 0
    )
    errors = int(
        db.scalar(
            select(func.count())
            .select_from(RetrievalAnalytics)
            .where(*window, RetrievalAnalytics.retrieval_error.is_(True))
        )
        or 0
    )

    day_expr = func.date_trunc("day", RetrievalAnalytics.created_at)
    daily_rows = db.execute(
        select(
            day_expr,
            func.count(),
            func.count().filter(RetrievalAnalytics.retrieval_error.is_(True)),
        )
        .where(*window)
        .group_by(day_expr)
        .order_by(day_expr)
    ).all()

    def _rate(count: int) -> float:
        return round(count / total, 4) if total else 0.0

    return {
        "days": bounded_days,
        "since": since,
        "total_queries": total,
        "by_search_mode": [
            {"mode": mode, "count": int(count)} for mode, count in mode_rows
        ],
        "average_retrieval_ms": round(average_retrieval_ms, 2),
        "average_total_ms": round(average_total_ms, 2),
        "p95_total_ms": round(p95_total_ms, 2),
        "citation_validation_rate": _rate(citation_valid),
        "insufficient_evidence_rate": _rate(insufficient),
        "retrieval_error_rate": _rate(errors),
        "daily": [
            {
                "date": day,
                "queries": int(count),
                "errors": int(error_count),
            }
            for day, count, error_count in daily_rows
        ],
    }
