"""Context construction from authorized chunks only."""

from uuid import uuid4

from app.services.context_builder import build_context
from app.services.reranking import RankedChunk


def _ranked(text: str, *, page_start=1, page_end=1, name="Policy.pdf"):
    return RankedChunk(
        chunk_id=uuid4(),
        tenant_id=uuid4(),
        document_id=uuid4(),
        chunk_index=0,
        page_start=page_start,
        page_end=page_end,
        distance=0.1,
        document_name=name,
        text=text,
        similarity=0.9,
    )


def test_context_contains_backend_source_ids_and_attribution():
    built = build_context(
        [_ranked("Annual leave is 18 days.", page_start=2, page_end=2)],
        max_tokens=200,
        token_counter=lambda text: len(text.split()),
    )

    assert len(built.sources) == 1
    source = built.sources[0]
    assert source.source_id == "S1"
    assert source.document_name == "Policy.pdf"
    assert source.page_start == 2
    assert source.page_end == 2
    assert "SOURCE S1" in built.context
    assert "Document: Policy.pdf" in built.context
    assert str(source.document_id) in built.context
    assert str(source.chunk_id) in built.context
    assert "Content:" in built.context


def test_context_is_limited_and_truncates_oversized_first_chunk():
    built = build_context(
        [_ranked("word " * 200)],
        max_tokens=40,
        token_counter=lambda text: len(text.split()),
    )

    assert built.sources
    assert built.token_count <= 40
    assert built.context.endswith("]") is False
    assert len(built.sources[0].text.split()) < 200


def test_empty_budget_returns_no_context():
    built = build_context(
        [_ranked("policy")],
        max_tokens=0,
        token_counter=lambda text: len(text.split()),
    )
    assert built.context == ""
    assert built.sources == ()
    assert built.token_count == 0
