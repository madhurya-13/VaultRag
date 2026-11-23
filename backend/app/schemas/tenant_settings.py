"""Strict request/response schemas for tenant settings."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictRequestModel


class TenantSettingsUpdateRequest(StrictRequestModel):
    default_search_mode: str | None = Field(default=None, max_length=20)
    default_top_k: int | None = Field(default=None, ge=1, le=50)
    max_top_k: int | None = Field(default=None, ge=1, le=50)
    rerank_enabled: bool | None = None
    graph_enabled: bool | None = None
    semantic_candidates: int | None = Field(default=None, ge=1, le=100)
    keyword_candidates: int | None = Field(default=None, ge=1, le=100)
    hybrid_candidates: int | None = Field(default=None, ge=1, le=200)
    min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_strong_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_moderate_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_limited_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    feedback_enabled: bool | None = None
    analytics_retention_days: int | None = Field(default=None, ge=1, le=365)
    max_upload_size_mb: int | None = Field(default=None, ge=1, le=100)
    allowed_file_types: list[str] | None = None


class TenantSettingsResponse(StrictRequestModel):
    settings: dict
    version: int


class TenantSettingsHistoryItem(StrictRequestModel):
    version: int
    settings: dict
    changed_by: UUID
    changed_at: datetime


class TenantSettingsHistoryResponse(StrictRequestModel):
    history: list[TenantSettingsHistoryItem] = Field(default_factory=list)


class TenantSettingsRollbackRequest(StrictRequestModel):
    version: int = Field(ge=1)
