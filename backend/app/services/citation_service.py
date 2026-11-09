"""Citation validation against the backend's authoritative source list.

The model may only reference source IDs that the backend generated for the
authorized context. Document names, page ranges and chunk IDs returned to the
client always come from that backend list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.services.context_builder import ContextSource

_GROUP_RE = re.compile(r"\[([^\]]*)\]")
_SOURCE_ID_RE = re.compile(r"\bS\d+\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CitationValidation:
    answer: str
    sources: tuple[ContextSource, ...]
    cited_source_ids: tuple[str, ...]
    invalid_source_ids: tuple[str, ...]
    has_valid_citation: bool


def validate_citations(
    answer: str,
    context_sources: Sequence[ContextSource],
) -> CitationValidation:
    """Keep only citations that exist in the authoritative source list."""
    source_map = {source.source_id.upper(): source for source in context_sources}
    valid_ids: list[str] = []
    invalid_ids: list[str] = []

    parts: list[str] = []
    last = 0
    for match in _GROUP_RE.finditer(answer):
        group = match.group(1)
        ids = [value.upper() for value in _SOURCE_ID_RE.findall(group)]
        if not ids:
            # Not a source citation; preserve ordinary brackets such as [note].
            parts.append(answer[last : match.end()])
            last = match.end()
            continue

        valid_in_group: list[str] = []
        for source_id in ids:
            if source_id in source_map:
                if source_id not in valid_ids:
                    valid_ids.append(source_id)
                if source_id not in valid_in_group:
                    valid_in_group.append(source_id)
            elif source_id not in invalid_ids:
                invalid_ids.append(source_id)

        parts.append(answer[last : match.start()])
        if valid_in_group:
            parts.append("[" + ", ".join(valid_in_group) + "]")
        # Invalid-only groups are removed entirely.
        last = match.end()

    parts.append(answer[last:])
    sanitized = "".join(parts).strip()
    # Collapse whitespace left behind by removed markers without changing the
    # substantive text.
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    sanitized = re.sub(r"\s+([.,;:!?])", r"\1", sanitized).strip()

    ordered_sources = tuple(
        source_map[source_id]
        for source_id in sorted(valid_ids, key=lambda value: int(value[1:]))
    )
    return CitationValidation(
        answer=sanitized,
        sources=ordered_sources,
        cited_source_ids=tuple(valid_ids),
        invalid_source_ids=tuple(invalid_ids),
        has_valid_citation=bool(valid_ids),
    )
