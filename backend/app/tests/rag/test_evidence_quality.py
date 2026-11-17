"""Qualitative evidence assessment (not a calibrated probability)."""

from uuid import uuid4

from app.services.context_builder import ContextSource
from app.services.rag.evidence_evaluator import (
    LEVEL_INSUFFICIENT,
    LEVEL_STRONG,
    assess_evidence,
)
from app.services.reranking import RankedChunk
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import patch_rag_runtime, upload_processed_pdf
from app.services.rag_service import NO_CONTEXT_ANSWER

THRESHOLDS = dict(
    min_coverage=0.25,
    strong_threshold=0.75,
    moderate_threshold=0.5,
    limited_threshold=0.25,
)


def _ranked(similarity: float, document_id=None):
    return RankedChunk(
        chunk_id=uuid4(),
        tenant_id=uuid4(),
        document_id=document_id or uuid4(),
        chunk_index=0,
        page_start=1,
        page_end=1,
        distance=1.0 - similarity,
        document_name="Policy.pdf",
        text="policy",
        similarity=similarity,
        semantic_similarity=similarity,
    )


def _source(document_id=None, source_id="S1"):
    return ContextSource(
        source_id=source_id,
        document_id=document_id or uuid4(),
        document_name="Policy.pdf",
        chunk_id=uuid4(),
        page_start=1,
        page_end=1,
        text="policy",
        similarity=0.9,
    )


def test_strong_evidence_requires_multiple_documents_and_full_coverage():
    doc_a, doc_b = uuid4(), uuid4()
    assessment = assess_evidence(
        ranked=[_ranked(0.9, doc_a), _ranked(0.8, doc_b)],
        sources=[_source(doc_a, "S1"), _source(doc_b, "S2")],
        citation_coverage=1.0,
        invalid_citations=0,
        conflict_count=0,
        **THRESHOLDS,
    )

    assert assessment.level == LEVEL_STRONG
    assert assessment.sufficient is True
    assert assessment.signals["distinct_documents"] == 2
    assert 0.0 <= assessment.score <= 1.0


def test_low_citation_coverage_is_insufficient():
    assessment = assess_evidence(
        ranked=[_ranked(0.9)],
        sources=[_source()],
        citation_coverage=0.0,
        invalid_citations=0,
        conflict_count=0,
        **THRESHOLDS,
    )

    assert assessment.level == LEVEL_INSUFFICIENT
    assert assessment.sufficient is False


def test_conflicts_reduce_the_score_without_hiding_sources():
    doc_a, doc_b = uuid4(), uuid4()
    without_conflict = assess_evidence(
        ranked=[_ranked(0.9, doc_a), _ranked(0.8, doc_b)],
        sources=[_source(doc_a, "S1"), _source(doc_b, "S2")],
        citation_coverage=1.0,
        invalid_citations=0,
        conflict_count=0,
        **THRESHOLDS,
    )
    with_conflict = assess_evidence(
        ranked=[_ranked(0.9, doc_a), _ranked(0.8, doc_b)],
        sources=[_source(doc_a, "S1"), _source(doc_b, "S2")],
        citation_coverage=1.0,
        invalid_citations=1,
        conflict_count=2,
        **THRESHOLDS,
    )

    assert with_conflict.score < without_conflict.score
    assert with_conflict.signals["source_count"] == 2


def test_invalid_citations_lower_quality():
    doc = uuid4()
    clean = assess_evidence(
        ranked=[_ranked(0.6, doc)],
        sources=[_source(doc)],
        citation_coverage=1.0,
        invalid_citations=0,
        conflict_count=0,
        **THRESHOLDS,
    )
    dirty = assess_evidence(
        ranked=[_ranked(0.6, doc)],
        sources=[_source(doc)],
        citation_coverage=1.0,
        invalid_citations=3,
        conflict_count=0,
        **THRESHOLDS,
    )

    assert dirty.score < clean.score


def test_no_sources_is_insufficient_evidence(client, db, monkeypatch):
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Evidence {uuid4()}",
        email=f"evidence-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy: 18 days."]),
        filename="evidence.pdf",
        document_name="Evidence.pdf",
        department="HR",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"evidence-employee-{uuid4()}@example.com",
        username="evidenceuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == NO_CONTEXT_ANSWER
    assert body["insufficient_evidence"] is True
    assert body["evidence"]["level"] == "insufficient"
    assert body["sources"] == []
    assert llm.calls == []


def test_successful_answer_exposes_a_qualitative_indicator(client, db, monkeypatch):
    _, _ = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Indicator {uuid4()}",
        email=f"indicator-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy: 18 days of annual leave."]),
        filename="indicator.pdf",
        document_name="Indicator.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evidence"]["level"] in {
        "strong",
        "moderate",
        "limited",
    }
    assert 0.0 <= body["evidence"]["score"] <= 1.0
    assert "distinct_documents" in body["evidence"]["signals"]
    assert body["insufficient_evidence"] is False
