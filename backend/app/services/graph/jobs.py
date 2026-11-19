"""Durable graph-extraction jobs.

Graph extraction is deliberately *not* part of the ingestion transaction. A
graph failure marks only its own job FAILED/PENDING and never touches the
document status, whose chunks are already usable by retrieval.

Lifecycle
---------
``enqueue_graph_job`` inserts one PENDING row per document inside a savepoint and
swallows a duplicate-active conflict, so ingestion can call it unconditionally.
``claim_next_job`` reclaims stale PROCESSING rows and claims one PENDING row with
``FOR UPDATE SKIP LOCKED``.

``process_document_graph`` is idempotent: every valid evidence row for the
document is invalidated first, then everything is re-derived from the current
chunks. Re-running a job therefore converges instead of duplicating claims.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security_constants import (
    DOC_STATUS_COMPLETED,
    ERROR_KG_EXTRACTION_FAILED,
    KG_EXTRACTION_LLM,
    KG_EXTRACTION_METHODS,
    KG_EXTRACTION_RULES,
    KG_EXTRACTION_SPACY,
    KG_JOB_STATUS_COMPLETED,
    KG_JOB_STATUS_FAILED,
    KG_JOB_STATUS_PENDING,
    KG_JOB_STATUS_PROCESSING,
    KG_JOB_STATUS_SKIPPED,
)
from app.models import (
    Document,
    DocumentChunk,
    KnowledgeEdge,
    KnowledgeExtractionJob,
    KnowledgeNode,
)
from app.services.graph.conflicts import detect_functional_conflicts, record_conflicts
from app.services.graph.entity_resolution import resolve_entity
from app.services.graph.extraction import EXTRACTOR_VERSION, extract_chunk
from app.services.graph.normalization import normalize_name
from app.services.graph.repository import (
    add_evidence,
    get_or_create_edge,
    invalidate_document_evidence,
    refresh_graph_state,
)
from app.services.graph.types import VisibleEdge, VisibleEvidence, VisibleNode

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS = 500

# The settings field uses snake_case (``rules_spacy``) because it is operator
# facing; the job row stores the canonical label enforced by the check
# constraint. Translating here keeps both vocabularies honest.
_METHOD_LABELS: dict[str, str] = {
    "rules": KG_EXTRACTION_RULES,
    "rules_spacy": KG_EXTRACTION_SPACY,
    "rules_spacy_llm": KG_EXTRACTION_LLM,
}


def extraction_method_label(method: str | None = None) -> str:
    """Canonical, constraint-valid extraction label for a job row."""
    configured = (method or get_settings().knowledge_graph_extraction_method)
    configured = str(configured).strip()
    if configured in KG_EXTRACTION_METHODS:
        return configured
    label = _METHOD_LABELS.get(configured.lower())
    if label is None:
        logger.warning(
            "Unknown knowledge graph extraction method %r; using rules", configured
        )
        return KG_EXTRACTION_RULES
    return label


# ---------------------------------------------------------------------------
# Enqueue / claim
# ---------------------------------------------------------------------------


def enqueue_graph_job(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    method: str | None = None,
) -> UUID | None:
    """Queue extraction for one document. Never raises into ingestion."""
    settings = get_settings()
    if not settings.knowledge_graph_enabled:
        return None

    extraction_method = extraction_method_label(method)
    try:
        with db.begin_nested():
            job = KnowledgeExtractionJob(
                tenant_id=tenant_id,
                document_id=document_id,
                status=KG_JOB_STATUS_PENDING,
                extraction_method=extraction_method,
                max_attempts=settings.knowledge_graph_job_max_attempts,
            )
            db.add(job)
            db.flush()
        return job.job_id
    except IntegrityError:
        # An active job already exists for this document; that is the expected
        # idempotent outcome, not an error.
        logger.info(
            "KG enqueue skipped: an active job exists document_id=%s", document_id
        )
        return None
    except Exception:
        logger.exception(
            "KG enqueue failed; ingestion continues document_id=%s", document_id
        )
        return None


def claim_next_job(
    db: Session, *, tenant_id: UUID, stale_seconds: int | None = None
) -> KnowledgeExtractionJob | None:
    """Reclaim stale jobs, then atomically claim one PENDING job."""
    settings = get_settings()
    window = (
        settings.knowledge_graph_job_stale_seconds
        if stale_seconds is None
        else stale_seconds
    )
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(30, int(window)))

    db.execute(
        update(KnowledgeExtractionJob)
        .where(
            KnowledgeExtractionJob.tenant_id == tenant_id,
            KnowledgeExtractionJob.status == KG_JOB_STATUS_PROCESSING,
            KnowledgeExtractionJob.started_at.is_not(None),
            KnowledgeExtractionJob.started_at < cutoff,
        )
        .values(
            status=KG_JOB_STATUS_PENDING,
            error_code="GRAPH_JOB_STALE",
            error_message="Reclaimed a stale processing job",
        )
    )
    db.flush()

    job = db.scalar(
        select(KnowledgeExtractionJob)
        .where(
            KnowledgeExtractionJob.tenant_id == tenant_id,
            KnowledgeExtractionJob.status == KG_JOB_STATUS_PENDING,
        )
        .order_by(
            KnowledgeExtractionJob.created_at.asc(),
            KnowledgeExtractionJob.job_id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None

    job.status = KG_JOB_STATUS_PROCESSING
    job.started_at = datetime.now(timezone.utc)
    job.attempts = int(job.attempts or 0) + 1
    job.error_code = None
    job.error_message = None
    db.flush()
    return job


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def invalidate_document_graph(
    db: Session, *, tenant_id: UUID, document_id: UUID
) -> int:
    """Retire every claim sourced from one document and recompute aggregates."""
    from app.models import KnowledgeEvidence

    affected = db.execute(
        select(KnowledgeEvidence.node_id, KnowledgeEvidence.edge_id).where(
            KnowledgeEvidence.tenant_id == tenant_id,
            KnowledgeEvidence.document_id == document_id,
            KnowledgeEvidence.is_valid.is_(True),
        )
    ).all()
    node_ids = [row[0] for row in affected if row[0] is not None]
    edge_ids = [row[1] for row in affected if row[1] is not None]

    invalidated = invalidate_document_evidence(
        db, tenant_id=tenant_id, document_id=document_id
    )
    refresh_graph_state(
        db,
        tenant_id=tenant_id,
        node_ids=node_ids or None,
        edge_ids=edge_ids or None,
    )
    return invalidated


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def _node_map_for(
    db: Session,
    *,
    nodes: dict[tuple[str, str], KnowledgeNode],
    by_name: dict[str, KnowledgeNode],
    node_type: str,
    name: str,
) -> KnowledgeNode | None:
    node = nodes.get((node_type, normalize_name(name)))
    if node is not None:
        return node
    return by_name.get(normalize_name(name))


def _first_evidence_by_edge(db: Session, tenant_id: UUID, edge_ids: list[UUID]):
    """First valid evidence row per edge, with its document name."""
    from app.models import KnowledgeEvidence

    if not edge_ids:
        return {}
    rows = db.scalars(
        select(KnowledgeEvidence)
        .where(
            KnowledgeEvidence.tenant_id == tenant_id,
            KnowledgeEvidence.edge_id.in_(edge_ids),
            KnowledgeEvidence.is_valid.is_(True),
        )
        .order_by(
            KnowledgeEvidence.extraction_confidence.desc(),
            KnowledgeEvidence.created_at.asc(),
        )
    ).all()
    chosen: dict[UUID, KnowledgeEvidence] = {}
    for row in rows:
        chosen.setdefault(row.edge_id, row)
    if not chosen:
        return {}

    document_ids = {row.document_id for row in chosen.values()}
    names = dict(
        db.execute(
            select(Document.document_id, Document.document_name).where(
                Document.tenant_id == tenant_id,
                Document.document_id.in_(document_ids),
            )
        ).all()
    )
    return {
        edge_id: VisibleEvidence(
            evidence_id=row.evidence_id,
            document_id=row.document_id,
            document_name=names.get(row.document_id, "Document"),
            chunk_id=row.chunk_id,
            page_start=1,
            page_end=1,
            quote=row.quote,
            confidence=row.extraction_confidence,
        )
        for edge_id, row in chosen.items()
    }


def _record_conflicts_for(
    db: Session, *, tenant_id: UUID, edge_ids: list[UUID]
) -> int:
    """Detect conflicts on the edges this job touched (tenant scope, no caller)."""
    if not edge_ids:
        return 0
    edges = db.scalars(
        select(KnowledgeEdge).where(
            KnowledgeEdge.tenant_id == tenant_id,
            KnowledgeEdge.edge_id.in_(edge_ids),
            KnowledgeEdge.is_archived.is_(False),
        )
    ).all()
    if not edges:
        return 0

    evidence = _first_evidence_by_edge(db, tenant_id, [edge.edge_id for edge in edges])
    node_ids = {
        node_id
        for edge in edges
        for node_id in (edge.source_node_id, edge.target_node_id)
    }
    node_rows = db.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id.in_(node_ids),
        )
    ).all()
    nodes = {
        row.node_id: VisibleNode(
            node_id=row.node_id,
            node_type=row.node_type,
            name=row.display_name,
            description=row.description,
            confidence=row.extraction_confidence,
            mention_count=row.mention_count,
        )
        for row in node_rows
    }

    visible_edges: list[VisibleEdge] = []
    for edge in edges:
        first = evidence.get(edge.edge_id)
        if first is None:
            continue
        visible_edges.append(
            VisibleEdge(
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation_type=edge.relation_type,
                confidence=edge.extraction_confidence,
                evidence=(first,),
            )
        )

    conflicts = detect_functional_conflicts(visible_edges, nodes)
    return record_conflicts(db, tenant_id=tenant_id, conflicts=conflicts)


def process_document_graph(
    db: Session, *, job: KnowledgeExtractionJob
) -> dict[str, int]:
    """Re-derive the graph for one document. Raises on unusable input."""
    settings = get_settings()
    tenant_id = job.tenant_id

    document = db.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.document_id == job.document_id,
        )
    )
    if document is None:
        raise ValueError("Document no longer exists")
    if document.processing_status != DOC_STATUS_COMPLETED:
        raise ValueError("Document is not fully processed")

    counters = {
        "chunks_processed": 0,
        "nodes_created": 0,
        "edges_created": 0,
        "evidence_created": 0,
        "evidence_invalidated": 0,
    }
    counters["evidence_invalidated"] = invalidate_document_evidence(
        db, tenant_id=tenant_id, document_id=document.document_id
    )

    chunks = db.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentChunk.document_id == document.document_id,
        )
        .order_by(DocumentChunk.chunk_index.asc())
    ).all()

    touched_edges: list[UUID] = []
    touched_nodes: list[UUID] = []

    for chunk in chunks:
        counters["chunks_processed"] += 1
        extraction = extract_chunk(
            chunk.chunk_text,
            method=job.extraction_method,
            spacy_model=settings.knowledge_graph_spacy_model,
            min_confidence=settings.knowledge_graph_min_confidence,
            max_quote_chars=settings.knowledge_graph_max_quote_chars,
            max_entities=settings.knowledge_graph_max_nodes_per_chunk,
            max_relations=settings.knowledge_graph_max_edges_per_chunk,
        )
        if extraction.is_empty():
            continue

        nodes_by_key: dict[tuple[str, str], KnowledgeNode] = {}
        nodes_by_name: dict[str, KnowledgeNode] = {}
        for entity in extraction.entities:
            resolution = resolve_entity(db, tenant_id=tenant_id, entity=entity)
            node = resolution.node
            nodes_by_key[(entity.node_type, normalize_name(entity.name))] = node
            nodes_by_name.setdefault(normalize_name(entity.name), node)
            if resolution.created:
                counters["nodes_created"] += 1
            add_evidence(
                db,
                tenant_id=tenant_id,
                document_id=document.document_id,
                chunk_id=chunk.chunk_id,
                quote=entity.quote,
                confidence=entity.confidence,
                method=extraction.method,
                extractor_version=EXTRACTOR_VERSION,
                node_id=node.node_id,
            )
            counters["evidence_created"] += 1
            touched_nodes.append(node.node_id)

        for relation in extraction.relations:
            source = _node_map_for(
                db,
                nodes=nodes_by_key,
                by_name=nodes_by_name,
                node_type=relation.source_type,
                name=relation.source_name,
            )
            target = _node_map_for(
                db,
                nodes=nodes_by_key,
                by_name=nodes_by_name,
                node_type=relation.target_type,
                name=relation.target_name,
            )
            if source is None or target is None or source.node_id == target.node_id:
                continue
            edge, created = get_or_create_edge(
                db,
                tenant_id=tenant_id,
                source_node=source,
                target_node=target,
                relation=relation,
            )
            if created:
                counters["edges_created"] += 1
            add_evidence(
                db,
                tenant_id=tenant_id,
                document_id=document.document_id,
                chunk_id=chunk.chunk_id,
                quote=relation.quote,
                confidence=relation.confidence,
                method=extraction.method,
                extractor_version=EXTRACTOR_VERSION,
                edge_id=edge.edge_id,
            )
            counters["evidence_created"] += 1
            touched_edges.append(edge.edge_id)

    refresh_graph_state(
        db,
        tenant_id=tenant_id,
        node_ids=list(dict.fromkeys(touched_nodes)) or None,
        edge_ids=list(dict.fromkeys(touched_edges)) or None,
    )
    if settings.knowledge_graph_enabled:
        _record_conflicts_for(
            db, tenant_id=tenant_id, edge_ids=list(dict.fromkeys(touched_edges))
        )

    logger.info(
        "KG extraction document_id=%s job_id=%s chunks=%d nodes=%d edges=%d "
        "evidence=%d invalidated=%d",
        document.document_id,
        job.job_id,
        counters["chunks_processed"],
        counters["nodes_created"],
        counters["edges_created"],
        counters["evidence_created"],
        counters["evidence_invalidated"],
    )
    return counters


def _apply_counters(job: KnowledgeExtractionJob, counters: dict[str, int]) -> None:
    job.chunks_processed = counters.get("chunks_processed", 0)
    job.nodes_created = counters.get("nodes_created", 0)
    job.edges_created = counters.get("edges_created", 0)
    job.evidence_created = counters.get("evidence_created", 0)
    job.evidence_invalidated = counters.get("evidence_invalidated", 0)


def run_next_job(
    db: Session, *, tenant_id: UUID, stale_seconds: int | None = None
) -> dict | None:
    """Claim and run one job inside a savepoint. Returns a result summary."""
    job = claim_next_job(db, tenant_id=tenant_id, stale_seconds=stale_seconds)
    if job is None:
        return None

    job_id = job.job_id
    document_id = job.document_id
    try:
        with db.begin_nested():
            counters = process_document_graph(db, job=job)
            _apply_counters(job, counters)
            job.status = KG_JOB_STATUS_COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.error_code = None
            job.error_message = None
        return {
            "job_id": job_id,
            "document_id": document_id,
            "status": KG_JOB_STATUS_COMPLETED,
            **counters,
        }
    except Exception as exc:
        logger.exception(
            "KG extraction failed document_id=%s job_id=%s", document_id, job_id
        )
        max_attempts = int(job.max_attempts or 1)
        exhausted = int(job.attempts or 0) >= max_attempts
        message = str(exc)[:MAX_ERROR_MESSAGE_CHARS] or "Graph extraction failed"
        try:
            with db.begin_nested():
                job.status = (
                    KG_JOB_STATUS_FAILED if exhausted else KG_JOB_STATUS_PENDING
                )
                job.error_code = ERROR_KG_EXTRACTION_FAILED
                job.error_message = message
                job.completed_at = (
                    datetime.now(timezone.utc) if exhausted else None
                )
        except Exception:
            logger.exception("Failed to record graph job failure job_id=%s", job_id)
        return {
            "job_id": job_id,
            "document_id": document_id,
            "status": KG_JOB_STATUS_FAILED if exhausted else KG_JOB_STATUS_PENDING,
            "error": message,
        }


def skip_job(db: Session, job: KnowledgeExtractionJob, *, reason: str) -> None:
    """Mark a job SKIPPED when the graph feature is disabled at run time."""
    job.status = KG_JOB_STATUS_SKIPPED
    job.completed_at = datetime.now(timezone.utc)
    job.error_code = None
    job.error_message = reason[:MAX_ERROR_MESSAGE_CHARS]
    db.flush()
