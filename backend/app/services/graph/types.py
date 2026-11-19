"""Shared graph value objects.

These dataclasses are deliberately independent of SQLAlchemy so the extraction,
validation, resolution and traversal logic can be unit-tested without a
database. The repository layer converts rows into these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    name: str
    node_type: str
    quote: str
    confidence: float
    aliases: tuple[str, ...] = ()
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    quote: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ChunkExtraction:
    entities: tuple[ExtractedEntity, ...] = ()
    relations: tuple[ExtractedRelation, ...] = ()
    method: str = "RULES"

    def is_empty(self) -> bool:
        return not self.entities and not self.relations


@dataclass(frozen=True, slots=True)
class VisibleEvidence:
    """One authorized quote. Only created after its document was authorized."""

    evidence_id: UUID
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    quote: str
    confidence: float


@dataclass(frozen=True, slots=True)
class VisibleNode:
    node_id: UUID
    node_type: str
    name: str
    description: str | None
    confidence: float
    mention_count: int
    evidence: tuple[VisibleEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleEdge:
    edge_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation_type: str
    confidence: float
    evidence: tuple[VisibleEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphSnapshot:
    nodes: tuple[VisibleNode, ...] = ()
    edges: tuple[VisibleEdge, ...] = ()
    seeds: tuple[UUID, ...] = ()
    denied_documents: int = 0
    truncated: bool = False
    depth: int = 0
    traversal_ms: int = 0

    def node_map(self) -> dict[UUID, VisibleNode]:
        return {node.node_id: node for node in self.nodes}

    def evidence_chunk_ids(self) -> tuple[UUID, ...]:
        seen: list[UUID] = []
        for node in self.nodes:
            for evidence in node.evidence:
                if evidence.chunk_id not in seen:
                    seen.append(evidence.chunk_id)
        for edge in self.edges:
            for evidence in edge.evidence:
                if evidence.chunk_id not in seen:
                    seen.append(evidence.chunk_id)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class GraphFinding:
    """One readable statement derived from a visible edge and its evidence."""

    subject: str
    relation: str
    object: str
    quote: str
    document_name: str
    document_id: UUID
    chunk_id: UUID
    page_start: int
    page_end: int
    confidence: float
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class GraphRetrievalResult:
    snapshot: GraphSnapshot
    candidates: tuple = ()  # tuple[CandidateChunk, ...]
    findings: tuple[GraphFinding, ...] = ()
    seed_count: int = 0
    denied_documents: int = 0
    truncated: bool = False
    traversal_ms: int = 0


@dataclass(frozen=True, slots=True)
class GraphConflictValue:
    value: str
    document_id: UUID
    document_name: str
    quote: str


@dataclass(frozen=True, slots=True)
class DetectedConflict:
    conflict_key: str
    subject: str
    relation_type: str
    description: str
    values: tuple[GraphConflictValue, ...] = ()
    node_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    detected_at: object = field(default=None)
