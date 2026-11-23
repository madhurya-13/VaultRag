"""Owner-scoped answer feedback and administrator review.

Feedback references an assistant message the caller owns. Review reads the
question and answer for context but never returns chunk text, and every
citation is re-authorized for the *reviewer* before it is shown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.dependencies.authentication import AuthenticatedContext
from app.models import AnswerFeedback, ConversationMessage
from app.models.feedback import (
    FEEDBACK_CATEGORIES,
    FEEDBACK_REVIEW_STATUSES,
    FEEDBACK_SENTIMENTS,
)
from app.services.authorization import authorize_document

MAX_COMMENT = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owned_assistant_message(
    db: Session, context: AuthenticatedContext, message_id: UUID
) -> ConversationMessage:
    message = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.tenant_id == context.tenant_id,
            ConversationMessage.user_id == context.user.user_id,
            ConversationMessage.message_id == message_id,
            ConversationMessage.role == "assistant",
            ConversationMessage.deleted_at.is_(None),
        )
    )
    if message is None:
        raise NotFoundError("Assistant message not found")
    return message


def _validate(category: str, sentiment: str, comment: str | None) -> tuple[str, str, str | None]:
    category = (category or "").strip().upper()
    sentiment = (sentiment or "").strip().upper()
    if category not in FEEDBACK_CATEGORIES:
        raise BadRequestError("Unknown feedback category")
    if sentiment not in FEEDBACK_SENTIMENTS:
        raise BadRequestError("Sentiment must be POSITIVE or NEGATIVE")
    cleaned = None
    if comment is not None:
        cleaned = " ".join(comment.split())[:MAX_COMMENT] or None
    return category, sentiment, cleaned


def upsert_feedback(
    db: Session,
    context: AuthenticatedContext,
    *,
    message_id: UUID,
    category: str,
    sentiment: str,
    comment: str | None,
) -> tuple[AnswerFeedback, bool]:
    """Create feedback or update the caller's active entry for that message."""
    category, sentiment, comment = _validate(category, sentiment, comment)
    message = _owned_assistant_message(db, context, message_id)

    existing = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.tenant_id == context.tenant_id,
            AnswerFeedback.user_id == context.user.user_id,
            AnswerFeedback.message_id == message_id,
            AnswerFeedback.retracted_at.is_(None),
        )
    )
    if existing is not None:
        existing.category = category
        existing.sentiment = sentiment
        existing.comment = comment
        existing.updated_at = _now()
        db.flush()
        return existing, False

    feedback = AnswerFeedback(
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        conversation_id=message.conversation_id,
        message_id=message_id,
        category=category,
        sentiment=sentiment,
        comment=comment,
    )
    db.add(feedback)
    db.flush()
    return feedback, True


def update_feedback(
    db: Session,
    context: AuthenticatedContext,
    feedback_id: UUID,
    *,
    category: str | None,
    sentiment: str | None,
    comment: str | None,
) -> AnswerFeedback:
    feedback = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.tenant_id == context.tenant_id,
            AnswerFeedback.user_id == context.user.user_id,
            AnswerFeedback.feedback_id == feedback_id,
            AnswerFeedback.retracted_at.is_(None),
        )
    )
    if feedback is None:
        raise NotFoundError("Feedback not found")
    category, sentiment, cleaned = _validate(
        category or feedback.category,
        sentiment or feedback.sentiment,
        comment if comment is not None else feedback.comment,
    )
    feedback.category = category
    feedback.sentiment = sentiment
    feedback.comment = cleaned
    feedback.updated_at = _now()
    db.flush()
    return feedback


def retract_feedback(
    db: Session, context: AuthenticatedContext, feedback_id: UUID
) -> AnswerFeedback:
    feedback = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.tenant_id == context.tenant_id,
            AnswerFeedback.user_id == context.user.user_id,
            AnswerFeedback.feedback_id == feedback_id,
            AnswerFeedback.retracted_at.is_(None),
        )
    )
    if feedback is None:
        raise NotFoundError("Feedback not found")
    feedback.retracted_at = _now()
    feedback.updated_at = _now()
    db.flush()
    return feedback


