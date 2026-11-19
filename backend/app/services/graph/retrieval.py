"""Graph retrieval: seeds, evidence-gated subgraph, findings and candidates.

The order of operations is the security design:

1. Find *candidate* seeds by name/alias. A seed is not yet visible.
2. Authorize the evidence of those nodes; keep only nodes with at least one
   currently readable evidence row.
3. Repeat, hop by hop: load edges touching the frontier, authorize *edge*
   evidence, then authorize the evidence of any endpoint that is not visible
   yet. An edge survives only when its own evidence is visible **and** both
   endpoints are visible.
4. Derive findings from surviving edges only.
5. Build RAG candidates from the chunk ids of surviving evidence only.

Every stage is bounded and the result records ``truncated`` rather than
silently returning a different shape.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, DocumentChunk, KnowledgeEdge, KnowledgeNode
from app.schemas.rag import RAGFilters
from app.services.graph.authorization import (
    GraphAuthorizationContext,
    chunk_page_map,
    to_visible_evidence,
    visible_evidence_rows,
)
from app.services.graph.authorization import (
    build_visible_edge as _build_visible_edge,
)
from app.services.graph.authorization import (
    build_visible_node as _build_visible_node,
)
from app.services.graph.normalization import display_relation, relation_key
from app.services.graph.repository import search_node_ids
from app.services.graph.types import (
    GraphFinding,
    GraphRetrievalResult,
    GraphSnapshot,
    VisibleEdge,
    VisibleNode,
)
from app.services.rag.retrieval_predicates import (
    candidate_where_clause,
    document_join_condition,
)
from app.services.vector_retrieval import CandidateChunk

logger = logging.getLogger(__name__)

MAX_QUERY_TOKENS = 12
MIN_TOKEN_CHARS = 2
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "does",
        "did",
        "was",
        "were",
        "are",
        "is",
        "of",
        "to",
        "in",
        "on",
        "by",
        "at",
        "as",
        "an",
        "or",
        "be",
        "has",
        "have",
        "had",
        "about",
    }
)
_TOKEN = re.compile(r"[a-z0-9]+")


def query_tokens(question: str) -> list[str]:
    """Deterministic, bounded seed tokens from a natural-language question."""
    if not question:
        return []
    tokens: list[str] = []
    for match in _TOKEN.finditer(question.lower()):
        token = match.group(0)
        if len(token) < MIN_TOKEN_CHARS or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= MAX_QUERY_TOKENS:
            break
    return tokens


# ---------------------------------------------------------------------------
# Row loading (no text is selected here)
# ---------------------------------------------------------------------------


def _load_node_rows(
    db: Session, tenant_id: UUID, node_ids: list[UUID]
) -> dict[UUID, KnowledgeNode]:
    if not node_ids:
        return {}
    rows = db.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id.in_(node_ids),
            KnowledgeNode.is_archived.is_(False),
        )
    ).all()
    return {row.node_id: row for row in rows}


def _visible_nodes(
    db: Session,
    context: AuthenticatedContext,
    auth: GraphAuthorizationContext,
    node_ids: list[UUID],
    *,
    max_evidence: int,
) -> dict[UUID, VisibleNode]:
    """Nodes that have at least one currently authorized evidence row."""
    rows = _load_node_rows(db, context.tenant_id, node_ids)
    if not rows:
        return {}

    evidence_rows = visible_evidence_rows(
        db, context, auth, node_ids=list(rows), limit=max_evidence
    )
    if not evidence_rows:
        return {}

    pages = chunk_page_map(db, context, [row.chunk_id for row in evidence_rows])
    by_node: dict[UUID, list] = defaultdict(list)
    for row in evidence_rows:
        if row.node_id is not None:
            by_node[row.node_id].append(row)

    visible: dict[UUID, VisibleNode] = {}
    for node_id, node in rows.items():
        node_rows = by_node.get(node_id, [])
        if not node_rows:
            continue
        visible[node_id] = _build_visible_node(
            node,
            to_visible_evidence(node_rows, pages=pages, names=auth.document_names),
        )
    return visible


def _edges_touching(
    db: Session, tenant_id: UUID, node_ids: list[UUID], *, limit: int
) -> list[KnowledgeEdge]:
    if not node_ids or limit < 1:
        return []
    return list(
        db.scalars(
            select(KnowledgeEdge)
            .where(
                KnowledgeEdge.tenant_id == tenant_id,
                KnowledgeEdge.is_archived.is_(False),
                or_(
                    KnowledgeEdge.source_node_id.in_(node_ids),
                    KnowledgeEdge.target_node_id.in_(node_ids),
                ),
            )
            .order_by(
                KnowledgeEdge.evidence_count.desc(),
                KnowledgeEdge.extraction_confidence.desc(),
                KnowledgeEdge.edge_id.asc(),
            )
            .limit(limit)
        ).all()
    )


def _visible_edges(
    db: Session,
    context: AuthenticatedContext,
    auth: GraphAuthorizationContext,
    candidates: list[KnowledgeEdge],
    *,
    visible_node_ids: set[UUID],
    max_edges: int,
    max_evidence: int,
) -> list[VisibleEdge]:
    """Edges with their own authorized evidence and both endpoints visible."""
    if not candidates or max_edges < 1:
        return []
    eligible = [
        edge
        for edge in candidates
        if edge.source_node_id in visible_node_ids
        and edge.target_node_id in visible_node_ids
    ][:max_edges]
    if not eligible:
        return []

    evidence_rows = visible_evidence_rows(
        db, context, auth, edge_ids=[edge.edge_id for edge in eligible],
        limit=max_evidence,
    )
    if not evidence_rows:
        return []

    pages = chunk_page_map(db, context, [row.chunk_id for row in evidence_rows])
    by_edge: dict[UUID, list] = defaultdict(list)
    for row in evidence_rows:
        if row.edge_id is not None:
            by_edge[row.edge_id].append(row)

    visible: list[VisibleEdge] = []
    for edge in eligible:
        edge_rows = by_edge.get(edge.edge_id, [])
        if not edge_rows:
            continue
        visible.append(
            _build_visible_edge(
                edge,
                to_visible_evidence(
                    edge_rows, pages=pages, names=auth.document_names
                ),
            )
        )
    return visible


def load_visible_graph(
    db: Session,
    context: AuthenticatedContext,
    auth: GraphAuthorizationContext,
    *,
    tokens: list[str],
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    max_evidence: int,
    max_seeds: int,
) -> GraphSnapshot:
    """Load a bounded subgraph in which every element is evidence-authorized."""
    started = time.monotonic()

    seed_candidates = search_node_ids(
        db, tenant_id=context.tenant_id, tokens=tokens, limit=max_seeds
    )
    seed_nodes = _visible_nodes(
        db, context, auth, seed_candidates, max_evidence=max_evidence
    )
    seeds = list(seed_nodes)[:max_seeds]
    nodes: dict[UUID, VisibleNode] = {
        node_id: seed_nodes[node_id] for node_id in seeds
    }
    edges: dict[UUID, VisibleEdge] = {}
    truncated = False
    depth_reached = 0
    frontier = list(seeds)

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        if len(nodes) >= max_nodes or len(edges) >= max_edges:
            truncated = True
            break

        candidate_edges = _edges_touching(
            db,
            context.tenant_id,
            frontier,
            limit=max(max_edges - len(edges), 1) * 4,
        )
        if not candidate_edges:
            break

        endpoints = {
            node_id
            for edge in candidate_edges
            for node_id in (edge.source_node_id, edge.target_node_id)
        }
        unseen = [node_id for node_id in endpoints if node_id not in nodes]
        capacity = max_nodes - len(nodes)
        if len(unseen) > capacity:
            unseen = unseen[:capacity]
            truncated = True
        if unseen:
            nodes.update(
                _visible_nodes(
                    db, context, auth, unseen, max_evidence=max_evidence
                )
            )

        new_edges = _visible_edges(
            db,
            context,
            auth,
            candidate_edges,
            visible_node_ids=set(nodes),
            max_edges=max_edges - len(edges),
            max_evidence=max_evidence,
        )
        added = 0
        next_frontier: list[UUID] = []
        for edge in new_edges:
            if edge.edge_id in edges:
                continue
            edges[edge.edge_id] = edge
            added += 1
            for endpoint in (edge.source_node_id, edge.target_node_id):
                if endpoint not in frontier and endpoint not in next_frontier:
                    next_frontier.append(endpoint)
        if len(edges) >= max_edges:
            truncated = True
        if not added and not unseen:
            break
        if next_frontier:
            depth_reached = depth
        frontier = next_frontier

    ordered_nodes = tuple(
        nodes[node_id] for node_id in seeds if node_id in nodes
    ) + tuple(node for node_id, node in nodes.items() if node_id not in seeds)

    return GraphSnapshot(
        nodes=ordered_nodes,
        edges=tuple(edges.values()),
        seeds=tuple(seeds),
        denied_documents=auth.denied_documents,
        truncated=truncated,
        depth=depth_reached,
        traversal_ms=int((time.monotonic() - started) * 1000),
    )


# ---------------------------------------------------------------------------
# Findings and candidates
# ---------------------------------------------------------------------------


def build_findings(
    snapshot: GraphSnapshot, *, limit: int
) -> tuple[GraphFinding, ...]:
    """One readable statement per visible edge, strongest evidence first."""
    if limit < 1:
        return ()
    nodes = snapshot.node_map()
    findings: list[GraphFinding] = []
    ordered = sorted(
        snapshot.edges,
        key=lambda edge: (-edge.confidence, str(edge.edge_id)),
    )
    for edge in ordered:
        if len(findings) >= limit:
            break
        if not edge.evidence:
            continue
        source = nodes.get(edge.source_node_id)
        target = nodes.get(edge.target_node_id)
        if source is None or target is None:
            continue
        evidence = edge.evidence[0]
        findings.append(
            GraphFinding(
                subject=source.name,
                relation=display_relation(relation_key(edge.relation_type)),
                object=target.name,
                quote=evidence.quote,
                document_name=evidence.document_name,
                document_id=evidence.document_id,
                chunk_id=evidence.chunk_id,
                page_start=evidence.page_start,
                page_end=evidence.page_end,
                confidence=min(edge.confidence, evidence.confidence),
            )
        )
    return tuple(findings)


def candidates_from_evidence(
    db: Session,
    context: AuthenticatedContext,
    *,
    auth: GraphAuthorizationContext,
    chunk_ids: list[UUID],
    model_label: str,
    filters: RAGFilters | None = None,
) -> list[CandidateChunk]:
    """Candidate rows for graph evidence chunks, with the shared SQL prefilter.

    Only chunk ids that already belong to authorized evidence are passed in, and
    the query still applies the same tenant/document/permission predicates as
    every other retriever. No chunk text is selected.
    """
    if not chunk_ids:
        return []

    rows = db.execute(
        select(
            DocumentChunk.chunk_id,
            DocumentChunk.tenant_id,
            DocumentChunk.document_id,
            DocumentChunk.chunk_index,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            Document.document_name,
            Document.department,
            Document.classification,
            Document.required_clearance_level,
            Document.is_active.label("document_is_active"),
            Document.processing_status,
            Document.embedding_model,
        )
        .select_from(DocumentChunk)
        .join(Document, document_join_condition())
        .where(
            *candidate_where_clause(
                context,
                model_label=model_label,
                role_ids=auth.role_ids,
                filters=filters,
            ),
            DocumentChunk.chunk_id.in_(chunk_ids),
        )
        .order_by(DocumentChunk.chunk_index.asc(), DocumentChunk.chunk_id.asc())
    ).all()

    candidates: list[CandidateChunk] = []
    for rank, row in enumerate(rows, start=1):
        candidates.append(
            CandidateChunk(
                chunk_id=row.chunk_id,
                tenant_id=row.tenant_id,
                document_id=row.document_id,
                chunk_index=row.chunk_index,
                page_start=row.page_start,
                page_end=row.page_end,
                # A graph match has no cosine distance. Zero here is a neutral
                # placeholder, and ``semantic_similarity`` stays None so no
                # cosine threshold can be applied to it downstream.
                distance=0.0,
                document_name=row.document_name,
                department=row.department,
                classification=row.classification,
                required_clearance_level=row.required_clearance_level,
                document_is_active=row.document_is_active,
                processing_status=row.processing_status,
                embedding_model=row.embedding_model,
                semantic_similarity=None,
                keyword_score=None,
                fused_score=None,
                retrieval_sources=("graph",),
                retrieval_rank=rank,
            )
        )
    return candidates


def retrieve_graph(
    db: Session,
    context: AuthenticatedContext,
    *,
    question: str,
    auth: GraphAuthorizationContext,
    model_label: str,
    filters: RAGFilters | None = None,
    max_depth: int | None = None,
) -> GraphRetrievalResult:
    """Full graph retrieval for one question."""
    settings = get_settings()
    depth = settings.knowledge_graph_default_depth if max_depth is None else max_depth
    depth = max(1, min(depth, settings.knowledge_graph_max_depth))

    started = time.monotonic()
    snapshot = load_visible_graph(
        db,
        context,
        auth,
        tokens=query_tokens(question),
        max_depth=depth,
        max_nodes=settings.knowledge_graph_max_nodes,
        max_edges=settings.knowledge_graph_max_edges,
        max_evidence=settings.knowledge_graph_max_evidence,
        max_seeds=settings.knowledge_graph_max_seeds,
    )
    findings = build_findings(
        snapshot, limit=settings.knowledge_graph_findings
    )
    candidates = candidates_from_evidence(
        db,
        context,
        auth=auth,
        chunk_ids=list(snapshot.evidence_chunk_ids()),
        model_label=model_label,
        filters=filters,
    )
    logger.info(
        "KG stage=retrieval seeds=%d nodes=%d edges=%d candidates=%d "
        "denied=%d truncated=%s duration_ms=%d",
        len(snapshot.seeds),
        len(snapshot.nodes),
        len(snapshot.edges),
        len(candidates),
        snapshot.denied_documents,
        snapshot.truncated,
        int((time.monotonic() - started) * 1000),
    )
    return GraphRetrievalResult(
        snapshot=snapshot,
        candidates=tuple(candidates),
        findings=findings,
        seed_count=len(snapshot.seeds),
        denied_documents=snapshot.denied_documents,
        truncated=snapshot.truncated,
        traversal_ms=int((time.monotonic() - started) * 1000),
    )
