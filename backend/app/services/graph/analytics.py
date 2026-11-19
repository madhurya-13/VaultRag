"""Privacy-limited knowledge-graph query metrics.

Only counts and durations are stored, never question text, node names, quotes or
document names. Reporting fails silently: metrics must never break a query.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import KnowledgeQueryAnalytics

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"graph", "graph_hybrid"})
DEFAULT_WINDOW_DAYS = 30


def record_graph_query(
    db: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    search_mode: str,
    seed_count: int = 0,
    node_count: int = 0,
    edge_count: int = 0,
    evidence_count: int = 0,
    denied_documents: int = 0,
    traversal_ms: int = 0,
    total_ms: int = 0,
    retrieval_error: bool = False,
    error_code: str | None = None,
) -> None:
    """Insert one analytics row. Never raises into the request path."""
    settings = get_settings()
    if not settings.knowledge_graph_analytics_enabled:
        return
    if search_mode not in VALID_MODES:
        return
    try:
        db.add(
            KnowledgeQueryAnalytics(
                tenant_id=tenant_id,
                user_id=user_id,
                search_mode=search_mode,
                seed_count=max(0, int(seed_count)),
                node_count=max(0, int(node_count)),
                edge_count=max(0, int(edge_count)),
                evidence_count=max(0, int(evidence_count)),
                denied_documents=max(0, int(denied_documents)),
                traversal_ms=max(0, int(traversal_ms)),
                total_ms=max(0, int(total_ms)),
                retrieval_error=bool(retrieval_error),
                error_code=(error_code or None),
            )
        )
        db.flush()
    except Exception:
        logger.exception("Failed to record knowledge graph query analytics")


def graph_analytics_summary(
    db: Session, *, tenant_id: UUID, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict:
    """Aggregate counts per mode for one tenant and time window."""
    window = max(1, min(int(window_days), 365))
    since = datetime.now(timezone.utc) - timedelta(days=window)

    rows = db.execute(
        select(
            KnowledgeQueryAnalytics.search_mode,
            func.count().label("queries"),
            func.coalesce(func.sum(KnowledgeQueryAnalytics.seed_count), 0),
            func.coalesce(func.sum(KnowledgeQueryAnalytics.node_count), 0),
            func.coalesce(func.sum(KnowledgeQueryAnalytics.edge_count), 0),
            func.coalesce(func.sum(KnowledgeQueryAnalytics.evidence_count), 0),
            func.coalesce(func.sum(KnowledgeQueryAnalytics.denied_documents), 0),
            func.coalesce(func.avg(KnowledgeQueryAnalytics.traversal_ms), 0),
            func.coalesce(func.avg(KnowledgeQueryAnalytics.total_ms), 0),
            func.coalesce(
                func.sum(cast(KnowledgeQueryAnalytics.retrieval_error, Integer)),
                0,
            ),
        )
        .where(
            KnowledgeQueryAnalytics.tenant_id == tenant_id,
            KnowledgeQueryAnalytics.created_at >= since,
        )
        .group_by(KnowledgeQueryAnalytics.search_mode)
        .order_by(KnowledgeQueryAnalytics.search_mode.asc())
    ).all()

    modes = [
        {
            "search_mode": row[0],
            "queries": int(row[1]),
            "seed_count": int(row[2]),
            "node_count": int(row[3]),
            "edge_count": int(row[4]),
            "evidence_count": int(row[5]),
            "denied_documents": int(row[6]),
            "average_traversal_ms": round(float(row[7]), 1),
            "average_total_ms": round(float(row[8]), 1),
            "retrieval_errors": int(row[9]),
        }
        for row in rows
    ]
    return {
        "window_days": window,
        "modes": modes,
        "total_queries": sum(mode["queries"] for mode in modes),
    }
