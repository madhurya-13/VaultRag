"""Relevance ranking over already-authorized chunks."""

from uuid import uuid4

from app.services.reranking import rerank_authorized_chunks
from app.services.retrieval_authorization import AuthorizedChunk


def _chunk(text: str, *, distance: float, document_id=None, index: int = 0):
    return AuthorizedChunk(
        chunk_id=uuid4(),
        tenant_id=uuid4(),
        document_id=document_id or uuid4(),
        chunk_index=index,
        page_start=1,
        page_end=1,
        distance=distance,
        document_name="Policy.pdf",
        text=text,
    )


def test_threshold_top_n_and_document_attribution_are_preserved():
    high = _chunk("annual leave policy", distance=0.05)
    medium = _chunk("leave application process", distance=0.25)
    low = _chunk("unrelated expense policy", distance=0.9)

    result = rerank_authorized_chunks(
        [low, medium, high],
        min_similarity=0.5,
        top_n=2,
    )

    assert [item.chunk_id for item in result] == [high.chunk_id, medium.chunk_id]
    assert all(item.document_name == "Policy.pdf" for item in result)
    assert all(item.page_start == 1 and item.page_end == 1 for item in result)


def test_duplicate_chunks_and_duplicate_text_are_removed():
    first = _chunk("identical policy text", distance=0.1)
    duplicate_id = _chunk("another text", distance=0.1)
    duplicate_id = type(duplicate_id)(
        chunk_id=first.chunk_id,
        tenant_id=duplicate_id.tenant_id,
        document_id=duplicate_id.document_id,
        chunk_index=2,
        page_start=1,
        page_end=1,
        distance=0.1,
        document_name="Policy.pdf",
        text="another text",
    )
    duplicate_text = _chunk(
        "  identical   policy text ",
        distance=0.1,
        document_id=first.document_id,
        index=1,
    )

    result = rerank_authorized_chunks(
        [first, duplicate_id, duplicate_text],
        min_similarity=0.0,
        top_n=5,
    )

    assert len(result) == 1
    assert result[0].chunk_id == first.chunk_id


def test_optional_reranker_can_reorder_but_not_expand_the_set():
    first = _chunk("first", distance=0.1)
    second = _chunk("second", distance=0.2)

    class FakeReranker:
        def score(self, question, texts):
            assert question == "question"
            return [0.0, 1.0]

    result = rerank_authorized_chunks(
        [first, second],
        min_similarity=0.0,
        top_n=5,
        question="question",
        reranker=FakeReranker(),
    )

    assert [item.chunk_id for item in result] == [second.chunk_id, first.chunk_id]
    assert len(result) == 2
