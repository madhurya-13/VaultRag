"""Persistence helpers for graph rows.

Nothing here performs authorization. Callers must already hold a trusted
``AuthenticatedContext`` and an open transaction whose tenant context is set;
RLS then enforces the boundary at the database level as a second line.

Deletion policy: no DELETE is issued. Evidence is invalidated, then aggregate
counts and archive flags are recomputed from the surviving valid rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    EntityAlias,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeNode,
)
from app.services.graph.normalization import (
    normalize_name,
    relation_key,
    truncate,
)
from app.services.graph.types import ExtractedRelation

logger = logging.getLogger(__name__)


def get_node(
    db: Session, *, tenant_id: UUID, node_id: UUID
) -> KnowledgeNode | None:
    return db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id == node_id,
        )
    )


def get_edge(
    db: Session, *, tenant_id: UUID, edge_id: UUID
) -> KnowledgeEdge | None:
    return db.scalar(
        select(KnowledgeEdge).where(
            KnowledgeEdge.tenant_id == tenant_id,
            KnowledgeEdge.edge_id == edge_id,
        )
    )


def get_or_create_edge(
    db: Session,
    *,
    tenant_id: UUID,
    source_node: KnowledgeNode,
    target_node: KnowledgeNode,
    relation: ExtractedRelation,
) -> tuple[KnowledgeEdge, bool]:
    """Return one edge per (source, target, relation), strengthening it."""
    if source_node.node_id == target_node.node_id:
        raise ValueError("self edges are not allowed")

    key = relation_key(relation.relation_type)
    existing = db.scalar(
        select(KnowledgeEdge).where(
            KnowledgeEdge.tenant_id == tenant_id,
            KnowledgeEdge.source_node_id == source_node.node_id,
            KnowledgeEdge.target_node_id == target_node.node_id,
            KnowledgeEdge.relation_key == key,
        )
    )
    if existing is not None:
        existing.extraction_confidence = max(
            existing.extraction_confidence, relation.confidence
        )
        if existing.is_archived:
            existing.is_archived = False
            existing.archived_at = None
        db.flush()
        return existing, False

    edge = KnowledgeEdge(
        tenant_id=tenant_id,
        source_node_id=source_node.node_id,
        target_node_id=target_node.node_id,
        relation_type=truncate(relation.relation_type, 64),
        relation_key=key,
        extraction_confidence=max(0.0, min(1.0, relation.confidence)),
        evidence_count=0,
    )
    db.add(edge)
    db.flush()
    return edge, True


def add_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
    chunk_id: UUID,
    quote: str,
    confidence: float,
    method: str,
    extractor_version: str,
    node_id: UUID | None = None,
    edge_id: UUID | None = None,
) -> KnowledgeEvidence:
    """Append one valid evidence row for a node or an edge (exactly one)."""
    if (node_id is None) == (edge_id is None):
        raise ValueError("evidence must reference exactly one of node or edge")
    evidence = KnowledgeEvidence(
        tenant_id=tenant_id,
        document_id=document_id,
        chunk_id=chunk_id,
        node_id=node_id,
        edge_id=edge_id,
        quote=truncate(quote, 2000),
        extraction_confidence=max(0.0, min(1.0, confidence)),
        extraction_method=method,
        extractor_version=truncate(extractor_version, 32),
        is_valid=True,
        invalidated_at=None,
    )
    db.add(evidence)
    db.flush()
    return evidence


def invalidate_document_evidence(
    db: Session,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> int:
    """Retire every valid claim sourced from one document.

    Used before re-extraction (idempotent reprocessing) and when a document is
    archived. The rows remain for audit; they simply stop counting as evidence.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(KnowledgeEvidence)
        .where(
            KnowledgeEvidence.tenant_id == tenant_id,
            KnowledgeEvidence.document_id == document_id,
            KnowledgeEvidence.is_valid.is_(True),
        )
        .values(is_valid=False, invalidated_at=now)
    )
    db.flush()
    return int(result.rowcount or 0)


