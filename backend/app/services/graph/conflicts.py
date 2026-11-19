"""Functional-relation conflicts between authorized documents.

Only relations that can hold a single value per subject are considered
(``MANAGED_BY``, ``OWNS``, ``APPROVES``, ``REPORTS_TO``, ``RESPONSIBLE_FOR``). A
conflict is recorded only when the visible edges point at *different* targets
*sourced from different documents*: two documents agreeing is corroboration, not
a contradiction, and two edges from the same document are one document's
internal inconsistency, which the extraction job already owns.

Both inputs are already authorized, so a conflict never leaks a name or quote
from a document the caller cannot read.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security_constants import CONFLICT_STATUS_OPEN
from app.models import KnowledgeConflict
from app.services.graph.normalization import display_relation, relation_key
from app.services.graph.extraction import FUNCTIONAL_RELATIONS
from app.services.graph.types import (
    DetectedConflict,
    GraphConflictValue,
    VisibleEdge,
    VisibleNode,
)

logger = logging.getLogger(__name__)

MAX_VALUES_PER_CONFLICT = 6


def detect_functional_conflicts(
    edges: list[VisibleEdge],
    nodes: dict[UUID, VisibleNode],
    *,
    relations: frozenset[str] = FUNCTIONAL_RELATIONS,
) -> list[DetectedConflict]:
    """Group visible edges by ``(subject, functional relation)`` and compare."""
    grouped: dict[tuple[UUID, str], list[VisibleEdge]] = defaultdict(list)
    for edge in edges:
        key = relation_key(edge.relation_type)
        if key not in relations:
            continue
        if not edge.evidence:
            continue
        grouped[(edge.source_node_id, key)].append(edge)

    conflicts: list[DetectedConflict] = []
    for (source_id, key), group in grouped.items():
        subject = nodes.get(source_id)
        if subject is None:
            continue

        values: list[GraphConflictValue] = []
        documents: set[UUID] = set()
        node_ids: list[UUID] = [source_id]
        evidence_ids: list[UUID] = []
        seen_targets: set[UUID] = set()

        for edge in sorted(group, key=lambda item: str(item.target_node_id)):
            target = nodes.get(edge.target_node_id)
            if target is None or edge.target_node_id in seen_targets:
                continue
            evidence = edge.evidence[0]
            if evidence.document_id in documents:
                # Same document already spoke about this relation; it cannot
                # contradict another document on its own.
                continue
            seen_targets.add(edge.target_node_id)
            documents.add(evidence.document_id)
            node_ids.append(edge.target_node_id)
            evidence_ids.append(evidence.evidence_id)
            values.append(
                GraphConflictValue(
                    value=target.name,
                    document_id=evidence.document_id,
                    document_name=evidence.document_name,
                    quote=evidence.quote,
                )
            )
            if len(values) >= MAX_VALUES_PER_CONFLICT:
                break

        if len(values) < 2 or len(documents) < 2:
            continue

        relation_label = display_relation(key)
        conflicts.append(
            DetectedConflict(
                conflict_key=f"{source_id}:{key}"[:200],
                subject=subject.name,
                relation_type=key,
                description=(
                    f"{subject.name} has conflicting {relation_label} values "
                    f"across {len(documents)} documents"
                )[:500],
                values=tuple(values),
                node_ids=tuple(node_ids),
                evidence_ids=tuple(evidence_ids),
                detected_at=datetime.now(timezone.utc),
            )
        )
    return conflicts


def record_conflicts(
    db: Session, *, tenant_id: UUID, conflicts: list[DetectedConflict]
) -> int:
    """Upsert detected conflicts on ``(tenant, conflict_key, relation_key)``.

    A resolved or dismissed conflict is left untouched: a fresh detection must
    not silently reopen a decision an administrator already made. Everything
    returned here is derived from authorized evidence by the caller.
    """
    if not conflicts:
        return 0

    written = 0
    for conflict in conflicts:
        existing = db.scalar(
            select(KnowledgeConflict).where(
                KnowledgeConflict.tenant_id == tenant_id,
                KnowledgeConflict.conflict_key == conflict.conflict_key,
                KnowledgeConflict.relation_key == conflict.relation_type,
            )
        )
        payload = [
            {
                "value": value.value,
                "document_id": str(value.document_id),
                "document_name": value.document_name,
                "quote": value.quote,
            }
            for value in conflict.values
        ]
        if existing is None:
            db.add(
                KnowledgeConflict(
                    tenant_id=tenant_id,
                    subject_node_id=(
                        conflict.node_ids[0] if conflict.node_ids else None
                    ),
                    conflict_key=conflict.conflict_key,
                    relation_key=conflict.relation_type,
                    description=conflict.description,
                    values=payload,
                    node_ids=[str(node_id) for node_id in conflict.node_ids],
                    evidence_ids=[
                        str(evidence_id) for evidence_id in conflict.evidence_ids
                    ],
                    status=CONFLICT_STATUS_OPEN,
                )
            )
            written += 1
            continue
        if existing.status != CONFLICT_STATUS_OPEN:
            continue
        existing.description = conflict.description
        existing.values = payload
        existing.node_ids = [str(node_id) for node_id in conflict.node_ids]
        existing.evidence_ids = [
            str(evidence_id) for evidence_id in conflict.evidence_ids
        ]
        written += 1

    db.flush()
    logger.info("KG stage=conflicts detected=%d recorded=%d", len(conflicts), written)
    return written
