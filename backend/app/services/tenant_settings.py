"""Tenant product settings: defaults, validation, versioning and rollback.

Security-critical policy is not configurable. This module only exposes
retrieval/product preferences that are already bounded by server settings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError, UnprocessableEntityError
from app.models import TenantSettings, TenantSettingsHistory

SEARCH_MODES = ("semantic", "keyword", "hybrid", "graph", "graph_hybrid")


def defaults() -> dict:
    server = get_settings()
    return {
        "default_search_mode": server.rag_search_mode_default,
        "default_top_k": server.rag_top_k_default,
        "max_top_k": server.rag_top_k_max,
        "rerank_enabled": server.rag_rerank_enabled,
        "graph_enabled": server.knowledge_graph_enabled,
        "semantic_candidates": server.rag_semantic_candidates,
        "keyword_candidates": server.rag_keyword_candidates,
        "hybrid_candidates": server.rag_hybrid_candidates,
        "min_similarity": server.rag_min_similarity,
        "evidence_strong_threshold": server.rag_evidence_strong_threshold,
        "evidence_moderate_threshold": server.rag_evidence_moderate_threshold,
        "evidence_limited_threshold": server.rag_evidence_limited_threshold,
        "feedback_enabled": True,
        "analytics_retention_days": 90,
        "max_upload_size_mb": max(1, server.max_upload_size_bytes // (1024 * 1024)),
        "allowed_file_types": ["PDF"],
    }


class TenantSettingsValues(BaseModel):
    default_search_mode: str = "hybrid"
    default_top_k: int = Field(default=10, ge=1, le=50)
    max_top_k: int = Field(default=50, ge=1, le=50)
    rerank_enabled: bool = False
    graph_enabled: bool = True
    semantic_candidates: int = Field(default=30, ge=1, le=100)
    keyword_candidates: int = Field(default=30, ge=1, le=100)
    hybrid_candidates: int = Field(default=40, ge=1, le=200)
    min_similarity: float = Field(default=0.20, ge=0.0, le=1.0)
    evidence_strong_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    evidence_moderate_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    evidence_limited_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    feedback_enabled: bool = True
    analytics_retention_days: int = Field(default=90, ge=1, le=365)
    max_upload_size_mb: int = Field(default=25, ge=1, le=100)
    allowed_file_types: list[str] = Field(default_factory=lambda: ["PDF"])

    @model_validator(mode="after")
    def _cross_field(self) -> "TenantSettingsValues":
        if self.default_search_mode not in SEARCH_MODES:
            raise ValueError("Unknown default search mode")
        if self.default_top_k > self.max_top_k:
            raise ValueError("default_top_k must not exceed max_top_k")
        if self.hybrid_candidates < max(self.semantic_candidates, self.keyword_candidates):
            raise ValueError("hybrid_candidates must be at least the per-retriever candidate count")
        if not (
            self.evidence_strong_threshold
            > self.evidence_moderate_threshold
            > self.evidence_limited_threshold
            >= 0
        ):
            raise ValueError("Evidence thresholds must satisfy strong > moderate > limited >= 0")
        if [item.upper() for item in self.allowed_file_types] != ["PDF"]:
            raise ValueError("Only PDF is supported by the ingestion pipeline")
        return self


def merged(db: Session, tenant_id: UUID) -> tuple[dict, int]:
    server_defaults = defaults()
    row = db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    if row is None:
        server_defaults["allowed_file_types"] = ["PDF"]
        return TenantSettingsValues(**server_defaults).model_dump(), 1
    payload = {**server_defaults, **(row.settings or {})}
    validated = TenantSettingsValues(**payload).model_dump()
    return validated, row.version


def get_for_rag(db: Session, tenant_id: UUID) -> dict:
    """Merged settings for request resolution. Failures fall back to defaults."""
    try:
        payload, _ = merged(db, tenant_id)
        return payload
    except Exception:
        return TenantSettingsValues(**defaults()).model_dump()


def update(db: Session, *, tenant_id: UUID, user_id: UUID, changes: dict) -> tuple[dict, int]:
    current, version = merged(db, tenant_id)
    payload = {**current, **{key: value for key, value in changes.items() if value is not None}}
    try:
        validated = TenantSettingsValues(**payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise UnprocessableEntityError(str(first.get("msg", "Invalid settings"))) from exc
    row = db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    if row is None:
        row = TenantSettings(
            tenant_id=tenant_id,
            settings=validated.model_dump(),
            version=2,
            updated_by=user_id,
        )
        db.add(row)
        db.flush()
        db.add(
            TenantSettingsHistory(
                tenant_id=tenant_id,
                version=1,
                settings=TenantSettingsValues(**defaults()).model_dump(),
                changed_by=user_id,
            )
        )
        db.flush()
    else:
        row.settings = validated.model_dump()
        row.version = version + 1
        row.updated_by = user_id
        row.updated_at = datetime.now(timezone.utc)
        db.flush()
    db.add(
        TenantSettingsHistory(
            tenant_id=tenant_id,
            version=row.version,
            settings=row.settings,
            changed_by=user_id,
        )
    )
    db.flush()
    return validated.model_dump(), row.version


def history(db: Session, tenant_id: UUID) -> list[TenantSettingsHistory]:
    return list(
        db.scalars(
            select(TenantSettingsHistory)
            .where(TenantSettingsHistory.tenant_id == tenant_id)
            .order_by(TenantSettingsHistory.version.desc())
            .limit(50)
        ).all()
    )


def rollback(
    db: Session, *, tenant_id: UUID, user_id: UUID, version: int
) -> tuple[dict, int]:
    target = db.scalar(
        select(TenantSettingsHistory).where(
            TenantSettingsHistory.tenant_id == tenant_id,
            TenantSettingsHistory.version == version,
        )
    )
    if target is None:
        raise NotFoundError("Settings version not found")
    try:
        validated = TenantSettingsValues(**{**defaults(), **(target.settings or {})})
    except ValidationError as exc:
        raise UnprocessableEntityError("Stored settings version is not valid") from exc
    row = db.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
    )
    if row is None:
        raise BadRequestError("No tenant settings exist")
    row.settings = validated.model_dump()
    row.version = row.version + 1
    row.updated_by = user_id
    row.updated_at = datetime.now(timezone.utc)
    db.add(
        TenantSettingsHistory(
            tenant_id=tenant_id,
            version=row.version,
            settings=row.settings,
            changed_by=user_id,
        )
    )
    db.flush()
    return row.settings, row.version
