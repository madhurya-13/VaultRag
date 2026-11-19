"""Entity resolution with a hard safety rule for people.

Resolution order
----------------
1. **Exact alias** inside the tenant and node type: merge into the aliased node.
   An alias is an explicit statement of identity, so this is safe.
2. **Exact identity key**: the same anchor (email, employee id) or the same
   normalized name for a non-person type.
3. **Fuzzy similarity**: only for non-PERSON types, and only above the
   configured auto-merge threshold. Between the review and auto-merge
   thresholds a ``PENDING`` review row is created for an administrator.

PERSON nodes are never merged on name similarity, and a PERSON without an anchor
is never merged into a PERSON with one. Two "Alice Smith" mentions therefore stay
two nodes until a human or an explicit anchor says otherwise. This is the
difference between a graph and a rumour.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security_constants import (
    ALIAS_SOURCE_EXTRACTION,
    NODE_TYPE_PERSON,
    REVIEW_STATUS_PENDING,
)
from app.models import EntityAlias, KnowledgeEntityReview, KnowledgeNode
from app.services.graph.normalization import (
    identity_key,
    normalize_name,
    truncate,
)
from app.services.graph.types import ExtractedEntity

logger = logging.getLogger(__name__)

MAX_ALIASES_PER_ENTITY = 8


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    node: KnowledgeNode
    created: bool
    review_created: bool = False
    matched_by: str = "identity_key"


def similarity(left: str, right: str) -> float:
    """Bounded 0..1 string similarity on normalized names."""
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def _find_alias_node(
    db: Session,
    tenant_id: UUID,
    node_type: str,
    normalized_alias: str,
) -> KnowledgeNode | None:
    return db.scalar(
        select(KnowledgeNode)
        .join(
            EntityAlias,
            (EntityAlias.tenant_id == KnowledgeNode.tenant_id)
            & (EntityAlias.node_id == KnowledgeNode.node_id),
        )
        .where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_type == node_type,
            KnowledgeNode.is_archived.is_(False),
            EntityAlias.tenant_id == tenant_id,
            EntityAlias.normalized_alias == normalized_alias,
        )
        .order_by(KnowledgeNode.mention_count.desc(), KnowledgeNode.created_at.asc())
        .limit(1)
    )


def find_similar_node(
    db: Session,
    tenant_id: UUID,
    node_type: str,
    name: str,
    *,
    floor: float,
) -> tuple[KnowledgeNode | None, float]:
    """Return the most similar existing node above ``floor`` and its score."""
    normalized = normalize_name(name)
    if not normalized:
        return None, 0.0
    candidates = db.scalars(
        select(KnowledgeNode)
        .where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_type == node_type,
            KnowledgeNode.is_archived.is_(False),
        )
        .order_by(KnowledgeNode.mention_count.desc())
        .limit(200)
    ).all()
    best: KnowledgeNode | None = None
    best_score = 0.0
    for candidate in candidates:
        score = similarity(normalized, candidate.normalized_name)
        if score > best_score:
            best, best_score = candidate, score
    if best_score < floor:
        return None, best_score
    return best, best_score


def _queue_review(
    db: Session,
    *,
    tenant_id: UUID,
    node: KnowledgeNode,
    suggestion: KnowledgeNode | None,
    candidate_alias: str,
    score: float,
    reason: str,
) -> bool:
    """Create one pending review per (node, candidate alias). Idempotent."""
    existing = db.scalar(
        select(KnowledgeEntityReview).where(
            KnowledgeEntityReview.tenant_id == tenant_id,
            KnowledgeEntityReview.node_id == node.node_id,
            KnowledgeEntityReview.candidate_alias == candidate_alias,
            KnowledgeEntityReview.status == REVIEW_STATUS_PENDING,
        )
    )
    if existing is not None:
        return False
    db.add(
        KnowledgeEntityReview(
            tenant_id=tenant_id,
            node_id=node.node_id,
            suggested_node_id=suggestion.node_id if suggestion else None,
            candidate_alias=candidate_alias,
            similarity=score,
            reason=reason,
            status=REVIEW_STATUS_PENDING,
        )
    )
    return True


def register_alias(
    db: Session,
    *,
    tenant_id: UUID,
    node: KnowledgeNode,
    alias: str,
    confidence: float,
    source: str = ALIAS_SOURCE_EXTRACTION,
    created_by: UUID | None = None,
) -> bool:
    """Attach one alias to a node. Duplicate aliases are ignored."""
    normalized = normalize_name(alias)
    if not normalized or len(alias.strip()) > 255:
        return False
    if normalized == node.normalized_name:
        return False
    existing = db.scalar(
        select(EntityAlias).where(
            EntityAlias.tenant_id == tenant_id,
            EntityAlias.node_id == node.node_id,
            EntityAlias.normalized_alias == normalized,
        )
    )
    if existing is not None:
        return False
    db.add(
        EntityAlias(
            tenant_id=tenant_id,
            node_id=node.node_id,
            alias=truncate(alias.strip(), 255),
            normalized_alias=normalized,
            source=source,
            extraction_confidence=max(0.0, min(1.0, confidence)),
            created_by=created_by,
        )
    )
    return True


def resolve_entity(
    db: Session,
    *,
    tenant_id: UUID,
    entity: ExtractedEntity,
) -> ResolutionResult:
    """Map one extracted mention onto a canonical node, creating it if needed."""
    settings = get_settings()
    node_type = entity.node_type
    key = identity_key(node_type, entity.name, entity.anchor)
    normalized = normalize_name(entity.name)

    alias_node = _find_alias_node(db, tenant_id, node_type, normalized)
    if alias_node is not None:
        alias_node.mention_count += 1
        alias_node.extraction_confidence = max(
            alias_node.extraction_confidence, entity.confidence
        )
        return ResolutionResult(node=alias_node, created=False, matched_by="alias")

    existing = db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.tenant_id == tenant_id,
            KnowledgeNode.node_type == node_type,
            KnowledgeNode.identity_key == key,
        )
    )
    if existing is not None:
        existing.mention_count += 1
        existing.extraction_confidence = max(
            existing.extraction_confidence, entity.confidence
        )
        if existing.is_archived:
            existing.is_archived = False
            existing.archived_at = None
        for alias in entity.aliases[:MAX_ALIASES_PER_ENTITY]:
            register_alias(
                db,
                tenant_id=tenant_id,
                node=existing,
                alias=alias,
                confidence=entity.confidence,
            )
        return ResolutionResult(node=existing, created=False)

    # Fuzzy matching is allowed for non-person types only. A PERSON without an
    # anchor is a distinct individual until proven otherwise.
    person_without_anchor = node_type == NODE_TYPE_PERSON and not entity.anchor
    if not person_without_anchor:
        match, score = find_similar_node(
            db,
            tenant_id,
            node_type,
            entity.name,
            floor=settings.knowledge_graph_review_threshold,
        )
        if match is not None and score >= settings.knowledge_graph_auto_merge_threshold:
            match.mention_count += 1
            register_alias(
                db,
                tenant_id=tenant_id,
                node=match,
                alias=entity.name,
                confidence=entity.confidence,
            )
            return ResolutionResult(
                node=match, created=False, matched_by="similarity"
            )

        node = _create_node(db, tenant_id=tenant_id, entity=entity, key=key)
        if match is not None:
            _queue_review(
                db,
                tenant_id=tenant_id,
                node=node,
                suggestion=match,
                candidate_alias=truncate(entity.name, 255),
                score=score,
                reason="SIMILAR_NAME",
            )
            return ResolutionResult(
                node=node, created=True, review_created=True
            )
        return ResolutionResult(node=node, created=True)

    # PERSON without an anchor: never auto-merge. Still surface a review when a
    # same-name person already exists, so an administrator can decide.
    same_name, score = find_similar_node(
        db,
        tenant_id,
        node_type,
        entity.name,
        floor=settings.knowledge_graph_review_threshold,
    )
    node = _create_node(db, tenant_id=tenant_id, entity=entity, key=key)
    if same_name is not None:
        _queue_review(
            db,
            tenant_id=tenant_id,
            node=node,
            suggestion=same_name,
            candidate_alias=truncate(entity.name, 255),
            score=score,
            reason="AMBIGUOUS_PERSON",
        )
        return ResolutionResult(node=node, created=True, review_created=True)
    return ResolutionResult(node=node, created=True)


def _create_node(
    db: Session,
    *,
    tenant_id: UUID,
    entity: ExtractedEntity,
    key: str,
) -> KnowledgeNode:
    node = KnowledgeNode(
        tenant_id=tenant_id,
        node_type=entity.node_type,
        canonical_name=truncate(entity.name, 255),
        display_name=truncate(entity.name, 255),
        normalized_name=normalize_name(entity.name)[:255] or "unnamed",
        identity_key=key,
        extraction_confidence=max(0.0, min(1.0, entity.confidence)),
        mention_count=1,
    )
    db.add(node)
    db.flush()
    for alias in entity.aliases[:MAX_ALIASES_PER_ENTITY]:
        register_alias(
            db,
            tenant_id=tenant_id,
            node=node,
            alias=alias,
            confidence=entity.confidence,
        )
    return node
