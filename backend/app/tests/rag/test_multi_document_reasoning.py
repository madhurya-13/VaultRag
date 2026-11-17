"""Multi-document evidence grouping and conflict detection."""

from uuid import uuid4

from app.services.context_builder import ContextSource
from app.services.rag.multi_document_reasoner import (
    detect_conflicts,
    reasoning_instructions,
)
from app.services.rag.query_understanding import analyze_query
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    patch_rag_runtime,
    upload_processed_pdf,
)


def _source(text: str, document_id=None, source_id: str = "S1") -> ContextSource:
    return ContextSource(
        source_id=source_id,
        document_id=document_id or uuid4(),
        document_name="Policy.pdf",
        chunk_id=uuid4(),
        page_start=1,
        page_end=1,
        text=text,
        similarity=0.8,
    )


def test_conflicting_numeric_values_across_documents_are_reported():
    first = _source(
        "Employees receive 18 days of annual leave per year.", source_id="S1"
    )
    second = _source(
        "Employees receive 25 days of annual leave per year.", source_id="S2"
    )

    conflicts = detect_conflicts([first, second])

    assert len(conflicts) == 1
    assert conflicts[0].topic == "annual leave"
    assert set(conflicts[0].values) == {"18 days", "25 days"}
    assert {source.source_id for source in conflicts[0].sources} == {"S1", "S2"}


def test_one_document_cannot_conflict_with_itself():
    first = _source("Employees receive 18 days of annual leave.")
    second = _source(
        "Contractors receive 25 days of annual leave.",
        document_id=first.document_id,
        source_id="S2",
    )

    assert detect_conflicts([first, second]) == ()


def test_approval_disagreement_is_detected_across_documents():
    first = _source("Travel reimbursement requires approval by the manager.")
    second = _source("Travel reimbursement does not require approval.", source_id="S2")

    conflicts = detect_conflicts([first, second])

    assert any(item.topic == "approval requirement" for item in conflicts)


def test_reasoning_instructions_are_empty_for_a_simple_single_source():
    analysis = analyze_query("What is the annual leave policy?")
    assert (
        reasoning_instructions(
            analysis, conflict_count=0, distinct_documents=1
        )
        == ""
    )


def test_reasoning_instructions_do_not_claim_precedence():
    analysis = analyze_query("Compare the Finance and HR leave policies.")
    instructions = reasoning_instructions(
        analysis, conflict_count=1, distinct_documents=2
    )

    assert "both positions" in instructions
    assert "Do not declare which document prevails" in instructions
    assert "newer" not in instructions.lower()


def test_two_document_comparison_returns_grouped_evidence_and_conflicts(client, db, monkeypatch):
    llm = RecordingLLM(
        answer=(
            "Finance grants 25 days [S1]. HR grants 18 days [S2]."
        )
    )
    _, _ = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1500,
    )
    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Compare {uuid4()}",
        email=f"compare-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    finance = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(
            ["Finance annual leave policy: 25 days of annual leave per year."]
        ),
        filename="finance.pdf",
        document_name="Finance Leave.pdf",
        department="HR",
    )
    hr = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(
            ["HR annual leave policy: 18 days of annual leave per year."]
        ),
        filename="hr-leave.pdf",
        document_name="HR Leave.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": (
                "Compare the annual leave policies of the Finance and HR "
                "departments."
            ),
            "top_k": 10,
            "search_mode": "hybrid",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert {source["document_id"] for source in body["sources"]} == {
        finance["document_id"],
        hr["document_id"],
    }
    assert body["source_groups"]
    assert body["conflicts"]
    assert body["conflicts"][0]["topic"] == "annual leave"
    assert set(body["conflicts"][0]["values"]) == {"18 days", "25 days"}
    assert len(llm.calls) == 1
