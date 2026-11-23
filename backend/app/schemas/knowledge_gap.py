"""Schemas for the administrator knowledge-gap workflow."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class GapClassificationCount(StrictRequestModel):
    classification: str
    count: int


class GapStatusCount(StrictRequestModel):
    status: str
    count: int


class GapSummaryResponse(StrictRequestModel):
    days: int
    total_queries: int
    total_signals: int
    visible_gaps: int
    unresolved_gaps: int
    by_classification: list[GapClassificationCount] = Field(default_factory=list)
    by_status: list[GapStatusCount] = Field(default_factory=list)
    minimum_group_size: int
    retention_days: int


class GapResponse(StrictRequestModel):
    gap_id: UUID
    question: str
    topic_key: str
    classification: str
    severity: str
    status: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    assigned_to: UUID | None = None
    resolution_notes: str | None = None
    resolution_document_id: UUID | None = None
    resolution_verified: bool | None = None
    resolved_at: datetime | None = None


class GapListResponse(StrictRequestModel):
    gaps: list[GapResponse] = Field(default_factory=list)
    total: int = 0


class GapEventResponse(StrictRequestModel):
    event_id: UUID
    action: str
    actor_id: UUID | None = None
    from_status: str | None = None
    to_status: str | None = None
    detail: str | None = None
    created_at: datetime


class GapDetailResponse(GapResponse):
    events: list[GapEventResponse] = Field(default_factory=list)


class GapAssignRequest(StrictRequestModel):
    user_id: UUID


class GapStatusRequest(StrictRequestModel):
    status: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class GapReevaluateRequest(StrictRequestModel):
    document_id: UUID | None = None


class GapReevaluateResponse(StrictRequestModel):
    gap: GapResponse
    answerable: bool
    retrieved_chunks: int
    evidence_level: str
    insufficient_evidence: bool
