"""One shared set of SQL predicates for every retriever.

Semantic and keyword search must not maintain separate copies of the tenant,
document-state, embedding-model or permission rules. A drift between the two
would turn keyword search into an authorization bypass, so both retrievers
build their WHERE clause from this module.

The predicate stage never selects or returns chunk text. Text is fetched later,
only for chunk IDs that survived current authorization.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.core.security_constants import (
    DOC_STATUS_COMPLETED,
    PERMISSION_READ,
    ROLE_ADMIN,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk, DocumentPermission
from app.schemas.rag import RAGFilters
from app.services.rag.retrieval_filters import to_predicates


def document_predicates(
    context: AuthenticatedContext,
    *,
    model_label: str,
    filters: RAGFilters | None = None,
) -> list[ColumnElement[bool]]:
    """Tenant, document-state, model and (optional) metadata predicates.

    ``embedding_model`` is required in every mode. Keyword search therefore
    operates on the same processed corpus as semantic search instead of
    creating a second, differently-versioned population of chunks.
    """
    predicates: list[ColumnElement[bool]] = [
        DocumentChunk.tenant_id == context.tenant_id,
        Document.tenant_id == context.tenant_id,
        Document.is_active.is_(True),
        Document.processing_status == DOC_STATUS_COMPLETED,
        Document.embedding_model == model_label,
    ]
    predicates.extend(to_predicates(filters))
    return predicates


def permission_predicate(
    context: AuthenticatedContext,
    role_ids: frozenset[UUID],
) -> ColumnElement[bool] | None:
    """SQL prefilter for an active, unexpired READ grant.

    ADMIN keeps its existing implicit tenant-wide capability. Everyone else
    needs an explicit grant for their user ID or one of their current roles.
    This is only a prefilter: the central engine re-evaluates RBAC, expiry,
    revocation and ABAC for each surviving document.
    """
    if ROLE_ADMIN in context.roles:
        return None

    subject_conditions = [DocumentPermission.user_id == context.user.user_id]
    if role_ids:
        subject_conditions.append(DocumentPermission.role_id.in_(list(role_ids)))

    return exists(
        select(1)
        .select_from(DocumentPermission)
        .where(
            DocumentPermission.tenant_id == DocumentChunk.tenant_id,
            DocumentPermission.document_id == DocumentChunk.document_id,
            DocumentPermission.permission_type == PERMISSION_READ,
            DocumentPermission.is_active.is_(True),
            or_(
                DocumentPermission.expires_at.is_(None),
                DocumentPermission.expires_at > func.now(),
            ),
            or_(*subject_conditions),
        )
    )


def candidate_where_clause(
    context: AuthenticatedContext,
    *,
    model_label: str,
    role_ids: frozenset[UUID],
    filters: RAGFilters | None = None,
) -> list[ColumnElement[bool]]:
    """Full WHERE list shared by semantic and keyword retrieval."""
    predicates = document_predicates(
        context, model_label=model_label, filters=filters
    )
    permission = permission_predicate(context, role_ids)
    if permission is not None:
        predicates.append(permission)
    return predicates


def document_join_condition():
    """Tenant-safe join from chunk to parent document."""
    return and_(
        Document.tenant_id == DocumentChunk.tenant_id,
        Document.document_id == DocumentChunk.document_id,
    )
