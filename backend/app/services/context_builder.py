"""Bounded context construction from authorized chunks only.

Source identifiers are generated here by the backend. The LLM never supplies
document names, page numbers or source identifiers that are trusted later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence
from uuid import UUID

from app.services.reranking import RankedChunk

TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class ContextSource:
    source_id: str
    document_id: UUID
    document_name: str
    chunk_id: UUID
    page_start: int
    page_end: int
    text: str
    similarity: float


@dataclass(frozen=True, slots=True)
class BuiltContext:
    context: str
    sources: tuple[ContextSource, ...]
    token_count: int


def estimate_tokens(text: str) -> int:
    """Conservative fallback when no tokenizer is supplied.

    This is an estimate, not an Ollama-exact token count. The LLM context window
    is configured separately and callers should keep headroom.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def _truncate_to_budget(
    text: str,
    budget: int,
    counter: TokenCounter,
) -> str:
    if budget <= 0:
        return ""
    if counter(text) <= budget:
        return text

    low = 0
    high = len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if counter(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip()


def _source_block(source: ContextSource) -> str:
    page = (
        str(source.page_start)
        if source.page_start == source.page_end
        else f"{source.page_start}-{source.page_end}"
    )
    return (
        f"SOURCE {source.source_id}\n"
        f"Document: {source.document_name}\n"
        f"Document ID: {source.document_id}\n"
        f"Chunk ID: {source.chunk_id}\n"
        f"Page: {page}\n"
        f"Content:\n{source.text}\n"
    )


def build_context(
    chunks: Sequence[RankedChunk],
    *,
    max_tokens: int,
    token_counter: TokenCounter | None = None,
) -> BuiltContext:
    """Build source-delimited context without exceeding the token budget."""
    if max_tokens < 1:
        return BuiltContext(context="", sources=(), token_count=0)

    counter = token_counter or estimate_tokens
    sections: list[str] = []
    sources: list[ContextSource] = []
    used_tokens = 0

    for chunk in chunks:
        source_id = f"S{len(sources) + 1}"
        source = ContextSource(
            source_id=source_id,
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=chunk.text,
            similarity=chunk.similarity,
        )
        block = _source_block(source)
        block_tokens = counter(block)
        remaining = max_tokens - used_tokens

        if block_tokens <= remaining:
            sections.append(block)
            sources.append(source)
            used_tokens += block_tokens
            continue

        # Try to include a truncated first/next source rather than abandoning
        # all evidence because one chunk is oversized.
        header = block[: block.index("Content:\n") + len("Content:\n")]
        header_tokens = counter(header)
        content_budget = remaining - header_tokens
        if content_budget <= 1:
            continue
        truncated = _truncate_to_budget(chunk.text, content_budget, counter)
        if not truncated:
            continue
        truncated_source = ContextSource(
            source_id=source_id,
            document_id=chunk.document_id,
            document_name=chunk.document_name,
            chunk_id=chunk.chunk_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            text=truncated,
            similarity=chunk.similarity,
        )
        truncated_block = _source_block(truncated_source)
        truncated_tokens = counter(truncated_block)
        if truncated_tokens > remaining:
            continue
        sections.append(truncated_block)
        sources.append(truncated_source)
        used_tokens += truncated_tokens

    context = "\n".join(sections).strip()
    return BuiltContext(
        context=context,
        sources=tuple(sources),
        token_count=counter(context) if context else 0,
    )