def refresh_edge_counts(
    db: Session, *, tenant_id: UUID, edge_ids: list[UUID] | None = None
) -> int:
    """Recompute ``evidence_count`` from valid evidence, archiving empty edges."""
    counts = dict(
        db.execute(
            select(
                KnowledgeEvidence.edge_id,
                func.count(),
            )
            .where(
                KnowledgeEvidence.tenant_id == tenant_id,
                KnowledgeEvidence.is_valid.is_(True),
                KnowledgeEvidence.edge_id.is_not(None),
            )
            .group_by(KnowledgeEvidence.edge_id)
        ).all()
    )

    statement = select(KnowledgeEdge).where(KnowledgeEdge.tenant_id == tenant_id)
    if edge_ids is not None:
        if not edge_ids:
            return 0
        statement = statement.where(KnowledgeEdge.edge_id.in_(edge_ids))

    changed = 0
    now = datetime.now(timezone.utc)
    for edge in db.scalars(statement).all():
        count = int(counts.get(edge.edge_id, 0))
        if edge.evidence_count != count:
            edge.evidence_count = count
            changed += 1
        should_archive = count == 0
        if edge.is_archived != should_archive:
            edge.is_archived = should_archive
            edge.archived_at = now if should_archive else None
            changed += 1
    db.flush()
    return changed


def refresh_node_state(
    db: Session, *, tenant_id: UUID, node_ids: list[UUID] | None = None
) -> int:
    """Recompute mention counts and archive nodes with no valid evidence.

    A node with zero valid evidence is invisible anyway; archiving it keeps
    search and traversal cheap without destroying the identity for a future
    re-ingestion.
    """
    counts = dict(
        db.execute(
            select(KnowledgeEvidence.node_id, func.count())
            .where(
                KnowledgeEvidence.tenant_id == tenant_id,
                KnowledgeEvidence.is_valid.is_(True),
                KnowledgeEvidence.node_id.is_not(None),
            )
            .group_by(KnowledgeEvidence.node_id)
        ).all()
    )

    statement = select(KnowledgeNode).where(KnowledgeNode.tenant_id == tenant_id)
    if node_ids is not None:
        if not node_ids:
            return 0
        statement = statement.where(KnowledgeNode.node_id.in_(node_ids))

    changed = 0
    now = datetime.now(timezone.utc)
    for node in db.scalars(statement).all():
        count = int(counts.get(node.node_id, 0))
        if node.mention_count < count:
            node.mention_count = count
            changed += 1
        should_archive = count == 0
        if node.is_archived != should_archive:
            node.is_archived = should_archive
            node.archived_at = now if should_archive else None
            changed += 1
    db.flush()
    return changed


def refresh_graph_state(
    db: Session,
    *,
    tenant_id: UUID,
    node_ids: list[UUID] | None = None,
    edge_ids: list[UUID] | None = None,
) -> None:
    """Recompute derived state after evidence was added or invalidated."""
    refresh_edge_counts(db, tenant_id=tenant_id, edge_ids=edge_ids)
    refresh_node_state(db, tenant_id=tenant_id, node_ids=node_ids)


def alias_count(db: Session, *, tenant_id: UUID, node_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EntityAlias)
            .where(
                EntityAlias.tenant_id == tenant_id,
                EntityAlias.node_id == node_id,
            )
        )
        or 0
    )


def node_names(
    db: Session, *, tenant_id: UUID, node_ids: list[UUID]
) -> dict[UUID, str]:
    if not node_ids:
        return {}
    rows = db.execute(
        select(KnowledgeNode.node_id, KnowledgeNode.display_name).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id.in_(node_ids),
        )
    ).all()
    return {row.node_id: row.display_name for row in rows}


def search_node_ids(
    db: Session,
    *,
    tenant_id: UUID,
    tokens: list[str],
    limit: int,
) -> list[UUID]:
    """Candidate node ids whose name or alias contains any query token.

    This is a *seed* lookup, not an answer: every returned node still goes
    through evidence authorization before anything is exposed.
    """
    if not tokens:
        return []
    conditions = [
        KnowledgeNode.tenant_id == tenant_id,
        KnowledgeNode.is_archived.is_(False),
    ]
    token_conditions = [
        KnowledgeNode.normalized_name.contains(normalize_name(token))
        for token in tokens
        if normalize_name(token)
    ]
    if not token_conditions:
        return []
    alias_conditions = [
        EntityAlias.tenant_id == tenant_id,
        EntityAlias.normalized_alias.in_(
            [normalize_name(token) for token in tokens if normalize_name(token)]
        ),
    ]

    from sqlalchemy import exists, or_

    statement = (
        select(KnowledgeNode.node_id)
        .where(
            *conditions,
            or_(
                or_(*token_conditions),
                exists(
                    select(1).where(
                        *alias_conditions,
                        EntityAlias.node_id == KnowledgeNode.node_id,
                    )
                ),
            ),
        )
        .order_by(KnowledgeNode.mention_count.desc())
        .limit(limit)
    )
    return list(db.scalars(statement).all())
