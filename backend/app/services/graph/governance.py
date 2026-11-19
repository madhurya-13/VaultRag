"""Administrator governance: review decisions, merges and splits.

Every operation is tenant-scoped, audited through ``KnowledgeEntityMerge`` and
followed by a recomputation of derived counts. Nothing here deletes rows: a
merged-away node is archived, and evidence is re-parented rather than destroyed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models import (
    EntityAlias,
    KnowledgeEdge,
    KnowledgeEvidence,
    KnowledgeEntityMerge,
    KnowledgeEntityReview,
    KnowledgeNode,
)
from app.core.security_constants import (
    MERGE_ACTION_MERGE,
    MERGE_ACTION_SPLIT,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
)
from app.services.graph.normalization import (
    identity_key,
    normalize_name,
    quote_supported,
    relation_key,
    truncate,
)
from app.services.graph.repository import refresh_graph_state

logger = logging.getLogger(__name__)


def _load_node(db: Session, tenant_id: UUID, node_id: UUID) -> KnowledgeNode:
    node = db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_id == node_id,
        )
    )
    if node is None:
        raise NotFoundError("Knowledge graph node not found")
    return node


def _audit(
    db: Session,
    *,
    tenant_id: UUID,
    action: str,
    source_node_id: UUID,
    target_node_id: UUID | None,
    actor_user_id: UUID,
    reason: str,
    aliases_moved: int = 0,
    edges_moved: int = 0,
    evidence_moved: int = 0,
) -> KnowledgeEntityMerge:
    record = KnowledgeEntityMerge(
        tenant_id=tenant_id,
        action=action,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        actor_user_id=actor_user_id,
        reason=truncate(reason, 500),
        aliases_moved=aliases_moved,
        edges_moved=edges_moved,
        evidence_moved=evidence_moved,
    )
    db.add(record)
    db.flush()
    return record


def _move_aliases(
    db: Session, *, tenant_id: UUID, source: KnowledgeNode, target: KnowledgeNode
) -> int:
    moved = 0
    aliases = db.scalars(
        select(EntityAlias).where(
            EntityAlias.tenant_id == tenant_id,
            EntityAlias.node_id == source.node_id,
        )
    ).all()
    existing = set(
        db.scalars(
            select(EntityAlias.normalized_alias).where(
                EntityAlias.tenant_id == tenant_id,
                EntityAlias.node_id == target.node_id,
            )
        ).all()
    )
    for alias in aliases:
        if alias.normalized_alias in existing:
            continue
        alias.node_id = target.node_id
        existing.add(alias.normalized_alias)
        moved += 1
    db.flush()
    return moved


def _move_edges(
    db: Session,
    *,
    tenant_id: UUID,
    source: KnowledgeNode,
    target: KnowledgeNode,
) -> int:
    """Re-point edges onto the merged node, deduplicating triples."""
    edges = db.scalars(
        select(KnowledgeEdge).where(
            KnowledgeEdge.tenant_id == tenant_id,
            (KnowledgeEdge.source_node_id == source.node_id)
            | (KnowledgeEdge.target_node_id == source.node_id),
        )
    ).all()
    moved = 0
    for edge in edges:
        new_source = (
            target.node_id if edge.source_node_id == source.node_id
            else edge.source_node_id
        )
        new_target = (
            target.node_id if edge.target_node_id == source.node_id
            else edge.target_node_id
        )
        if new_source == new_target:
            # The merge collapsed a self-relationship. Retire its claims.
            db.execute(
                update(KnowledgeEvidence)
                .where(
                    KnowledgeEvidence.tenant_id == tenant_id,
                    KnowledgeEvidence.edge_id == edge.edge_id,
                    KnowledgeEvidence.is_valid.is_(True),
                )
                .values(
                    is_valid=False, invalidated_at=datetime.now(timezone.utc)
                )
            )
            edge.evidence_count = 0
            edge.is_archived = True
            edge.archived_at = datetime.now(timezone.utc)
            continue

        duplicate = db.scalar(
            select(KnowledgeEdge).where(
                KnowledgeEdge.tenant_id == tenant_id,
                KnowledgeEdge.edge_id != edge.edge_id,
                KnowledgeEdge.source_node_id == new_source,
                KnowledgeEdge.target_node_id == new_target,
                KnowledgeEdge.relation_key
                == relation_key(edge.relation_type),
            )
        )
        if duplicate is not None:
            db.execute(
                update(KnowledgeEvidence)
                .where(
                    KnowledgeEvidence.tenant_id == tenant_id,
                    KnowledgeEvidence.edge_id == edge.edge_id,
                )
                .values(edge_id=duplicate.edge_id)
            )
            edge.evidence_count = 0
            edge.is_archived = True
            edge.archived_at = datetime.now(timezone.utc)
            duplicate.extraction_confidence = max(
                duplicate.extraction_confidence, edge.extraction_confidence
            )
            moved += 1
            continue

        edge.source_node_id = new_source
        edge.target_node_id = new_target
        moved += 1
    db.flush()
    return moved


def merge_nodes(
    db: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    source_node_id: UUID,
    target_node_id: UUID,
    reason: str,
) -> dict:
    """Merge ``source`` into ``target`` and archive the source node."""
    if source_node_id == target_node_id:
        raise BadRequestError("A node cannot be merged into itself")
    source = _load_node(db, tenant_id, source_node_id)
    target = _load_node(db, tenant_id, target_node_id)
    if source.node_type != target.node_type:
        raise BadRequestError("Only nodes of the same type can be merged")

    aliases_moved = _move_aliases(db, tenant_id=tenant_id, source=source, target=target)
    evidence_moved = int(
        db.execute(
            update(KnowledgeEvidence)
            .where(
                KnowledgeEvidence.tenant_id == tenant_id,
                KnowledgeEvidence.node_id == source.node_id,
            )
            .values(node_id=target.node_id)
        ).rowcount
        or 0
    )
    edges_moved = _move_edges(db, tenant_id=tenant_id, source=source, target=target)

    now = datetime.now(timezone.utc)
    source.is_archived = True
    source.archived_at = now
    source.mention_count = 0
    target.mention_count = target.mention_count + max(source.mention_count, 0)
    target.extraction_confidence = max(
        target.extraction_confidence, source.extraction_confidence
    )

    _audit(
        db,
        tenant_id=tenant_id,
        action=MERGE_ACTION_MERGE,
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        actor_user_id=actor_user_id,
        reason=reason,
        aliases_moved=aliases_moved,
        edges_moved=edges_moved,
        evidence_moved=evidence_moved,
    )
    refresh_graph_state(db, tenant_id=tenant_id)
    logger.info(
        "KG governance merge tenant=%s source=%s target=%s aliases=%d edges=%d "
        "evidence=%d",
        tenant_id,
        source.node_id,
        target.node_id,
        aliases_moved,
        edges_moved,
        evidence_moved,
    )
    return {
        "action": MERGE_ACTION_MERGE,
        "source_node_id": source.node_id,
        "target_node_id": target.node_id,
        "aliases_moved": aliases_moved,
        "edges_moved": edges_moved,
        "evidence_moved": evidence_moved,
    }


def split_alias(
    db: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    node_id: UUID,
    alias: str,
    reason: str,
) -> dict:
    """Create a new node for one alias and move the evidence it supports."""
    node = _load_node(db, tenant_id, node_id)
    normalized = normalize_name(alias)
    alias_row = db.scalar(
        select(EntityAlias).where(
            EntityAlias.tenant_id == tenant_id,
            EntityAlias.node_id == node_id,
            EntityAlias.normalized_alias == normalized,
        )
    )
    if alias_row is None:
        raise BadRequestError("The alias does not belong to this node")

    new_node = KnowledgeNode(
        tenant_id=tenant_id,
        node_type=node.node_type,
        canonical_name=truncate(alias_row.alias, 255),
        display_name=truncate(alias_row.alias, 255),
        normalized_name=normalized[:255] or "unnamed",
        identity_key=identity_key(node.node_type, alias_row.alias),
        extraction_confidence=alias_row.extraction_confidence,
        mention_count=0,
    )
    # The insert may violate the unique node identity constraint. Roll back a
    # savepoint rather than the session so the caller's transaction (and every
    # other unit of work in it) stays intact and can still be committed.
    try:
        with db.begin_nested():
            db.add(new_node)
            db.flush()
    except IntegrityError as exc:
        raise BadRequestError("A node for that alias already exists") from exc

    # Only evidence whose quote actually contains the alias moves; everything
    # else still belongs to the original node.
    candidates = db.scalars(
        select(KnowledgeEvidence).where(
            KnowledgeEvidence.tenant_id == tenant_id,
            KnowledgeEvidence.node_id == node_id,
        )
    ).all()
    evidence_moved = 0
    for evidence in candidates:
        if quote_supported(alias_row.alias, evidence.quote):
            evidence.node_id = new_node.node_id
            evidence_moved += 1
    alias_row.node_id = new_node.node_id

    _audit(
        db,
        tenant_id=tenant_id,
        action=MERGE_ACTION_SPLIT,
        source_node_id=node.node_id,
        target_node_id=new_node.node_id,
        actor_user_id=actor_user_id,
        reason=reason,
        aliases_moved=1,
        evidence_moved=evidence_moved,
    )
    refresh_graph_state(db, tenant_id=tenant_id)
    logger.info(
        "KG governance split tenant=%s node=%s new=%s evidence=%d",
        tenant_id,
        node.node_id,
        new_node.node_id,
        evidence_moved,
    )
    return {
        "action": MERGE_ACTION_SPLIT,
        "source_node_id": node.node_id,
        "target_node_id": new_node.node_id,
        "aliases_moved": 1,
        "edges_moved": 0,
        "evidence_moved": evidence_moved,
    }


def decide_review(
    db: Session,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    review_id: UUID,
    approve: bool,
    note: str | None,
) -> dict | None:
    """Approve (merge the suggestion) or reject one pending review."""
    review = db.scalar(
        select(KnowledgeEntityReview).where(
            KnowledgeEntityReview.tenant_id == tenant_id,
            KnowledgeEntityReview.review_id == review_id,
        )
    )
    if review is None:
        raise NotFoundError("Review not found")
    if review.status != "PENDING":
        raise BadRequestError("This review has already been decided")

    result: dict | None = None
    if approve and review.suggested_node_id is not None:
        result = merge_nodes(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            source_node_id=review.node_id,
            target_node_id=review.suggested_node_id,
            reason=f"Review {review.review_id} approved"
            + (f": {note}" if note else ""),
        )

    review.status = REVIEW_STATUS_APPROVED if approve else REVIEW_STATUS_REJECTED
    review.reviewed_by = actor_user_id
    review.reviewed_at = datetime.now(timezone.utc)
    review.review_note = truncate(note, 500) if note else None
    db.flush()
    return result
