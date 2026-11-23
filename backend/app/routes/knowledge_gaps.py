"""Administrator knowledge-gap API. Tenant-scoped and capability-gated."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import CAP_MANAGE_KNOWLEDGE_GAPS, POLICY_ALLOW
from app.dependencies.authentication import AuthenticatedContext, require_capability
from app.models import KnowledgeGap, KnowledgeGapEvent
from app.schemas.knowledge_gap import (
    GapAssignRequest,
    GapDetailResponse,
    GapEventResponse,
    GapListResponse,
    GapReevaluateRequest,
    GapReevaluateResponse,
    GapResponse,
    GapStatusRequest,
    GapSummaryResponse,
)
from app.services import knowledge_gaps as service
from app.services.security_audit import log_security_event

router = APIRouter(prefix="/admin/knowledge-gaps", tags=["Knowledge Gaps"])
admin_only = require_capability(CAP_MANAGE_KNOWLEDGE_GAPS)


def _response(gap: KnowledgeGap) -> GapResponse:
    return GapResponse(
        gap_id=gap.gap_id,
        question=gap.normalized_question,
        topic_key=gap.topic_key,
        classification=gap.classification,
        severity=gap.severity,
        status=gap.status,
        occurrence_count=gap.occurrence_count,
        first_seen_at=gap.first_seen_at,
        last_seen_at=gap.last_seen_at,
        assigned_to=gap.assigned_to,
        resolution_notes=gap.resolution_notes,
        resolution_document_id=gap.resolution_document_id,
        resolution_verified=gap.resolution_verified,
        resolved_at=gap.resolved_at,
    )


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta,
    gap: KnowledgeGap,
    action: str,
) -> None:
    log_security_event(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user.user_id,
        resource_type="KNOWLEDGE_GAP",
        resource_id=gap.gap_id,
        action=action,
        decision="ALLOW",
        reason=f"status={gap.status};classification={gap.classification}",
        policy=POLICY_ALLOW,
        request_meta=request_meta,
    )


@router.get("", response_model=GapListResponse)
def list_gaps(
    status: Annotated[str | None, Query(max_length=20)] = None,
    classification: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> GapListResponse:
    gaps = service.list_gaps(
        db, context, status=status, classification=classification, limit=limit
    )
    return GapListResponse(gaps=[_response(gap) for gap in gaps], total=len(gaps))


@router.get("/summary", response_model=GapSummaryResponse)
def summary(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> GapSummaryResponse:
    return GapSummaryResponse(**service.summary(db, context, days=days))


@router.get("/{gap_id}", response_model=GapDetailResponse)
def detail(
    gap_id: UUID,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
) -> GapDetailResponse:
    gap = service.get_gap(db, context, gap_id)
    events = db.scalars(
        select(KnowledgeGapEvent)
        .where(
            KnowledgeGapEvent.tenant_id == context.tenant_id,
            KnowledgeGapEvent.gap_id == gap_id,
        )
        .order_by(KnowledgeGapEvent.created_at.desc())
        .limit(50)
    ).all()
    return GapDetailResponse(
        **_response(gap).model_dump(),
        events=[
            GapEventResponse(
                event_id=event.event_id,
                action=event.action,
                actor_id=event.actor_id,
                from_status=event.from_status,
                to_status=event.to_status,
                detail=event.detail,
                created_at=event.created_at,
            )
            for event in events
        ],
    )


@router.post("/{gap_id}/assign", response_model=GapResponse)
def assign(
    gap_id: UUID,
    payload: GapAssignRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> GapResponse:
    gap = service.assign_gap(db, context, gap_id, payload.user_id)
    _audit(db, context, request_meta, gap, "GAP_ASSIGN")
    return _response(gap)


@router.post("/{gap_id}/status", response_model=GapResponse)
def change_status(
    gap_id: UUID,
    payload: GapStatusRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> GapResponse:
    gap = service.change_status(db, context, gap_id, payload.status, payload.notes)
    _audit(db, context, request_meta, gap, "GAP_STATUS")
    return _response(gap)


@router.post("/{gap_id}/re-evaluate", response_model=GapReevaluateResponse)
def re_evaluate(
    gap_id: UUID,
    payload: GapReevaluateRequest,
    context: AuthenticatedContext = Depends(admin_only),
    db: Session = Depends(get_db),
    request_meta: RequestMeta = Depends(get_request_meta),
) -> GapReevaluateResponse:
    gap, result = service.re_evaluate(
        db, context, gap_id, document_id=payload.document_id
    )
    _audit(db, context, request_meta, gap, "GAP_RE_EVALUATE")
    return GapReevaluateResponse(gap=_response(gap), **result)
