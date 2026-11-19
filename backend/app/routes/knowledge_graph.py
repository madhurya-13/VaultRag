"""Knowledge graph API.

Read endpoints are evidence-gated: they use the same per-request document
authorization as retrieval, so nothing is returned for a document the caller
cannot read. Write endpoints require ``MANAGE_KNOWLEDGE_GRAPH`` (ADMIN).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.core.request_context import RequestMeta, get_request_meta
from app.core.security_constants import (
    CAP_MANAGE_KNOWLEDGE_GRAPH,
    CONFLICT_STATUS_OPEN,
    POLICY_ALLOW,
    POLICY_GRAPH_EVIDENCE,
)
from app.dependencies.authentication import (
    AuthenticatedContext,
    get_current_auth,
    require_capability,
)
from app.models import KnowledgeConflict, KnowledgeNode
from app.schemas.knowledge_graph import (
    KGAnalyticsResponse,
    KGConflict,
    KGConflictListResponse,
    KGConflictValue,
    KGEdge,
    KGEvidence,
    KGFinding,
    KGGovernanceResponse,
    KGMergeRequest,
    KGNode,
    KGNodeDetailResponse,
    KGQueryRequest,
    KGQueryResponse,
    KGReprocessResponse,
    KGReviewDecisionRequest,
    KGSplitRequest,
)
from app.services.embedding_service import get_embedding_service
from app.services.graph.analytics import (
    graph_analytics_summary,
    record_graph_query,
)
from app.services.graph.authorization import (
    authorize_document_ids,
    build_visible_node,
    chunk_page_map,
    load_graph_authorization_context,
    to_visible_evidence,
    visible_evidence_rows,
)
from app.services.graph.governance import decide_review, merge_nodes, split_alias
from app.services.graph.jobs import enqueue_graph_job
from app.services.graph.retrieval import retrieve_graph
from app.services.graph.types import (
    GraphFinding,
    VisibleEdge,
    VisibleEvidence,
    VisibleNode,
)
from app.services.security_audit import log_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-graph", tags=["Knowledge Graph"])


def _evidence(item: VisibleEvidence) -> KGEvidence:
    return KGEvidence(
        evidence_id=item.evidence_id,
        document_id=item.document_id,
        document_name=item.document_name,
        chunk_id=item.chunk_id,
        page_start=item.page_start,
        page_end=item.page_end,
        quote=item.quote,
        confidence=item.confidence,
    )


def _node(item: VisibleNode) -> KGNode:
    return KGNode(
        node_id=item.node_id,
        node_type=item.node_type,
        name=item.name,
        description=item.description,
        confidence=item.confidence,
        mention_count=item.mention_count,
        evidence=[_evidence(evidence) for evidence in item.evidence],
    )


def _edge(item: VisibleEdge) -> KGEdge:
    return KGEdge(
        edge_id=item.edge_id,
        source_node_id=item.source_node_id,
        target_node_id=item.target_node_id,
        relation_type=item.relation_type,
        confidence=item.confidence,
        evidence=[_evidence(evidence) for evidence in item.evidence],
    )


def _finding(item: GraphFinding) -> KGFinding:
    return KGFinding(
        subject=item.subject,
        relation=item.relation,
        object=item.object,
        quote=item.quote,
        document_id=item.document_id,
        document_name=item.document_name,
        chunk_id=item.chunk_id,
        page_start=item.page_start,
        page_end=item.page_end,
        confidence=item.confidence,
    )


def _require_enabled() -> None:
    if not get_settings().knowledge_graph_enabled:
        raise ServiceUnavailableError("The knowledge graph is not enabled")


def _audit(
    db: Session,
    context: AuthenticatedContext,
    request_meta: RequestMeta | None,
    *,
    resource_id: UUID,
    action: str,
    reason: str,
    policy: str = POLICY_ALLOW,
) -> None:
    """Audit graph access without storing names, quotes or question text."""
    try:
        log_security_event(
            db,
            tenant_id=context.tenant_id,
            user_id=context.user.user_id,
            resource_type="KNOWLEDGE_GRAPH",
            resource_id=resource_id,
            action=action,
            decision="ALLOW",
            reason=reason,
            policy=policy,
            request_meta=request_meta,
        )
    except Exception:
        logger.exception("Failed to audit knowledge graph access")


@router.post("/query", response_model=KGQueryResponse)
def query_graph(
    payload: KGQueryRequest,
    context: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    request_meta: Annotated[RequestMeta | None, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> KGQueryResponse:
    """Bounded, evidence-gated subgraph for one question."""
    _require_enabled()
    auth = load_graph_authorization_context(db, context)
    result = retrieve_graph(
        db,
        context,
        question=payload.question,
        auth=auth,
        model_label=get_embedding_service().model_label,
        max_depth=payload.max_depth,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=uuid4(),
        action="GRAPH_QUERY",
        reason=(
            f"mode={payload.search_mode};seeds={result.seed_count};"
            f"nodes={len(result.snapshot.nodes)};edges={len(result.snapshot.edges)};"
            f"denied={result.denied_documents};truncated={result.truncated}"
        ),
        policy=POLICY_GRAPH_EVIDENCE,
    )
    if payload.search_mode in {"graph", "graph_hybrid"}:
        record_graph_query(
            db,
            tenant_id=context.tenant_id,
            user_id=context.user.user_id,
            search_mode=payload.search_mode,
            seed_count=result.seed_count,
            node_count=len(result.snapshot.nodes),
            edge_count=len(result.snapshot.edges),
            evidence_count=len(result.snapshot.evidence_chunk_ids()),
            denied_documents=result.denied_documents,
            traversal_ms=result.traversal_ms,
            total_ms=result.traversal_ms,
        )

    return KGQueryResponse(
        search_mode=payload.search_mode,
        seed_count=result.seed_count,
        denied_documents=result.denied_documents,
        truncated=result.truncated,
        depth=result.snapshot.depth,
        traversal_ms=result.traversal_ms,
        nodes=[_node(item) for item in result.snapshot.nodes],
        edges=[_edge(item) for item in result.snapshot.edges],
        findings=(
            [_finding(item) for item in result.findings]
            if payload.include_findings
            else []
        ),
        candidate_chunks=len(result.candidates),
    )


@router.get("/nodes/{node_id}", response_model=KGNodeDetailResponse)
def node_detail(
    node_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
) -> KGNodeDetailResponse:
    """One node with only the evidence the caller may currently read."""
    _require_enabled()
    auth = load_graph_authorization_context(db, context)
    node = db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == context.tenant_id,
            KnowledgeNode.node_id == node_id,
        )
    )
    if node is None:
        # Do not distinguish "missing" from "not visible to you".
        raise NotFoundError("Knowledge graph node not found")

    rows = visible_evidence_rows(
        db,
        context,
        auth,
        node_ids=[node_id],
        limit=get_settings().knowledge_graph_max_evidence,
    )
    if not rows:
        raise NotFoundError("Knowledge graph node not found")
    pages = chunk_page_map(db, context, [row.chunk_id for row in rows])
    visible = build_visible_node(
        node, to_visible_evidence(rows, pages=pages, names=auth.document_names)
    )
    return KGNodeDetailResponse(node=_node(visible))


@router.get("/conflicts", response_model=KGConflictListResponse)
def list_conflicts(
    context: Annotated[AuthenticatedContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> KGConflictListResponse:
    """Open conflicts whose values are visible to this caller.

    A conflict is only returned when at least two of its values still come from
    documents the caller can read; otherwise it would leak a document name or a
    value from an unauthorized source.
    """
    _require_enabled()
    auth = load_graph_authorization_context(db, context)
    rows = db.scalars(
        select(KnowledgeConflict)
        .where(
            KnowledgeConflict.tenant_id == context.tenant_id,
            KnowledgeConflict.status == CONFLICT_STATUS_OPEN,
        )
        .order_by(KnowledgeConflict.detected_at.desc())
        .limit(limit)
    ).all()

    document_ids: set[UUID] = set()
    for row in rows:
        for value in row.values or []:
            try:
                document_ids.add(UUID(str(value.get("document_id"))))
            except (TypeError, ValueError):
                continue
    allowed = authorize_document_ids(db, context, auth, document_ids)

    conflicts: list[KGConflict] = []
    for row in rows:
        values: list[KGConflictValue] = []
        for value in row.values or []:
            try:
                document_id = UUID(str(value.get("document_id")))
            except (TypeError, ValueError):
                continue
            if document_id not in allowed:
                continue
            values.append(
                KGConflictValue(
                    value=str(value.get("value", "")),
                    document_id=document_id,
                    document_name=str(value.get("document_name", "Document")),
                    quote=str(value.get("quote", "")),
                )
            )
        if len(values) < 2:
            continue
        conflicts.append(
            KGConflict(
                conflict_id=row.conflict_id,
                subject=row.description.split(" has conflicting ")[0],
                relation_type=row.relation_key,
                description=row.description,
                status=row.status,
                values=values,
                detected_at=row.detected_at,
            )
        )
    return KGConflictListResponse(conflicts=conflicts)


admin_only = require_capability(CAP_MANAGE_KNOWLEDGE_GRAPH)


@router.post("/reviews/{review_id}/decide", response_model=KGGovernanceResponse | None)
def decide_entity_review(
    review_id: UUID,
    payload: KGReviewDecisionRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta | None, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> KGGovernanceResponse | None:
    """Approve or reject a pending entity-resolution review."""
    result = decide_review(
        db,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.user_id,
        review_id=review_id,
        approve=payload.approve,
        note=payload.note,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=review_id,
        action="GRAPH_REVIEW_DECISION",
        reason=f"approve={payload.approve}",
    )
    return KGGovernanceResponse(**result) if result else None


@router.post("/nodes/merge", response_model=KGGovernanceResponse)
def merge_entity_nodes(
    payload: KGMergeRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta | None, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> KGGovernanceResponse:
    """Merge two nodes of the same type. Audited and reversible by re-ingestion."""
    result = merge_nodes(
        db,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.user_id,
        source_node_id=payload.source_node_id,
        target_node_id=payload.target_node_id,
        reason=payload.reason,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=payload.source_node_id,
        action="GRAPH_MERGE",
        reason=f"target={payload.target_node_id}",
    )
    return KGGovernanceResponse(**result)


@router.post("/nodes/split", response_model=KGGovernanceResponse)
def split_entity_alias(
    payload: KGSplitRequest,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta | None, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> KGGovernanceResponse:
    """Split one alias into its own node, moving only evidence it supports."""
    result = split_alias(
        db,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.user_id,
        node_id=payload.node_id,
        alias=payload.alias,
        reason=payload.reason,
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=payload.node_id,
        action="GRAPH_SPLIT",
        reason=f"alias_chars={len(payload.alias)}",
    )
    return KGGovernanceResponse(**result)


@router.post(
    "/documents/{document_id}/reprocess", response_model=KGReprocessResponse
)
def reprocess_document_graph(
    document_id: UUID,
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    request_meta: Annotated[RequestMeta | None, Depends(get_request_meta)],
    db: Annotated[Session, Depends(get_db)],
) -> KGReprocessResponse:
    """Queue a fresh, idempotent extraction pass for one document."""
    _require_enabled()
    job_id = enqueue_graph_job(
        db, tenant_id=context.tenant_id, document_id=document_id
    )
    _audit(
        db,
        context,
        request_meta,
        resource_id=document_id,
        action="GRAPH_REPROCESS",
        reason=f"queued={job_id is not None}",
    )
    return KGReprocessResponse(
        document_id=document_id, job_id=job_id, queued=job_id is not None
    )


@router.get("/analytics", response_model=KGAnalyticsResponse)
def graph_analytics(
    context: Annotated[AuthenticatedContext, Depends(admin_only)],
    db: Annotated[Session, Depends(get_db)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> KGAnalyticsResponse:
    """Counts and latencies only; no question text is ever stored."""
    summary = graph_analytics_summary(
        db, tenant_id=context.tenant_id, window_days=window_days
    )
    return KGAnalyticsResponse(**summary)

