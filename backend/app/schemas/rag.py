"""Schemas for advanced permission-aware RAG requests and responses.

Backward compatibility
----------------------
The Milestone 4 payload (``question`` plus ``top_k``) is still valid. Every new
field is optional. Responses only gain keys; existing keys keep their meaning.

Security note
-------------
Tenant identity, roles, clearance and permission decisions are never accepted
from the client. ``StrictRequestModel`` rejects unknown fields, including
nested filter keys, so a caller cannot smuggle ``tenant_id`` through a filter
object.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.security_constants import (
    ALLOWED_DEPARTMENTS,
    CLASSIFICATIONS,
    DOCUMENT_STATUSES,
)
from app.schemas.common import StrictRequestModel

SearchMode = Literal["semantic", "keyword", "hybrid"]
EvidenceLevel = Literal["strong", "moderate", "limited", "insufficient"]

_DOCUMENT_TYPE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _./&()+-]{0,99}$"


class RAGFilters(StrictRequestModel):
    """Authorized metadata narrowing. Never an authorization input.

    Every value is validated against the same controlled vocabulary the
    document API uses. Filters are applied as additional SQL predicates next to
    tenant and permission rules; they cannot widen access.
    """

    department: str | None = Field(default=None, max_length=50)
    document_type: str | None = Field(
        default=None, max_length=100, pattern=_DOCUMENT_TYPE_PATTERN
    )
    classification: str | list[str] | None = None
    status: str | None = Field(default=None, max_length=20)
    uploaded_by: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("department")
    @classmethod
    def _validate_department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in ALLOWED_DEPARTMENTS:
            raise ValueError("Unknown department filter")
        return normalized

    @field_validator("document_type")
    @classmethod
    def _validate_document_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("document_type filter cannot be blank")
        return normalized

    @field_validator("classification")
    @classmethod
    def _validate_classification(
        cls, value: str | list[str] | None
    ) -> list[str] | None:
        if value is None:
            return None
        values = [value] if isinstance(value, str) else list(value)
        if not values:
            raise ValueError("classification filter cannot be empty")
        if len(values) > len(CLASSIFICATIONS):
            raise ValueError("Too many classification values")
        normalized: list[str] = []
        for item in values:
            candidate = str(item).strip().upper()
            if candidate not in CLASSIFICATIONS:
                raise ValueError("Unknown classification filter")
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in DOCUMENT_STATUSES:
            raise ValueError("Unknown document status filter")
        return normalized

    @model_validator(mode="after")
    def _validate_dates(self) -> "RAGFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class RAGAskRequest(StrictRequestModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=50)
    search_mode: SearchMode | None = None
    filters: RAGFilters | None = None
    conversation_id: UUID | None = None


class RAGSource(StrictRequestModel):
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    excerpt: str | None = None
    similarity: float | None = None
    rerank_score: float | None = None
    matched_by: list[str] = Field(default_factory=list)


class RAGSourceGroup(StrictRequestModel):
    document_id: UUID
    document_name: str
    sources: list[RAGSource] = Field(default_factory=list)


class RAGEvidence(StrictRequestModel):
    level: EvidenceLevel
    score: float = Field(ge=0.0, le=1.0)
    signals: dict[str, float | int | str | bool] = Field(default_factory=dict)


class RAGConflict(StrictRequestModel):
    topic: str
    description: str
    values: list[str] = Field(default_factory=list)
    sources: list[RAGSource] = Field(default_factory=list)


class RAGTimings(StrictRequestModel):
    retrieval_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0


class RAGAskResponse(StrictRequestModel):
    answer: str
    sources: list[RAGSource] = Field(default_factory=list)
    source_groups: list[RAGSourceGroup] = Field(default_factory=list)
    retrieved_chunks: int = 0
    model: str | None = None
    search_mode: SearchMode = "hybrid"
    evidence: RAGEvidence | None = None
    conflicts: list[RAGConflict] = Field(default_factory=list)
    insufficient_evidence: bool = False
    timings: RAGTimings = Field(default_factory=RAGTimings)
    conversation_id: UUID | None = None
    message_id: UUID | None = None


class RAGHealthComponent(StrictRequestModel):
    status: str
    detail: str | None = None


class RAGHealthResponse(StrictRequestModel):
    status: str
    components: dict[str, RAGHealthComponent]


class RAGHistoryItem(StrictRequestModel):
    question: str | None = None
    answer: str | None = None
    sources: list[RAGSource] = Field(default_factory=list)
    conversation_id: UUID | None = None
    evidence_level: EvidenceLevel | None = None
    created_at: datetime | None = None


class RAGHistoryResponse(StrictRequestModel):
    items: list[RAGHistoryItem] = Field(default_factory=list)
    persistent: bool = False


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class ConversationCreateRequest(StrictRequestModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationRenameRequest(StrictRequestModel):
    title: str = Field(min_length=1, max_length=120)


class ConversationResponse(StrictRequestModel):
    conversation_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationListResponse(StrictRequestModel):
    conversations: list[ConversationResponse] = Field(default_factory=list)


class ConversationCitation(StrictRequestModel):
    document_id: UUID
    chunk_id: UUID
    page_start: int
    page_end: int
    document_name: str | None = None


class ConversationMessageResponse(StrictRequestModel):
    message_id: UUID
    role: str
    content: str
    citations: list[ConversationCitation] = Field(default_factory=list)
    evidence_level: EvidenceLevel | None = None
    created_at: datetime


class ConversationDetailResponse(StrictRequestModel):
    conversation: ConversationResponse
    messages: list[ConversationMessageResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Retrieval analytics (tenant-scoped, ADMIN capability)
# ---------------------------------------------------------------------------


class RetrievalModeCount(StrictRequestModel):
    mode: str
    count: int


class RetrievalDailyPoint(StrictRequestModel):
    date: datetime
    queries: int
    errors: int


class RetrievalAnalyticsResponse(StrictRequestModel):
    days: int
    since: datetime
    total_queries: int
    by_search_mode: list[RetrievalModeCount] = Field(default_factory=list)
    average_retrieval_ms: float = 0.0
    average_total_ms: float = 0.0
    p95_total_ms: float = 0.0
    citation_validation_rate: float = 0.0
    insufficient_evidence_rate: float = 0.0
    retrieval_error_rate: float = 0.0
    daily: list[RetrievalDailyPoint] = Field(default_factory=list)
