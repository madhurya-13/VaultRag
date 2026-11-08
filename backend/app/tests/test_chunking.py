"""Token-aware chunking tests using a deterministic word tokenizer."""

import pytest

from app.services.chunking import (
    ChunkingConfig,
    NoChunksGeneratedError,
    chunk_pages,
)
from app.tests.helpers import WhitespaceTokenizer


def _words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{index:03d}" for index in range(count))


def test_chunk_pages_is_ordered_and_respects_token_limits_and_overlap():
    tokenizer = WhitespaceTokenizer()
    pages = [(1, _words("a", 40)), (2, _words("b", 40))]
    config = ChunkingConfig(
        chunk_size_tokens=20,
        chunk_overlap_tokens=5,
        min_tokens=5,
        max_tokens=30,
    )

    chunks = chunk_pages(pages, tokenizer, config)

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert len(chunks) >= 4
    assert all(chunk.chunk_text.strip() for chunk in chunks)
    assert all(chunk.token_count <= config.max_tokens for chunk in chunks)
    assert all(chunk.page_start >= 1 for chunk in chunks)
    assert all(chunk.page_end <= 2 for chunk in chunks)
    assert all(chunk.page_end >= chunk.page_start for chunk in chunks)

    # Consecutive windows overlap by the configured token count.
    for previous, current in zip(chunks, chunks[1:]):
        previous_words = set(previous.chunk_text.split())
        current_words = set(current.chunk_text.split())
        assert previous_words & current_words


def test_chunk_pages_tracks_page_ranges_across_page_boundary():
    tokenizer = WhitespaceTokenizer()
    pages = [(1, _words("x", 30)), (2, _words("y", 30))]
    config = ChunkingConfig(
        chunk_size_tokens=40,
        chunk_overlap_tokens=10,
        min_tokens=5,
        max_tokens=40,
    )

    chunks = chunk_pages(pages, tokenizer, config)

    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2
    assert chunks[-1].page_end == 2


def test_chunk_pages_returns_one_chunk_for_short_document():
    tokenizer = WhitespaceTokenizer()
    chunks = chunk_pages(
        [(1, "short document")],
        tokenizer,
        ChunkingConfig(
            chunk_size_tokens=20,
            chunk_overlap_tokens=5,
            min_tokens=2,
            max_tokens=30,
        ),
    )

    assert len(chunks) == 1
    assert chunks[0].chunk_text == "short document"
    assert chunks[0].token_count == 2


def test_chunk_pages_rebuilds_tiny_trailing_window_with_zero_overlap():
    tokenizer = WhitespaceTokenizer()
    chunks = chunk_pages(
        [(1, _words("z", 22))],
        tokenizer,
        ChunkingConfig(
            chunk_size_tokens=10,
            chunk_overlap_tokens=0,
            min_tokens=5,
            max_tokens=10,
        ),
    )

    assert len(chunks) == 3
    assert chunks[-1].token_count == 5


def test_chunk_pages_rejects_whitespace_only_document():
    with pytest.raises(NoChunksGeneratedError):
        chunk_pages(
            [(1, "   \n\t "), (2, "")],
            WhitespaceTokenizer(),
            ChunkingConfig(
                chunk_size_tokens=10,
                chunk_overlap_tokens=2,
                min_tokens=2,
                max_tokens=10,
            ),
        )


def test_chunking_config_validation():
    with pytest.raises(ValueError):
        ChunkingConfig(
            chunk_size_tokens=10,
            chunk_overlap_tokens=10,
            min_tokens=2,
            max_tokens=20,
        )
    with pytest.raises(ValueError):
        ChunkingConfig(
            chunk_size_tokens=10,
            chunk_overlap_tokens=2,
            min_tokens=11,
            max_tokens=20,
        )
