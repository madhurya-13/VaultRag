"""Tenant-scoped, content-free enterprise analytics.

Every value is an aggregate from a persisted table. No question text, chunk
text, prompts or secrets are selected. Document analytics are aggregate-only:
the section returns counts by status/department/classification, never document
titles or contents, so it cannot leak a document the caller cannot read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnswerFeedback,
    Document,
    EvaluationRun,
    Integration,
    IntegrationSyncJob,
    KnowledgeGap,
    KnowledgeGapSignal,
    RetrievalAnalytics,
    SecurityAuditLog,
)
from app.services.knowledge_gaps import summary as gap_summary
from app.services.rag.retrieval_analytics import retrieval_analytics_summary


def _since(days: int) -> tuple[int, datetime]:
    bounded = max(1, min(days, 365))
    return bounded, datetime.now(timezone.utc) - timedelta(days=bounded)


def _counts(db: Session, column, *conditions) -> list[dict]:
    rows = db.execute(
        select(column, func.count()).where(*conditions).group_by(column).order_by(func.count().desc())
    ).all()
    return [{"key": str(key), "count": int(count)} for key, count in rows]


def overview(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, since = _since(days)
    documents = int(db.scalar(select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id)) or 0)
    completed = int(db.scalar(select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id, Document.processing_status == "COMPLETED")) or 0)
    queries = int(db.scalar(select(func.count()).select_from(RetrievalAnalytics).where(RetrievalAnalytics.tenant_id == tenant_id, RetrievalAnalytics.created_at >= since)) or 0)
    feedback = int(db.scalar(select(func.count()).select_from(AnswerFeedback).where(AnswerFeedback.tenant_id == tenant_id, AnswerFeedback.created_at >= since, AnswerFeedback.retracted_at.is_(None))) or 0)
    gaps = int(db.scalar(select(func.count()).select_from(KnowledgeGap).where(KnowledgeGap.tenant_id == tenant_id, KnowledgeGap.occurrence_count >= 3)) or 0)
    evaluations = int(db.scalar(select(func.count()).select_from(EvaluationRun).where(EvaluationRun.tenant_id == tenant_id, EvaluationRun.created_at >= since)) or 0)
    integrations = int(db.scalar(        select(func.count()).select_from(Integration).where(Integration.tenant_id == tenant_id, Integration.connection_status == "CONNECTED")) or 0)
    return {
        "days": bounded,
        "documents": documents,
        "documents_completed": completed,
        "queries": queries,
        "feedback": feedback,
        "knowledge_gaps": gaps,
        "evaluation_runs": evaluations,
        "connected_integrations": integrations,
    }


def usage(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, since = _since(days)
    day_expr = func.date_trunc("day", RetrievalAnalytics.created_at)
    rows = db.execute(
        select(
            day_expr,
            func.count(),
            func.count().filter(RetrievalAnalytics.retrieval_error.is_(True)),
        )
        .where(RetrievalAnalytics.tenant_id == tenant_id, RetrievalAnalytics.created_at >= since)
        .group_by(day_expr)
        .order_by(day_expr)
    ).all()
    return {
        "days": bounded,
        "total_queries": sum(int(count) for _, count, _ in rows),
        "daily": [
            {"date": day, "queries": int(count), "errors": int(errors)}
            for day, count, errors in rows
        ],
        "by_mode": _counts(
            db,
            RetrievalAnalytics.search_mode,
            RetrievalAnalytics.tenant_id == tenant_id,
            RetrievalAnalytics.created_at >= since,
        ),
    }


def documents(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, since = _since(days)
    base = (Document.tenant_id == tenant_id,)
    return {
        "days": bounded,
        "total": int(db.scalar(select(func.count()).select_from(Document).where(*base)) or 0),
        "by_status": _counts(db, Document.processing_status, *base),
        "by_classification": _counts(db, Document.classification, *base),
        "by_department": _counts(db, Document.department, *base),
        "recent_uploads": int(db.scalar(select(func.count()).select_from(Document).where(*base, Document.created_at >= since)) or 0),
    }


def retrieval(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, _ = _since(days)
    payload = retrieval_analytics_summary(db, tenant_id=tenant_id, days=bounded)
    return {
        "days": bounded,
        "total_queries": payload["total_queries"],
        "by_search_mode": payload["by_search_mode"],
        "average_retrieval_ms": payload["average_retrieval_ms"],
        "average_total_ms": payload["average_total_ms"],
        "p95_total_ms": payload["p95_total_ms"],
        "citation_validation_rate": payload["citation_validation_rate"],
        "insufficient_evidence_rate": payload["insufficient_evidence_rate"],
        "retrieval_error_rate": payload["retrieval_error_rate"],
        "daily": payload["daily"],
    }


def integrations(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, since = _since(days)
    return {
        "days": bounded,
        "by_provider": _counts(db, Integration.provider, Integration.tenant_id == tenant_id),
        "by_status": _counts(db, Integration.connection_status, Integration.tenant_id == tenant_id),
        "sync_jobs": int(db.scalar(select(func.count()).select_from(IntegrationSyncJob).where(IntegrationSyncJob.tenant_id == tenant_id, IntegrationSyncJob.created_at >= since)) or 0),
        "sync_status": _counts(
            db,
            IntegrationSyncJob.sync_status,
            IntegrationSyncJob.tenant_id == tenant_id,
            IntegrationSyncJob.created_at >= since,
        ),
    }


def feedback(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    from app.services.feedback import feedback_summary  # local import avoids a cycle

    # The summary service only reads tenant_id from the context.
    return feedback_summary(db, SimpleNamespace(tenant_id=tenant_id), days=days)


def gaps(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    payload = gap_summary(db, SimpleNamespace(tenant_id=tenant_id), days=days)
    payload["recent_signals"] = int(
        db.scalar(
            select(func.count()).select_from(KnowledgeGapSignal).where(
                KnowledgeGapSignal.tenant_id == tenant_id,
                KnowledgeGapSignal.created_at >= datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365))),
            )
        )
        or 0
    )
    return payload


def security(db: Session, tenant_id: UUID, days: int = 30) -> dict:
    bounded, since = _since(days)
    base = (SecurityAuditLog.tenant_id == tenant_id, SecurityAuditLog.timestamp >= since)
    return {
        "days": bounded,
        "events": int(db.scalar(select(func.count()).select_from(SecurityAuditLog).where(*base)) or 0),
        "by_action": _counts(db, SecurityAuditLog.action, *base)[:20],
        "by_decision": _counts(db, SecurityAuditLog.decision, *base),
        "denials": int(db.scalar(select(func.count()).select_from(SecurityAuditLog).where(*base, SecurityAuditLog.decision != "ALLOW")) or 0),
    }
