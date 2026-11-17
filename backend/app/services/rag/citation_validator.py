"""Server-side citation validation and claim coverage.

Source identifiers are validated against the exact authorized evidence set the
model received. Document names and page ranges always come from the database,
never from generated text.

This module also re-authorizes cited documents immediately before the response
is returned. That closes the window in which a permission could be revoked
while a slow local LLM was generating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security_constants import DOC_STATUS_COMPLETED
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document
from app.services.authorization import (
    authorize_loaded_document,
    load_role_ids,
)
from app.services.abac import load_user_attribute_map
from app.services.citation_service import CitationValidation, validate_citations
from app.services.context_builder import ContextSource

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CITATION_RE = re.compile(r"\[(?:S\d+(?:,\s*)?)+\]", re.IGNORECASE)
_MIN_CLAIM_WORDS = 5


@dataclass(frozen=True, slots=True)
class CitationAudit:
    validation: CitationValidation
    claim_count: int
    cited_claim_count: int
    coverage: float
    unsupported_claims: tuple[str, ...]

    @property
    def has_valid_citation(self) -> bool:
        return self.validation.has_valid_citation


def _claims(answer: str) -> list[str]:
    segments = [
        segment.strip()
        for segment in _SENTENCE_SPLIT_RE.split(answer.strip())
        if segment.strip()
    ]
    claims: list[str] = []
    for segment in segments:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", segment)
        if len(words) < _MIN_CLAIM_WORDS:
            continue
        if segment.endswith(":") and _CITATION_RE.search(segment) is None:
            # A lead-in such as "Sources:" is not a factual claim.
            continue
        claims.append(segment)
    return claims


def audit_answer(
    answer: str,
    context_sources: Sequence[ContextSource],
) -> CitationAudit:
    """Validate citations and measure how many factual sentences are cited."""
    validation = validate_citations(answer, context_sources)
    claims = _claims(validation.answer)
    cited = [claim for claim in claims if _CITATION_RE.search(claim)]
    coverage = (len(cited) / len(claims)) if claims else 0.0
    unsupported = tuple(claim for claim in claims if claim not in cited)
    return CitationAudit(
        validation=validation,
        claim_count=len(claims),
        cited_claim_count=len(cited),
        coverage=round(coverage, 4),
        unsupported_claims=unsupported[:20],
    )


def reauthorize_sources(
    db: Session,
    context: AuthenticatedContext,
    sources: Sequence[ContextSource],
    *,
    role_ids: frozenset[UUID] | None = None,
    raw_attributes: dict[str, str] | None = None,
) -> tuple[tuple[ContextSource, ...], tuple[UUID, ...]]:
    """Re-check every cited document against current authorization state."""
    if not sources:
        return (), ()

    resolved_role_ids = (
        role_ids
        if role_ids is not None
        else load_role_ids(db, context.tenant_id, context.roles)
    )
    attributes = (
        raw_attributes
        if raw_attributes is not None
        else load_user_attribute_map(db, context.tenant_id, context.user.user_id)
    )

    document_ids = {source.document_id for source in sources}
    documents = db.scalars(
        select(Document).where(
            Document.tenant_id == context.tenant_id,
            Document.document_id.in_(document_ids),
        )
    ).all()
    document_map = {document.document_id: document for document in documents}

    decisions: dict[UUID, bool] = {}
    for document_id in document_ids:
        document = document_map.get(document_id)
        if document is None or document.processing_status != DOC_STATUS_COMPLETED:
            decisions[document_id] = False
            continue
        decisions[document_id] = authorize_loaded_document(
            db,
            context,
            document,
            "READ",
            role_ids=resolved_role_ids,
            raw_attributes=attributes,
        ).allowed

    allowed = tuple(
        source for source in sources if decisions.get(source.document_id, False)
    )
    revoked = tuple(
        document_id
        for document_id, is_allowed in decisions.items()
        if not is_allowed
    )
    return allowed, revoked
