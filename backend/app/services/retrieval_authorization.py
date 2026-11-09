"""Current-permission authorization for retrieved candidates.

Nothing in this module trusts ``document_chunks.metadata``. That JSONB object is
an ingestion-time snapshot and can be stale after a permission revocation.

The stages are intentionally ordered:

1. candidate metadata arrives without text;
2. document metadata is loaded with column pruning;
3. the central Milestone 2 authorization engine evaluates current RBAC,
   current document permissions, expiry/revocation and ABAC;
4. only then may the caller fetch text for the surviving chunk IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.request_context import RequestMeta
from app.core.security_constants import (
    DOC_STATUS_COMPLETED,
    POLICY_INGESTION,
    POLICY_RESOURCE_NOT_FOUND,
    POLICY_TENANT_ISOLATION,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document
from app.services.abac import load_user_attribute_map
from app.services.authorization import (
    AuthorizationDecision,
    authorize_loaded_document,
    load_role_ids,
)
from app.services.security_audit import log_security_event
from app.services.vector_retrieval import CandidateChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalAuthorizationContext:
    role_ids: frozenset[UUID]
    raw_attributes: dict[str, str]


@dataclass(frozen=True, slots=True)
class AuthorizedChunk:
    chunk_id: UUID
    tenant_id: UUID
    document_id: UUID
    chunk_index: int
    page_start: int
    page_end: int
    distance: float
    document_name: str
    text: str


@dataclass(frozen=True, slots=True)
class AuthorizationFilterResult:
    authorized: tuple[CandidateChunk, ...]
    denied: tuple[AuthorizationDecision, ...]


def load_retrieval_authorization_context(
    db: Session,
    context: AuthenticatedContext,
) -> RetrievalAuthorizationContext:
    """Load roles and ABAC attributes once for the whole retrieval request."""
    return RetrievalAuthorizationContext(
        role_ids=load_role_ids(db, context.tenant_id, context.roles),
        raw_attributes=load_user_attribute_map(
            db, context.tenant_id, context.user.user_id
        ),
    )


def _deny(document_id: UUID, policy: str, reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        action="READ",
        document_id=document_id,
        policy=policy,
        reason=reason,
    )


def _record_denial(
    context: AuthenticatedContext,
    decision: AuthorizationDecision,
    request_meta: RequestMeta | None,
) -> None:
    """Record a denied document without exposing text or chunk content."""
    try:
        log_security_event(
            None,
            tenant_id=context.tenant_id,
            user_id=context.user.user_id,
            resource_type="DOCUMENT",
            resource_id=decision.document_id,
            action="RETRIEVAL_READ",
            decision="DENY",
            reason=decision.reason,
            policy=decision.policy,
            request_meta=request_meta,
        )
    except Exception:
        # Audit failure must not be silently swallowed in normal operation, but
        # it also must not turn a denied candidate into an allowed one. The
        # caller still drops the candidate.
        logger.exception(
            "Failed to record retrieval denial document_id=%s",
            decision.document_id,
        )


def filter_authorized_chunks(
    db: Session,
    context: AuthenticatedContext,
    candidates: list[CandidateChunk],
    *,
    model_label: str,
    authorization_context: RetrievalAuthorizationContext | None = None,
    request_meta: RequestMeta | None = None,
) -> AuthorizationFilterResult:
    """Authorize candidate documents using only current database state."""
    if not candidates:
        return AuthorizationFilterResult(authorized=(), denied=())

    auth_context = authorization_context or load_retrieval_authorization_context(
        db, context
    )

    document_ids = {candidate.document_id for candidate in candidates}
    documents = db.scalars(
        select(Document)
        .where(
            Document.tenant_id == context.tenant_id,
            Document.document_id.in_(document_ids),
        )
        .options(
            load_only(
                Document.document_id,
                Document.tenant_id,
                Document.document_name,
                Document.department,
                Document.classification,
                Document.required_clearance_level,
                Document.is_active,
                Document.processing_status,
                Document.embedding_model,
            )
        )
    ).all()
    document_map = {document.document_id: document for document in documents}

    decisions: dict[UUID, AuthorizationDecision] = {}
    for document_id in document_ids:
        document = document_map.get(document_id)
        if document is None:
            decision = _deny(
                document_id,
                POLICY_RESOURCE_NOT_FOUND,
                "Document not found in this tenant",
            )
        elif document.tenant_id != context.tenant_id:
            decision = _deny(
                document_id,
                POLICY_TENANT_ISOLATION,
                "Document belongs to a different tenant",
            )
        elif not document.is_active:
            decision = _deny(
                document_id,
                POLICY_INGESTION,
                "Document is inactive",
            )
        elif document.processing_status != DOC_STATUS_COMPLETED:
            decision = _deny(
                document_id,
                POLICY_INGESTION,
                "Document is not fully processed",
            )
        elif document.embedding_model != model_label:
            decision = _deny(
                document_id,
                POLICY_INGESTION,
                "Document embedding model does not match the query model",
            )
        else:
            decision = authorize_loaded_document(
                db,
                context,
                document,
                "READ",
                role_ids=auth_context.role_ids,
                raw_attributes=auth_context.raw_attributes,
            )

        decisions[document_id] = decision
        if not decision.allowed:
            _record_denial(context, decision, request_meta)

    authorized = tuple(
        candidate
        for candidate in candidates
        if decisions.get(candidate.document_id) is not None
        and decisions[candidate.document_id].allowed
    )
    denied = tuple(
        decision for decision in decisions.values() if not decision.allowed
    )
    return AuthorizationFilterResult(authorized=authorized, denied=denied)
