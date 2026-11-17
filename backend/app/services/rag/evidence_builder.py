"""Evidence grouping for multi-document answers.

Grouping is presentation and reasoning support only. Every item comes from
``ContextSource``, which the context builder creates from authorized chunks.
Excerpts are truncated authorized text, never file paths or raw storage data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from app.services.context_builder import ContextSource
from app.services.reranking import RankedChunk

EXCERPT_CHARS = 400


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    domain_source_id: str
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    excerpt: str
    similarity: float
    rerank_score: float | None
    matched_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    document_id: UUID
    document_name: str
    items: tuple[EvidenceItem, ...]

    @property
    def best_similarity(self) -> float:
        return max((item.similarity for item in self.items), default=0.0)


def build_evidence_items(
    sources: Sequence[ContextSource],
    ranked: Sequence[RankedChunk],
) -> tuple[EvidenceItem, ...]:
    """Attach ranking provenance and an excerpt to every context source."""
    by_chunk = {chunk.chunk_id: chunk for chunk in ranked}
    items: list[EvidenceItem] = []
    for source in sources:
        chunk = by_chunk.get(source.chunk_id)
        excerpt = " ".join(source.text.split())[:EXCERPT_CHARS].strip()
        items.append(
            EvidenceItem(
                domain_source_id=source.source_id,
                document_id=source.document_id,
                document_name=source.document_name,
                chunk_id=source.chunk_id,
                page_start=source.page_start,
                page_end=source.page_end,
                excerpt=excerpt,
                similarity=(
                    source.similarity
                    if chunk is None
                    else (chunk.semantic_similarity or source.similarity)
                ),
                rerank_score=None if chunk is None else chunk.rerank_score,
                matched_by=() if chunk is None else chunk.retrieval_sources,
            )
        )
    return tuple(items)


def group_evidence(
    items: Sequence[EvidenceItem],
) -> tuple[EvidenceGroup, ...]:
    """Group evidence items by document, preserving best-first order."""
    groups: dict[UUID, list[EvidenceItem]] = {}
    names: dict[UUID, str] = {}
    for item in items:
        groups.setdefault(item.document_id, []).append(item)
        names[item.document_id] = item.document_name

    ordered = sorted(
        groups.items(),
        key=lambda pair: (
            -max((item.similarity for item in pair[1]), default=0.0),
            names[pair[0]],
        ),
    )
    return tuple(
        EvidenceGroup(
            document_id=document_id,
            document_name=names[document_id],
            items=tuple(sorted(group, key=lambda item: item.domain_source_id)),
        )
        for document_id, group in ordered
    )


def citations_payload(sources: Sequence[ContextSource]) -> list[dict]:
    """Citation references safe to persist in conversation history.

    Only identifiers and page ranges are stored. Chunk text is never copied
    into conversation memory.
    """
    return [
        {
            "document_id": str(source.document_id),
            "chunk_id": str(source.chunk_id),
            "page_start": source.page_start,
            "page_end": source.page_end,
            "document_name": source.document_name,
        }
        for source in sources
    ]
