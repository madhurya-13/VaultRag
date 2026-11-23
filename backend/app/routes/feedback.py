"""Answer feedback: owner-scoped submission plus administrator review."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_VIEW_FEEDBACK, POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext, get_current_auth, require_capability
from app.models import AnswerFeedback
from app.schemas.feedback import (
    FeedbackCitation,
    FeedbackCreateRequest,
    FeedbackDetailResponse,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackReviewRequest,
    FeedbackSummaryResponse,
    FeedbackUpdateRequest,
)
from app.services import feedback as service
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/feedback", tags=["Feedback"])
admin_router = APIRouter(prefix="/admin/feedback", tags=["Admin Feedback"])
admin_only = require_capability(CAP_VIEW_FEEDBACK)


def _response(item: AnswerFeedback) -> FeedbackResponse:
    return FeedbackResponse(
        feedback_id=item.feedback_id,
        message_id=item.message_id,
        conversation_id=item.conversation_id,
        category=item.category,
        sentiment=item.sentiment,
        comment=item.comment,
        review_status=item.review_status,
        review_notes=item.review_notes,
        reviewer_id=item.reviewer_id,
        reviewed_at=item.reviewed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    *,
    resource_id: UUID,
    action: str,
    reason: str,
) -> None:
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="ANSWER_FEEDBACK",
        resource_id=resource_id,
        action=action,
        decision="ALLOW",
        reason=reason[:500],
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )


@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def create_feedback(
    payload: FeedbackCreateRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> FeedbackResponse:
    from app.services.tenant_settings import get_for_rag

    if not get_for_rag(db, context.tenant_id).get("feedback_enabled", True):
        from app.core.exceptions import BadRequestError

        raise BadRequestError("Feedback is disabled for this organization")
    item, created = service.upsert_feedback(
        db,
        context,
        message_id=payload.message_id,
        category=payload.category,
        sentiment=payload.sentiment,
        comment=payload.comment,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=item.feedback_id,
        action="FEEDBACK_CREATE" if created else "FEEDBACK_UPDATE",
        reason=f"category={item.category};sentiment={item.sentiment}",
    )
    return _response(item)


@router.get("/mine", response_model=FeedbackListResponse)
def my_feedback(
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> FeedbackListResponse:
    rows = service.list_my_feedback(db, context, limit=limit)
    return FeedbackListResponse(feedback=[_response(row) for row in rows], total=len(rows))


@router.patch("/{feedback_id}", response_model=FeedbackResponse)
def update_feedback(
    feedback_id: UUID,
    payload: FeedbackUpdateRequest,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> FeedbackResponse:
    item = service.update_feedback(
        db,
        context,
        feedback_id,
        category=payload.category,
        sentiment=payload.sentiment,
        comment=payload.comment,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=item.feedback_id,
        action="FEEDBACK_UPDATE",
        reason=f"category={item.category};sentiment={item.sentiment}",
    )
    return _response(item)


@router.delete("/{feedback_id}", status_code=status.HTTP_204_NO_CONTENT)
def retract_feedback(
    feedback_id: UUID,
    context: AuthenticatedContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> Response:
    service.retract_feedback(db, context, feedback_id)
    _audit(
        db,
        context,
        request_meta,
        resource_id=feedback_id,
        action="FEEDBACK_RETRACT",
        reason="Feedback retracted by its owner",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("", response_model=FeedbackListResponse)
def list_feedback(
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    sentiment: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> FeedbackListResponse:
    rows = service.list_feedback(
        db,
        context,
        status=status_filter,
        category=category,
        sentiment=sentiment,
        limit=limit,
    )
    return FeedbackListResponse(feedback=[_response(row) for row in rows], total=len(rows))


@admin_router.get("/summary", response_model=FeedbackSummaryResponse)
def summary(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> FeedbackSummaryResponse:
    return FeedbackSummaryResponse(**service.feedback_summary(db, context, days=days))


@admin_router.get("/{feedback_id}", response_model=FeedbackDetailResponse)
def detail(
    feedback_id: UUID,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> FeedbackDetailResponse:
    item = db.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.tenant_id == context.tenant_id,
            AnswerFeedback.feedback_id == feedback_id,
            AnswerFeedback.retracted_at.is_(None),
        )
    )
    if item is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Feedback not found")
    review = service.review_context(db, context, item)
    return FeedbackDetailResponse(
        **_response(item).model_dump(),
        question=review["question"],
        answer=review["answer"],
        citations=[FeedbackCitation(**citation) for citation in review["citations"]],
        hidden_citations=review["hidden_citations"],
    )


@admin_router.post("/{feedback_id}/review", response_model=FeedbackResponse)
def review(
    feedback_id: UUID,
    payload: FeedbackReviewRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> FeedbackResponse:
    item = service.review_feedback(
        db,
        context,
        feedback_id,
        status=payload.status,
        category=payload.category,
        review_notes=payload.review_notes,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=item.feedback_id,
        action="FEEDBACK_REVIEW",
        reason=f"status={item.review_status};category={item.category}",
    )
    return _response(item)
