"""Advanced reranking over already-authorized chunks."""

from uuid import uuid4

from app.services.rag.reranker import rerank_evidence
from app.services.retrieval_authorization import AuthorizedChunk
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    patch_rag_runtime,
    upload_processed_pdf,
)


def _chunk(text, *, document_id=None, index=0, similarity=0.8, sources=("semantic",)):
    return AuthorizedChunk(
        chunk_id=uuid4(),
        tenant_id=uuid4(),
        document_id=document_id or uuid4(),
        chunk_index=index,
        page_start=1,
        page_end=1,
        distance=1.0 - (similarity or 0.0),
        document_name="Policy.pdf",
        text=text,
        semantic_similarity=similarity,
        retrieval_sources=sources,
    )


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.calls: list[list[str]] = []

    def score(self, question, texts):
        self.calls.append(list(texts))
        return self.scores[: len(texts)]


def test_reranker_reorders_but_never_expands_the_authorized_set():
    # Explicit similarities make the pre-rerank order deterministic instead of
    # relying on a UUID tie-break.
    first = _chunk("alpha", similarity=0.9)
    second = _chunk("beta", similarity=0.1)
    reranker = FakeReranker([0.0, 1.0])

    result = rerank_evidence(
        [first, second],
        limit=5,
        question="q",
        reranker=reranker,
        min_similarity=0.0,
    )

    assert [item.chunk_id for item in result] == [second.chunk_id, first.chunk_id]
    assert len(result) == 2


def test_semantic_threshold_does_not_drop_keyword_only_candidates():
    weak_semantic = _chunk("weak", similarity=0.05, sources=("semantic",))
    keyword_only = _chunk("exact EMP-4471", similarity=None, sources=("keyword",))

    result = rerank_evidence(
        [weak_semantic, keyword_only],
        limit=5,
        min_similarity=0.5,
    )

    assert [item.chunk_id for item in result] == [keyword_only.chunk_id]


def test_adjacent_chunks_are_merged_without_crossing_documents():
    document = uuid4()
    first = _chunk("annual leave is eighteen days", document_id=document, index=0)
    second = _chunk("eighteen days and approval is required", document_id=document, index=1)
    other_document = _chunk("unrelated", index=0)

    result = rerank_evidence(
        [first, second, other_document],
        limit=5,
        min_similarity=0.0,
        merge_adjacent=True,
    )

    merged = next(item for item in result if item.document_id == document)
    assert "approval is required" in merged.text
    assert merged.text.count("eighteen days") == 1
    assert len([item for item in result if item.document_id == document]) == 1


def test_duplicate_text_and_duplicate_ids_are_removed():
    first = _chunk("identical policy text")
    same_id = _chunk("other", document_id=first.document_id, index=2)
    duplicate = _chunk(
        "  identical   policy text ", document_id=first.document_id, index=1
    )

    result = rerank_evidence(
        [first, same_id, duplicate], limit=5, min_similarity=0.0
    )

    assert len({item.chunk_id for item in result}) == len(result)
    assert len(result) == 2


def test_per_document_diversity_cap_keeps_other_documents():
    document = uuid4()
    chunks = [
        _chunk(f"chunk {index}", document_id=document, index=index)
        for index in range(4)
    ]
    other = _chunk("other document", index=0)

    result = rerank_evidence(
        [*chunks, other],
        limit=3,
        min_similarity=0.0,
        # Isolate the diversity cap: adjacency merging intentionally collapses
        # contiguous chunks from one document before the cap is applied.
        merge_adjacent=False,
        per_document_limit=2,
    )

    assert len(result) == 3
    assert sum(1 for item in result if item.document_id == document) == 2
    assert any(item.document_id == other.document_id for item in result)


def test_failed_reranker_degrades_to_deterministic_ordering():
    class BrokenReranker:
        def score(self, question, texts):
            raise RuntimeError("model unavailable")

    first = _chunk("first", similarity=0.9)
    second = _chunk("second", similarity=0.3)

    result = rerank_evidence(
        [first, second],
        limit=5,
        question="q",
        reranker=BrokenReranker(),
        min_similarity=0.0,
    )

    assert [item.chunk_id for item in result] == [first.chunk_id, second.chunk_id]


def test_reranker_never_receives_unauthorized_text(client, db, monkeypatch):
    spy = FakeReranker([1.0, 0.5, 0.0])
    _, llm = patch_rag_runtime(
        monkeypatch,
        llm=RecordingLLM(),
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
        rag_rerank_enabled=True,
    )
    monkeypatch.setattr(
        "app.services.rag_service.CrossEncoderReranker",
        lambda model_name: spy,
    )
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Rerank {uuid4()}",
        email=f"rerank-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["HR-RERANK-MARKER annual leave is 18 days."]),
        filename="hr.pdf",
        document_name="HR.pdf",
        department="HR",
    )
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["FINANCE-RERANK-MARKER executive bonuses."]),
        filename="finance.pdf",
        document_name="Finance.pdf",
        department="FINANCE",
        classification="CONFIDENTIAL",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"rerank-employee-{uuid4()}@example.com",
        username="rerankuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )
    from app.tests.helpers import grant_document_permission

    permission = grant_document_permission(
        client,
        admin_token,
        find_hr_document(client, admin_token),
        role_name="EMPLOYEE",
    )
    assert permission["permission_id"]

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": "annual leave policy", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    assert spy.calls
    assert all("FINANCE-RERANK-MARKER" not in text for text in spy.calls[0])
    assert all("FINANCE-RERANK-MARKER" not in call.context for call in llm.calls)


def find_hr_document(client, token) -> str:
    response = client.get("/documents", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    documents = [
        item for item in response.json() if item["document_name"] == "HR.pdf"
    ]
    assert len(documents) == 1
    return documents[0]["document_id"]
