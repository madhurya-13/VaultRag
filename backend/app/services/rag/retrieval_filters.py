"""Validated metadata filters as SQL narrowing predicates.

Filters are *narrowing only*. They are applied next to tenant, document-state,
embedding-model and permission predicates, and the central authorization
engine still re-evaluates every surviving document. A filter can never widen
access, and it is never derived from model output.

The pydantic model lives in ``app.schemas.rag`` so the API rejects unknown keys
with a 422 before any query runs. This module converts a validated instance
into SQLAlchemy predicates.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.sql.elements import ColumnElement

from app.models import Document
from app.schemas.rag import RAGFilters


def to_predicates(filters: RAGFilters | None) -> list[ColumnElement[bool]]:
    """Translate validated filters into ``Document`` predicates."""
    if filters is None:
        return []

    predicates: list[ColumnElement[bool]] = []
    if filters.department is not None:
        predicates.append(Document.department == filters.department)
    if filters.document_type is not None:
        predicates.append(Document.document_type == filters.document_type)
    if filters.classification:
        predicates.append(Document.classification.in_(list(filters.classification)))
    if filters.status is not None:
        predicates.append(Document.processing_status == filters.status)
    if filters.uploaded_by is not None:
        predicates.append(Document.uploaded_by == filters.uploaded_by)
    if filters.date_from is not None:
        predicates.append(Document.created_at >= filters.date_from)
    if filters.date_to is not None:
        predicates.append(Document.created_at <= filters.date_to)

    return predicates


def describe_filters(filters: RAGFilters | None) -> dict[str, Any]:
    """Return non-sensitive filter metadata for audit reasons and analytics.

    Only the filter *keys* that were applied are reported. Values such as an
    uploader UUID are deliberately omitted from logs.
    """
    if filters is None:
        return {}
    return {
        key: True
        for key, value in filters.model_dump(exclude_none=True).items()
        if value not in (None, [], "")
    }
