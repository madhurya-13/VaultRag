"""Reciprocal Rank Fusion and hybrid retrieval security boundaries."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.rag import RAGFilters
from app.services.rag.hybrid_retriever import (
    fuse_candidate_lists,
    reciprocal_rank_fusion,
)
from app.services.vector_retrieval import CandidateChunk
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    patch_rag_runtime,
    upload_processed_pdf,
)


def _candidate(*, chunk_id=None, document_id=None, index=0, similarity=0.5, keyword=None, sources=("semantic",)):
    return CandidateChunk(
        chunk_id=chunk_id or uuid4(),
        tenant_id=uuid4(),
        document_id=document_id or uuid4(),
        chunk_index=index,
        page_start=1,
        page_end=1,
        distance=1.0 - (similarity or 0.0),
        document_name="Policy.pdf",
        department="HR",
        classification="INTERNAL",
        required_clearance_level=1,
        document_is_active=True,
        processing_status="COMPLETED",
        embedding_model="model@v1",
        semantic_similarity=similarity,
        keyword_score=keyword,
        retrieval_sources=sources,
    )


def test_rrf_prefers_chunks_found_by_both_retrievers():
    shared = _candidate(similarity=0.4, keyword=0.2, sources=("semantic", "keyword"))
    semantic_only = _candidate(similarity=0.9)
    keyword_only = _candidate(similarity=None, keyword=0.9, sources=("keyword",))

    fused = fuse_candidate_lists(
        semantic_lists=[[semantic_only, shared]],
        keyword_lists=[[shared, keyword_only]],
        rrf_k=60,
        limit=10,
    )

    assert fused[0].chunk_id == shared.chunk_id
    assert fused[0].retrieval_sources == ("keyword", "semantic")
    assert {item.chunk_id for item in fused} == {
        shared.chunk_id,
        semantic_only.chunk_id,
        keyword_only.chunk_id,
    }
    assert all(item.fused_score and item.fused_score > 0 for item in fused)


def test_rrf_scores_are_rank_based_and_bounded_by_limit():
    scores = reciprocal_rank_fusion([[uuid4(), uuid4()]], rrf_k=10)
    assert len(scores) == 2
    assert max(scores.values()) == pytest.approx(1 / 11)
    assert fuse_candidate_lists(
        semantic_lists=[[_candidate(), _candidate()]], rrf_k=60, limit=1
    ).__len__() == 1


def test_metadata_filters_reject_unknown_or_privileged_keys():
    with pytest.raises(ValidationError):
        RAGFilters(department="NOT_A_DEPARTMENT")
    with pytest.raises(ValidationError):
        RAGFilters(status="DELETED")
    with pytest.raises(ValidationError):
        RAGFilters(classification="TOP_SECRET")
    with pytest.raises(ValidationError):
        RAGFilters(tenant_id=str(uuid4()))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RAGFilters(date_from="2026-02-01T00:00:00Z", date_to="2026-01-01T00:00:00Z")


def _hybrid_tenant(client, db, monkeypatch, *, llm=None):
    embedder, recording = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Hybrid {uuid4()}",
        email=f"hybrid-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    return embedder, recording, organization, admin, token


def test_hybrid_retrieval_cannot_cross_tenant_boundaries(client, db, monkeypatch):
    _, llm, organization, admin, token = _hybrid_tenant(client, db, monkeypatch)
    own_doc = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["HR-ALPHA-MARKER annual leave is 18 days for employees."]),
        filename="alpha.pdf",
        document_name="Alpha HR.pdf",
        department="HR",
    )
    _, _, other_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Other {uuid4()}",
        email=f"other-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        other_token,
        pdf_bytes=make_pdf_bytes(["HR-BETA-MARKER annual leave is 30 days for employees."]),
        filename="beta.pdf",
        document_name="Beta HR.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": "HR-BETA-MARKER annual leave", "top_k": 10, "search_mode": "hybrid"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [source["document_id"] for source in body["sources"]] == [own_doc["document_id"]]
    assert "HR-BETA-MARKER" not in llm.calls[0].context
    assert "HR-ALPHA-MARKER" in llm.calls[0].context


def test_metadata_filter_narrows_authorized_results_only(client, db, monkeypatch):
    _, llm, organization, admin, token = _hybrid_tenant(client, db, monkeypatch)
    hr_doc = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["HR-MARKER annual leave policy for HR staff."]),
        filename="hr.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["ENGINEERING-MARKER annual leave policy for engineers."]),
        filename="eng.pdf",
        document_name="Engineering Policy.pdf",
        department="ENGINEERING",
    )

    filtered = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "annual leave policy",
            "top_k": 10,
            "search_mode": "hybrid",
            "filters": {"department": "HR"},
        },
    )

    assert filtered.status_code == 200, filtered.text
    assert [item["document_id"] for item in filtered.json()["sources"]] == [
        hr_doc["document_id"]
    ]
    assert "ENGINEERING-MARKER" not in llm.calls[-1].context


def test_keyword_candidates_cannot_bypass_rls_without_tenant_context(client, db, monkeypatch):
    _, _, organization, admin, token = _hybrid_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["RLS-MARKER annual leave policy."]),
        filename="rls.pdf",
        document_name="RLS Policy.pdf",
        department="HR",
    )

    from app.core.database import SessionLocal
    from app.services.rag.keyword_retriever import retrieve_keyword_candidates
    from app.tests.rag_helpers import load_context

    context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    with SessionLocal() as session:
        # Deliberately no set_tenant_context(): forced RLS must return nothing.
        candidates = retrieve_keyword_candidates(
            session,
            context,
            "RLS-MARKER",
            top_k=10,
            model_label="fake-test-model@test",
            role_ids=frozenset(),
        )
    assert candidates == []


def test_unauthorized_keyword_match_never_reaches_llm(client, db, monkeypatch):
    _, llm, organization, _, admin_token = _hybrid_tenant(client, db, monkeypatch)
    document = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["SECRET-KEYWORD bonus pool details for executives."]),
        filename="secret.pdf",
        document_name="Secret.pdf",
        department="HR",
        classification="CONFIDENTIAL",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"employee-{uuid4()}@example.com",
        username="employee",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": "SECRET-KEYWORD bonus pool", "top_k": 10, "search_mode": "keyword"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == []
    assert llm.calls == []
    assert document["document_id"]
