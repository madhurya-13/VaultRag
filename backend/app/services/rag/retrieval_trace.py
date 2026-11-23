"""ID-only retrieval trace.

Evaluation and explainability need to know *which* authorized chunks were
retrieved and in what order. They do not need chunk text beyond the final
sources. This trace therefore contains identifiers, counters and flags only.

The trace is produced after the current authorization filter and after
reranking, but before context construction. It can never contain a chunk that
the caller was not authorized to read, because it is built from the authorized
candidate set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalTrace:
    search_mode: str
    rerank_used: bool
    graph_used: bool
    candidate_count: int
    authorized_count: int
    denied_count: int
    ranked_chunk_ids: tuple[UUID, ...] = ()
    ranked_document_ids: tuple[UUID, ...] = ()
    filters: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        """JSON-safe representation for storage and API responses."""
        return {
            "search_mode": self.search_mode,
            "rerank_used": self.rerank_used,
            "graph_used": self.graph_used,
            "candidate_count": self.candidate_count,
            "authorized_count": self.authorized_count,
            "denied_count": self.denied_count,
            "ranked_chunk_ids": [str(value) for value in self.ranked_chunk_ids],
            "ranked_document_ids": [
                str(value) for value in self.ranked_document_ids
            ],
            "filters": dict(self.filters),
        }
