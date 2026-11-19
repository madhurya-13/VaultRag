"""Evidence-gated graph visibility, built on the Milestone 2 engine.

The rule is one sentence long: *a graph fact is visible if and only if at least
one of its evidence rows points at a document the caller may read right now*.

Consequences that this module implements literally:

* Node visibility requires node evidence. A node mentioned only in a document the
  caller cannot read does not exist for that caller — not as a name, not as a
  count, not as a search suggestion.
* Edge visibility requires *edge* evidence. Two visible nodes do not imply a
  visible relationship.
* Descriptions and aliases are derived only from visible evidence.
* Nothing is cached across requests; a per-request cache exists so one document's
  decision is reused inside a single query, never beyond it.
* Chunk text is not fetched here at all. Callers fetch text only for chunk ids of
  documents that already passed authorization, using the existing
  ``fetch_chunk_texts`` helper.

Authorization goes through ``authorize_loaded_document``, so RBAC, exact-action
permissions, expiry, revocation, department, clearance and employment status keep
one single implementation shared with every other document path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.core.security_constants import (
    DOC_STATUS_COMPLETED,
    POLICY_INGESTION,
    POLICY_RESOURCE_NOT_FOUND,
)
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, KnowledgeEdge, KnowledgeEvidence, KnowledgeNode
from app.services.abac import load_user_attribute_map
from app.services.authorization import authorize_loaded_document, load_role_ids
from app.services.graph.types import VisibleEdge, VisibleEvidence, VisibleNode

logger = logging.getLogger(__name__)

MAX_DOCUMENTS_PER_LOOKUP = 1000


@dataclass
class GraphAuthorizationContext:
    """Per-request authorization state.

    ``document_decisions`` is intentionally request-scoped: caching a permission
    decision beyond one request is exactly the stale-permission bug this design
    avoids.
    """

    role_ids: frozenset[UUID]
    raw_attributes: dict[str, str]
    document_decisions: dict[UUID, bool] = field(default_factory=dict)
    document_names: dict[UUID, str] = field(default_factory=dict)
    denied_documents: int = 0


def load_graph_authorization_context(
    db: Session,
    context: AuthenticatedContext,
) -> GraphAuthorizationContext:
    """Load roles and ABAC attributes once for the whole graph query."""
    return GraphAuthorizationContext(
        role_ids=load_role_ids(db, context.tenant_id, context.roles),
        raw_attributes=load_user_attribute_map(
            db, context.tenant_id, context.user.user_id
        ),
    )


def authorize_document_ids(
    db: Session,
    context: AuthenticatedContext,
    auth: GraphAuthorizationContext,
    document_ids: set[UUID] | frozenset[UUID],
) -> frozenset[UUID]:
    """Authorize a batch of evidence documents, reusing per-request decisions."""
    pending = [
        document_id
        for document_id in document_ids
        if document_id not in auth.document_decisions
    ]
    if pending:
        # Bound the batch so one hostile graph cannot turn a single question into
        # an unbounded permission workload.
        batch = pending[:MAX_DOCUMENTS_PER_LOOKUP]
        documents = db.scalars(
            select(Document)
            .where(
                Document.tenant_id == context.tenant_id,
                Document.document_id.in_(batch),
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
        by_id = {document.document_id: document for document in documents}

        for document_id in batch:
            document = by_id.get(document_id)
            if document is None:
                auth.document_decisions[document_id] = False
                auth.denied_documents += 1
                logger.info(
                    "Graph evidence denied policy=%s document_id=%s",
                    POLICY_RESOURCE_NOT_FOUND,
                    document_id,
                )
                continue
            auth.document_names[document_id] = document.document_name
            if document.processing_status != DOC_STATUS_COMPLETED:
                # A document mid-reingestion may have its chunks replaced under
                # us; its graph claims are not yet stable.
                auth.document_decisions[document_id] = False
                auth.denied_documents += 1
                logger.info(
                    "Graph evidence denied policy=%s document_id=%s",
                    POLICY_INGESTION,
                    document_id,
                )
                continue
            decision = authorize_loaded_document(
                db,
                context,
                document,
                "READ",
                role_ids=auth.role_ids,
                raw_attributes=auth.raw_attributes,
            )
            auth.document_decisions[document_id] = decision.allowed
            if not decision.allowed:
                auth.denied_documents += 1
                logger.info(
                    "Graph evidence denied policy=%s document_id=%s",
                    decision.policy,
                    document_id,
                )
        # Any overflow document is treated as denied rather than assumed safe.
        for document_id in pending[MAX_DOCUMENTS_PER_LOOKUP:]:
            auth.document_decisions[document_id] = False
            auth.denied_documents += 1

    return frozenset(
        document_id
        for document_id in document_ids
        if auth.document_decisions.get(document_id, False)
    )


def document_id_of(evidence: KnowledgeEvidence) -> UUID:
    return evidence.document_id


def visible_evidence_rows(
    db: Session,
    context: AuthenticatedContext,
    auth: GraphAuthorizationContext,
    *,
    node_ids: list[UUID] | None = None,
    edge_ids: list[UUID] | None = None,
    limit: int,
) -> list[KnowledgeEvidence]:
    """Load valid evidence for the given subjects, keeping only authorized rows."""
    if not node_ids and not edge_ids:
        return []

    from sqlalchemy import or_

    conditions = []
    if node_ids:
        conditions.append(KnowledgeEvidence.node_id.in_(node_ids))
    if edge_ids:
        conditions.append(KnowledgeEvidence.edge_id.in_(edge_ids))

    rows = list(
        db.scalars(
            select(KnowledgeEvidence)
            .where(
                KnowledgeEvidence.tenant_id == context.tenant_id,
                KnowledgeEvidence.is_valid.is_(True),
                or_(*conditions),
            )
            .order_by(
                KnowledgeEvidence.extraction_confidence.desc(),
                KnowledgeEvidence.created_at.asc(),
                KnowledgeEvidence.evidence_id.asc(),
            )
            .limit(limit)
        ).all()
    )
    if not rows:
        return []

    allowed = authorize_document_ids(
        db, context, auth, {row.document_id for row in rows}
    )
    return [row for row in rows if row.document_id in allowed]


def chunk_page_map(
    db: Session, context: AuthenticatedContext, chunk_ids: list[UUID]
) -> dict[UUID, tuple[int, int]]:
    """Page attribution for already-authorized chunks (no text selected)."""
    if not chunk_ids:
        return {}
    from app.models import DocumentChunk

    rows = db.execute(
        select(
            DocumentChunk.chunk_id,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
        ).where(
            DocumentChunk.tenant_id == context.tenant_id,
            DocumentChunk.chunk_id.in_(chunk_ids),
        )
    ).all()
    return {row.chunk_id: (row.page_start, row.page_end) for row in rows}


def to_visible_evidence(
    rows: list[KnowledgeEvidence],
    *,
    pages: dict[UUID, tuple[int, int]],
    names: dict[UUID, str],
) -> list[VisibleEvidence]:
    visible: list[VisibleEvidence] = []
    for row in rows:
        page_start, page_end = pages.get(row.chunk_id, (1, 1))
        visible.append(
            VisibleEvidence(
                evidence_id=row.evidence_id,
                document_id=row.document_id,
                document_name=names.get(row.document_id, "Document"),
                chunk_id=row.chunk_id,
                page_start=page_start,
                page_end=page_end,
                quote=row.quote,
                confidence=row.extraction_confidence,
            )
        )
    return visible


def build_visible_node(
    node: KnowledgeNode, evidence: list[VisibleEvidence]
) -> VisibleNode:
    return VisibleNode(
        node_id=node.node_id,
        node_type=node.node_type,
        name=node.display_name,
        description=node.description,
        confidence=node.extraction_confidence,
        mention_count=node.mention_count,
        evidence=tuple(evidence),
    )


def build_visible_edge(
    edge: KnowledgeEdge, evidence: list[VisibleEvidence]
) -> VisibleEdge:
    return VisibleEdge(
        edge_id=edge.edge_id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        relation_type=edge.relation_type,
        confidence=edge.extraction_confidence,
        evidence=tuple(evidence),
    )
