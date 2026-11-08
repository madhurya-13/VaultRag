"""Token-aware document chunking.

The algorithm works on the embedding model's token IDs, never on character
counts. A sliding window produces chunks of at most ``chunk_size_tokens`` with
the configured token overlap. Every chunk keeps the minimum and maximum page
number of the tokens it contains.

The tokenizer is injected so unit tests can run without downloading a model.
The production tokenizer comes from the selected Sentence Transformers model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from app.core.security_constants import (
    ERROR_CHUNKING_FAILED,
    ERROR_NO_CHUNKS_GENERATED,
)


class Tokenizer(Protocol):
    """Subset of the Hugging Face tokenizer interface used by chunking."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]: ...

    def decode(
        self,
        token_ids: Sequence[int],
        skip_special_tokens: bool = True,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    chunk_size_tokens: int = 500
    chunk_overlap_tokens: int = 100
    min_tokens: int = 50
    max_tokens: int = 700

    def __post_init__(self) -> None:
        if self.chunk_size_tokens <= 0:
            raise ValueError("chunk_size_tokens must be positive")
        if self.chunk_overlap_tokens < 0:
            raise ValueError("chunk_overlap_tokens must be non-negative")
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk size")
        if self.min_tokens <= 0:
            raise ValueError("min_tokens must be positive")
        if not (self.min_tokens <= self.chunk_size_tokens <= self.max_tokens):
            raise ValueError(
                "min_tokens <= chunk_size_tokens <= max_tokens is required"
            )


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_index: int
    chunk_text: str
    page_start: int
    page_end: int
    token_count: int


class ChunkingError(Exception):
    code = ERROR_CHUNKING_FAILED
    default_message = "The document could not be split into chunks"

    def __init__(self, message: str | None = None) -> None:
        self.code = type(self).code
        self.safe_message = message or self.default_message
        super().__init__(self.safe_message)


class NoChunksGeneratedError(ChunkingError):
    code = ERROR_NO_CHUNKS_GENERATED
    default_message = "No non-empty chunks were generated"


@dataclass(frozen=True, slots=True)
class _Token:
    token_id: int
    page_number: int


def _page_token_stream(
    pages: Iterable[tuple[int, str]],
    tokenizer: Tokenizer,
) -> list[_Token]:
    stream: list[_Token] = []
    separator = tokenizer.encode("\n\n", add_special_tokens=False)
    for page_number, text in pages:
        for token_id in tokenizer.encode(text, add_special_tokens=False):
            stream.append(_Token(token_id=token_id, page_number=page_number))
        # Preserve a visible page break in decoded chunk text where the
        # tokenizer represents newlines as tokens. If it does not, the token
        # IDs remain contiguous but page metadata is still exact.
        for token_id in separator:
            stream.append(_Token(token_id=token_id, page_number=page_number))
    return stream


def _window_to_chunk(
    window: Sequence[_Token],
    tokenizer: Tokenizer,
) -> tuple[str, int, int]:
    ids = [token.token_id for token in window]
    text = tokenizer.decode(ids, skip_special_tokens=True).strip()
    page_start = min(token.page_number for token in window)
    page_end = max(token.page_number for token in window)
    return text, page_start, page_end


def chunk_pages(
    pages: Iterable[tuple[int, str]],
    tokenizer: Tokenizer,
    config: ChunkingConfig | None = None,
) -> list[ChunkDraft]:
    """Split page-labelled text into ordered, token-bounded chunks."""
    cfg = config or ChunkingConfig()
    tokens = _page_token_stream(pages, tokenizer)
    if not tokens:
        raise NoChunksGeneratedError()

    window_size = min(cfg.chunk_size_tokens, cfg.max_tokens)
    step = max(1, window_size - cfg.chunk_overlap_tokens)

    raw_chunks: list[tuple[str, int, int, int]] = []
    start = 0
    while start < len(tokens):
        end = min(start + window_size, len(tokens))
        window = tokens[start:end]
        text, page_start, page_end = _window_to_chunk(window, tokenizer)
        if text:
            raw_chunks.append((text, page_start, page_end, len(window)))
        if end >= len(tokens):
            break
        start += step

    if not raw_chunks:
        raise NoChunksGeneratedError()

    # With a very small overlap, the final window can be shorter than the
    # configured minimum. Rebuild that final window backwards to the minimum
    # size rather than emitting a tiny trailing fragment. A document shorter
    # than the minimum produces one chunk; dropping it would lose content.
    if len(raw_chunks) > 1 and raw_chunks[-1][3] < cfg.min_tokens:
        last_start = max(0, len(tokens) - cfg.min_tokens)
        last_window = tokens[last_start:]
        text, page_start, page_end = _window_to_chunk(last_window, tokenizer)
        if text:
            raw_chunks[-1] = (text, page_start, page_end, len(last_window))
        else:
            raw_chunks.pop()

    if not raw_chunks:
        raise NoChunksGeneratedError()

    return [
        ChunkDraft(
            chunk_index=index,
            chunk_text=text,
            page_start=page_start,
            page_end=page_end,
            token_count=token_count,
        )
        for index, (text, page_start, page_end, token_count) in enumerate(raw_chunks)
    ]