def list_my_feedback(
    db: Session, context: AuthenticatedContext, *, limit: int = 50
) -> list[AnswerFeedback]:
    return list(
        db.scalars(
            select(AnswerFeedback)
            .where(
                AnswerFeedback.tenant_id == context.tenant_id,
                AnswerFeedback.user_id == context.user.user_id,
                AnswerFeedback.retracted_at.is_(None),
            )
            .order_by(AnswerFeedback.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
    )


def list_feedback(
    db: Session,
    context: AuthenticatedContext,
    *,
    status: str | None = None,
    category: str | None = None,
    sentiment: str | None = None,
    limit: int = 100,
) -> list[AnswerFeedback]:
    statement = select(AnswerFeedback).where(
        AnswerFeedback.tenant_id == context.tenant_id,
        AnswerFeedback.retracted_at.is_(None),
    )
    if status:
        normalized = status.strip().upper()
        if normalized not in FEEDBACK_REVIEW_STATUSES:
            raise BadRequestError("Unknown review status")
        statement = statement.where(AnswerFeedback.review_status == normalized)
    if category:
        normalized = category.strip().upper()
        if normalized not in FEEDBACK_CATEGORIES:
            raise BadRequestError("Unknown feedback category")
        statement = statement.where(AnswerFeedback.category == normalized)
    if sentiment:
        normalized = sentiment.strip().upper()
        if normalized not in FEEDBACK_SENTIMENTS:
            raise BadRequestError("Unknown feedback sentiment")
        statement = statement.where(AnswerFeedback.sentiment == normalized)
    return list(
        db.scalars(
            statement.order_by(AnswerFeedback.created_at.desc()).limit(
                max(1, min(limit, 200))
            )
        ).all()
    )


def review_feedback(
    db: Session,
    context: AuthenticatedContext,
    feedback_id: UUID,
    *,
    status: str,
    category: str | None,
    review_notes: str | None,
) -> AnswerFeedback:
    feedback = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.tenant_id == context.tenant_id,
            AnswerFeedback.feedback_id == feedback_id,
            AnswerFeedback.retracted_at.is_(None),
        )
    )
    if feedback is None:
        raise NotFoundError("Feedback not found")
    status = (status or "").strip().upper()
    if status not in FEEDBACK_REVIEW_STATUSES:
        raise BadRequestError("Unknown review status")
    if category is not None:
        normalized = category.strip().upper()
        if normalized not in FEEDBACK_CATEGORIES:
            raise BadRequestError("Unknown feedback category")
        feedback.category = normalized
    if review_notes is not None:
        feedback.review_notes = (" ".join(review_notes.split())[:MAX_COMMENT]) or None
    feedback.review_status = status
    feedback.reviewer_id = context.user.user_id
    feedback.reviewed_at = _now()
    feedback.updated_at = _now()
    db.flush()
    return feedback


def feedback_summary(
    db: Session, context: AuthenticatedContext, *, days: int = 30
) -> dict:
    bounded = max(1, min(days, 365))
    since = _now() - timedelta(days=bounded)
    base = (
        AnswerFeedback.tenant_id == context.tenant_id,
        AnswerFeedback.retracted_at.is_(None),
        AnswerFeedback.created_at >= since,
    )
    total = int(
        db.scalar(select(func.count()).select_from(AnswerFeedback).where(*base)) or 0
    )
    by_category = db.execute(
        select(AnswerFeedback.category, func.count())
        .where(*base)
        .group_by(AnswerFeedback.category)
        .order_by(func.count().desc())
    ).all()
    by_status = db.execute(
        select(AnswerFeedback.review_status, func.count())
        .where(*base)
        .group_by(AnswerFeedback.review_status)
    ).all()
    positive = int(
        db.scalar(
            select(func.count())
            .select_from(AnswerFeedback)
            .where(*base, AnswerFeedback.sentiment == "POSITIVE")
        )
        or 0
    )
    day_expr = func.date_trunc("day", AnswerFeedback.created_at)
    daily = db.execute(
        select(day_expr, func.count())
        .where(*base)
        .group_by(day_expr)
        .order_by(day_expr)
    ).all()
    return {
        "days": bounded,
        "total": total,
        "positive": positive,
        "negative": total - positive,
        "positive_rate": round(positive / total, 4) if total else 0.0,
        "unreviewed": sum(
            int(count) for status, count in by_status if status == "UNREVIEWED"
        ),
        "by_category": [
            {"category": category, "count": int(count)}
            for category, count in by_category
        ],
        "by_status": [
            {"status": status, "count": int(count)} for status, count in by_status
        ],
        "daily": [
            {"date": day, "count": int(count)} for day, count in daily
        ],
    }


def review_context(
    db: Session, context: AuthenticatedContext, feedback: AnswerFeedback
) -> dict:
    """Question, answer and citations currently readable by the reviewer."""
    assistant = db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.tenant_id == context.tenant_id,
            ConversationMessage.message_id == feedback.message_id,
        )
    )
    if assistant is None:
        raise NotFoundError("Feedback message not found")
    question = db.scalar(
        select(ConversationMessage.content)
        .where(
            ConversationMessage.tenant_id == context.tenant_id,
            ConversationMessage.conversation_id == assistant.conversation_id,
            ConversationMessage.role == "user",
            ConversationMessage.deleted_at.is_(None),
            ConversationMessage.created_at <= assistant.created_at,
        )
        .order_by(ConversationMessage.created_at.desc())
        .limit(1)
    )
    citations: list[dict] = []
    for item in assistant.citations or []:
        try:
            document_id = UUID(str(item["document_id"]))
            page_start = int(item["page_start"])
            page_end = int(item["page_end"])
        except (KeyError, TypeError, ValueError):
            continue
        allowed = authorize_document(db, context, document_id, "READ").allowed
        if not allowed:
            continue
        citations.append(
            {
                "document_id": str(document_id),
                "document_name": str(item.get("document_name") or "Document"),
                "page_start": page_start,
                "page_end": page_end,
                "authorized": True,
            }
        )
    return {
        "question": question,
        "answer": assistant.content,
        "citations": citations,
        "hidden_citations": len(assistant.citations or []) - len(citations),
    }
