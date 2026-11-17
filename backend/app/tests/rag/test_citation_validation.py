"""Citation validation, claim coverage and unauthorized citations."""

from uuid import uuid4

from app.services.context_builder import ContextSource
from app.services.rag.citation_validator import audit_answer, reauthorize_sources
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    load_context,
    patch_rag_runtime,
    upload_processed_pdf,
)


def _source(text="policy text", source_id="S1", document_id=None):
    return ContextSource(
        source_id=source_id,
        document_id=document_id or uuid4(),
        document_name="Policy.pdf",
        chunk_id=uuid4(),
        page_start=1,
        page_end=2,
        text=text,
        similarity=0.8,
    )


def test_claim_coverage_measures_uncited_factual_sentences():
    source = _source()
    audit = audit_answer(
        "Annual leave is 18 days [S1]. Employees must request leave in advance.",
        [source],
    )

    assert audit.claim_count == 2
    assert audit.cited_claim_count == 1
    assert audit.coverage == 0.5
    assert len(audit.unsupported_claims) == 1


def test_invalid_citation_is_removed_before_coverage_is_measured():
    audit = audit_answer(
        "Annual leave is eighteen days [S1]. Employees must request leave in "
        "advance [S99].",
        [_source()],
    )

    assert audit.has_valid_citation is True
    assert audit.validation.invalid_source_ids == ("S99",)
    assert "S99" not in audit.validation.answer
    assert audit.coverage == 0.5


def test_uncited_answer_has_zero_coverage_and_no_valid_citation():
    audit = audit_answer("Annual leave is 18 days.", [_source()])

    assert audit.has_valid_citation is False
    assert audit.coverage == 0.0
    assert audit.validation.sources == ()


def test_reauthorization_reports_revoked_documents(client, db, monkeypatch):
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Reauth {uuid4()}",
        email=f"reauth-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["Annual leave is 18 days."]),
        filename="reauth.pdf",
        document_name="Reauth.pdf",
        department="HR",
    )
    context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    source = ContextSource(
        source_id="S1",
        document_id=__import__("uuid").UUID(document["document_id"]),
        document_name=document["document_name"],
        chunk_id=uuid4(),
        page_start=1,
        page_end=1,
        text="Annual leave is 18 days.",
        similarity=0.9,
    )

    allowed, revoked = reauthorize_sources(db, context, [source])
    assert allowed == (source,)
    assert revoked == ()

    allowed_after, revoked_after = reauthorize_sources(
        db, context, [source], raw_attributes={"department": "HR"}
    )
    # Missing required ABAC attributes fail closed.
    assert allowed_after == ()
    assert revoked_after


def test_llm_cannot_cite_a_source_outside_the_authorized_set(client, db, monkeypatch):
    llm = RecordingLLM(
        answer="Annual leave is 18 days [S1]. A second fact [S2]."
    )
    _, _ = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Citations {uuid4()}",
        email=f"citations-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave is 18 days for employees."]),
        filename="citation.pdf",
        document_name="Citation Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "S2" not in body["answer"]
    assert all(
        source.get("matched_by") is not None for source in body["sources"]
    )
    assert body["sources"][0]["excerpt"]
    assert body["sources"][0]["page_start"] >= 1
