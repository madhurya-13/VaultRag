"""Privacy-preserving knowledge-gap capture, aggregation and lifecycle.

Signals are created only when an answer had insufficient evidence or no
authorized context. A permission denial is classified separately and is never
counted as missing content. Raw conversation text, document text and user
identity are not stored: the signal keeps a redacted normalized question, a
SHA-256 fingerprint and topic keywords. Clusters surface only after
``MIN_OCCURRENCES`` signals.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, set_tenant_context
from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies.authentication import AuthenticatedContext
from app.models import KnowledgeGap, KnowledgeGapEvent, KnowledgeGapSignal
from app.models.knowledge_gap import GAP_CLASSIFICATIONS, GAP_STATUSES

logger = logging.getLogger(__name__)

MIN_OCCURRENCES = 3
RETENTION_DAYS = 180
MAX_QUESTION_CHARS = 500

_EMAIL = re.compile(r"\b[^@\s]+@[^@\s]+\.[A-Za-z]{2,}\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b")
_LONG_NUMBER = re.compile(r"\b\d{4,}\b")


def normalize_question(question: str) -> str:
    text = " ".join((question or "").split()).lower()
    text = _EMAIL.sub("[email]", text)
    text = _UUID.sub("[id]", text)
    text = _LONG_NUMBER.sub("[number]", text)
    return text[:MAX_QUESTION_CHARS]


def fingerprint(question: str) -> str:
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


def topic_key(question: str) -> str:
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", normalize_question(question))
    stop = {
        "the", "and", "for", "what", "how", "why", "when", "where", "who",
        "does", "did", "can", "could", "should", "would", "with", "from",
        "that", "this", "are", "was", "were", "have", "has", "had", "our",
        "you", "your", "about", "into", "which", "many", "much",
    }
    keywords = [token for token in tokens if token not in stop][:6]
    return " ".join(keywords) or "general"


def classify(
    *,
    question: str,
    candidate_count: int,
    authorized_count: int,
    denied_count: int,
    retrieved_chunks: int,
    conflicts: int = 0,
) -> str:
    lowered = question.lower()
    if denied_count > 0 and authorized_count == 0:
        return "INSUFFICIENT_PERMISSIONS"
    if conflicts > 0:
        return "CONFLICTING_KNOWLEDGE"
    if any(word in lowered for word in ("current", "latest", "up to date", "2023", "2022")):
        return "OUTDATED_KNOWLEDGE"
    if candidate_count == 0 or authorized_count == 0:
        return "MISSING_KNOWLEDGE"
    if retrieved_chunks == 0:
        return "POOR_RETRIEVAL"
    if "unanswerable" in lowered or "cannot be answered" in lowered:
        return "UNANSWERABLE"
    return "POOR_RETRIEVAL"


def _severity(count: int) -> str:
    if count >= 10:
        return "HIGH"
    if count >= 5:
        return "MEDIUM"
    return "LOW"


def record_gap_signal(
    *,
    tenant_id: UUID,
    question: str,
    candidate_count: int,
    authorized_count: int,
    denied_count: int,
    retrieved_chunks: int,
    conflicts: int = 0,
) -> None:
    """Best-effort signal capture. Never raises into the answer path."""
    if not question or not question.strip():
        return
    classification = classify(
        question=question,
        candidate_count=candidate_count,
        authorized_count=authorized_count,
        denied_count=denied_count,
        retrieved_chunks=retrieved_chunks,
        conflicts=conflicts,
    )
    if classification == "INSUFFICIENT_PERMISSIONS":
        # Permission gaps are a security/entitlement signal, not missing content.
        # They are recorded for audit elsewhere but excluded from gap counts.
        return
    normalized = normalize_question(question)
    question_fp = fingerprint(question)
    topic = topic_key(question)
    try:
        with SessionLocal() as session:
            with session.begin():
                set_tenant_context(session, tenant_id)
                session.add(
                    KnowledgeGapSignal(
                        tenant_id=tenant_id,
                        question_fingerprint=question_fp,
                        normalized_question=normalized,
                        topic_key=topic,
                        classification=classification,
                        candidate_count=max(0, int(candidate_count)),
                        authorized_count=max(0, int(authorized_count)),
                        denied_count=max(0, int(denied_count)),
                        retrievable=retrieved_chunks > 0,
                    )
                )
                gap = session.scalar(
                    select(KnowledgeGap).where(
                        KnowledgeGap.tenant_id == tenant_id,
                        KnowledgeGap.question_fingerprint == question_fp,
                    )
                )
                now = datetime.now(timezone.utc)
                if gap is None:
                    gap = KnowledgeGap(
                        tenant_id=tenant_id,
                        question_fingerprint=question_fp,
                        normalized_question=normalized,
                        topic_key=topic,
                        classification=classification,
                        severity="LOW",
                        status="OPEN",
                        occurrence_count=1,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(gap)
                    session.flush()
                    session.add(
                        KnowledgeGapEvent(
                            tenant_id=tenant_id,
                            gap_id=gap.gap_id,
                            action="CREATED",
                            actor_id=None,  # system signal, not a user action
                            to_status="OPEN",
                            detail="Signal cluster created",
                        )
                    )
                else:
                    gap.occurrence_count += 1
                    gap.last_seen_at = now
                    gap.classification = classification
                    gap.severity = _severity(gap.occurrence_count)
    except Exception:
        logger.exception("Failed to record knowledge-gap signal")


def _visible_gaps(context: AuthenticatedContext):
    since = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return (
        KnowledgeGap.tenant_id == context.tenant_id,
        KnowledgeGap.occurrence_count >= MIN_OCCURRENCES,
        KnowledgeGap.last_seen_at >= since,
    )


def list_gaps(
    db: Session,
    context: AuthenticatedContext,
    *,
    status: str | None = None,
    classification: str | None = None,
    limit: int = 100,
) -> list[KnowledgeGap]:
    statement = select(KnowledgeGap).where(*_visible_gaps(context))
    if status:
        normalized = status.strip().upper()
        if normalized not in GAP_STATUSES:
            raise BadRequestError("Unknown gap status")
        statement = statement.where(KnowledgeGap.status == normalized)
    if classification:
        normalized = classification.strip().upper()
        if normalized not in GAP_CLASSIFICATIONS:
            raise BadRequestError("Unknown gap classification")
        statement = statement.where(KnowledgeGap.classification == normalized)
    return list(
        db.scalars(
            statement.order_by(
                KnowledgeGap.occurrence_count.desc(), KnowledgeGap.last_seen_at.desc()
            ).limit(max(1, min(limit, 200)))
        ).all()
    )


def summary(db: Session, context: AuthenticatedContext, *, days: int = 30) -> dict:
    bounded = max(1, min(days, 365))
    cutoff = datetime.now(timezone.utc) - timedelta(days=bounded)
    from app.models import RetrievalAnalytics

    total_queries = int(
        db.scalar(
            select(func.count())
            .select_from(RetrievalAnalytics)
            .where(
                RetrievalAnalytics.tenant_id == context.tenant_id,
                RetrievalAnalytics.created_at >= cutoff,
            )
        )
        or 0
    )
    gaps = list_gaps(db, context, limit=200)
    by_classification = {}
    by_status = {}
    for gap in gaps:
        by_classification[gap.classification] = by_classification.get(gap.classification, 0) + gap.occurrence_count
        by_status[gap.status] = by_status.get(gap.status, 0) + 1
    signals = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeGapSignal)
            .where(
                KnowledgeGapSignal.tenant_id == context.tenant_id,
                KnowledgeGapSignal.created_at >= cutoff,
            )
        )
        or 0
    )
    return {
        "days": bounded,
        "total_queries": total_queries,
        "total_signals": signals,
        "visible_gaps": len(gaps),
        "unresolved_gaps": sum(1 for gap in gaps if gap.status != "RESOLVED" and gap.status != "DISMISSED"),
        "by_classification": [
            {"classification": key, "count": value}
            for key, value in sorted(by_classification.items(), key=lambda item: -item[1])
        ],
        "by_status": [
            {"status": key, "count": value} for key, value in sorted(by_status.items())
        ],
        "minimum_group_size": MIN_OCCURRENCES,
        "retention_days": RETENTION_DAYS,
    }


def get_gap(db: Session, context: AuthenticatedContext, gap_id: UUID) -> KnowledgeGap:
    gap = db.scalar(
        select(KnowledgeGap).where(
            KnowledgeGap.tenant_id == context.tenant_id,
            KnowledgeGap.gap_id == gap_id,
        )
    )
    if gap is None:
        raise NotFoundError("Knowledge gap not found")
    return gap


def _event(
    db: Session,
    context: AuthenticatedContext,
    gap: KnowledgeGap,
    action: str,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        KnowledgeGapEvent(
            tenant_id=context.tenant_id,
            gap_id=gap.gap_id,
            action=action,
            actor_id=context.user.user_id,
            from_status=from_status,
            to_status=to_status,
            detail=(detail or "")[:1000] or None,
        )
    )


def assign_gap(
    db: Session, context: AuthenticatedContext, gap_id: UUID, user_id: UUID
) -> KnowledgeGap:
    gap = get_gap(db, context, gap_id)
    from app.models import User

    exists = db.scalar(
        select(User.user_id).where(
            User.tenant_id == context.tenant_id, User.user_id == user_id
        )
    )
    if exists is None:
        raise NotFoundError("Assignee not found")
    gap.assigned_to = user_id
    gap.updated_at = datetime.now(timezone.utc)
    _event(db, context, gap, "ASSIGNED", detail="Gap assigned")
    db.flush()
    return gap


def change_status(
    db: Session,
    context: AuthenticatedContext,
    gap_id: UUID,
    status: str,
    notes: str | None = None,
) -> KnowledgeGap:
    gap = get_gap(db, context, gap_id)
    normalized = (status or "").strip().upper()
    if normalized not in GAP_STATUSES:
        raise BadRequestError("Unknown gap status")
    previous = gap.status
    gap.status = normalized
    gap.updated_at = datetime.now(timezone.utc)
    if notes is not None:
        gap.resolution_notes = (" ".join(notes.split())[:2000]) or None
    if normalized == "RESOLVED":
        gap.resolved_at = datetime.now(timezone.utc)
    _event(
        db,
        context,
        gap,
        "STATUS_CHANGED",
        from_status=previous,
        to_status=normalized,
        detail=notes,
    )
    db.flush()
    return gap


def re_evaluate(
    db: Session,
    context: AuthenticatedContext,
    gap_id: UUID,
    *,
    document_id: UUID | None = None,
) -> tuple[KnowledgeGap, dict]:
    """Run the normalized gap question through the real pipeline again."""
    from app.services.rag_service import answer_question

    gap = get_gap(db, context, gap_id)
    if document_id is not None:
        from app.models import Document

        document = db.scalar(
            select(Document).where(
                Document.tenant_id == context.tenant_id,
                Document.document_id == document_id,
            )
        )
        if document is None:
            raise NotFoundError("Resolution document not found")
        gap.resolution_document_id = document_id

    settings = get_settings()
    result = answer_question(
        context,
        gap.normalized_question,
        top_k=settings.rag_top_k_default,
        filters=None,
    )
    improved = result.retrieved_chunks > 0 and not result.insufficient_evidence
    gap.resolution_verified = improved
    gap.updated_at = datetime.now(timezone.utc)
    _event(
        db,
        context,
        gap,
        "RE_EVALUATED",
        detail=(
            f"chunks={result.retrieved_chunks};insufficient={result.insufficient_evidence}"
        ),
    )
    db.flush()
    return gap, {
        "answerable": improved,
        "retrieved_chunks": result.retrieved_chunks,
        "evidence_level": result.evidence_level,
        "insufficient_evidence": result.insufficient_evidence,
    }
