"""Strict schemas for answer feedback and review."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class FeedbackCreateRequest(StrictRequestModel):
    message_id: UUID
    category: str = Field(min_length=1, max_length=40)
    sentiment: str = Field(min_length=1, max_length=20)
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackUpdateRequest(StrictRequestModel):
    category: str | None = Field(default=None, max_length=40)
    sentiment: str | None = Field(default=None, max_length=20)
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackReviewRequest(StrictRequestModel):
    status: str = Field(min_length=1, max_length=20)
    category: str | None = Field(default=None, max_length=40)
    review_notes: str | None = Field(default=None, max_length=2000)


class FeedbackCitation(StrictRequestModel):
    document_id: UUID
    document_name: str
    page_start: int
    page_end: int
    authorized: bool = True


class FeedbackResponse(StrictRequestModel):
    feedback_id: UUID
    message_id: UUID
    conversation_id: UUID
    category: str
    sentiment: str
    comment: str | None = None
    review_status: str
    review_notes: str | None = None
    reviewer_id: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FeedbackDetailResponse(FeedbackResponse):
    question: str | None = None
    answer: str | None = None
    citations: list[FeedbackCitation] = Field(default_factory=list)
    hidden_citations: int = 0


class FeedbackListResponse(StrictRequestModel):
    feedback: list[FeedbackResponse] = Field(default_factory=list)
    total: int = 0


class FeedbackCategoryCount(StrictRequestModel):
    category: str
    count: int


class FeedbackStatusCount(StrictRequestModel):
    status: str
    count: int


class FeedbackDailyPoint(StrictRequestModel):
    date: datetime
    count: int


class FeedbackSummaryResponse(StrictRequestModel):
    days: int
    total: int
    positive: int
    negative: int
    positive_rate: float
    unreviewed: int
    by_category: list[FeedbackCategoryCount] = Field(default_factory=list)
    by_status: list[FeedbackStatusCount] = Field(default_factory=list)
    daily: list[FeedbackDailyPoint] = Field(default_factory=list)
