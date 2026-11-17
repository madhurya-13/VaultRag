"""Query decomposition and subquery authorization boundaries."""

from uuid import uuid4

from app.services.rag.query_decomposer import build_query_plan, decompose_question
from app.services.rag.query_understanding import analyze_query
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

COMPLEX = (
    "Compare the leave policies of the Finance and HR departments and identify "
    "differences in annual leave and approval procedures."
)


def test_comparison_question_is_decomposed_by_department_and_aspect():
    analysis = analyze_query(COMPLEX)
    subquestions = decompose_question(COMPLEX, analysis, max_subqueries=4)

    assert len(subquestions) == 4
    assert any("Finance" in item for item in subquestions)
    assert any("HR" in item for item in subquestions)
    assert any("annual leave" in item for item in subquestions)
    assert any("approval" in item for item in subquestions)


def test_simple_question_is_not_decomposed():
    question = "What is the annual leave policy?"
    plan = build_query_plan(question, max_expansions=0)

    assert plan.subquestions == (plan.rewritten,)
    assert len(plan.retrieval_queries) == 1


def test_decomposition_respects_the_configured_cap():
    analysis = analyze_query(COMPLEX)
    assert len(decompose_question(COMPLEX, analysis, max_subqueries=2)) == 2
    assert decompose_question(COMPLEX, analysis, max_subqueries=1) == ()


def test_retrieval_queries_are_deduplicated_and_bounded():
    plan = build_query_plan(
        COMPLEX, max_expansions=2, max_subqueries=4, max_chars=400
    )

    assert len(plan.retrieval_queries) == len(set(plan.retrieval_queries))
    assert len(plan.retrieval_queries) <= 7
    assert all(len(query) <= 400 for query in plan.retrieval_queries)


def test_decomposed_subqueries_cannot_reach_unauthorized_documents(client, db, monkeypatch):
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1200
    )
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Decompose {uuid4()}",
        email=f"decompose-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    from app.tests.helpers import grant_document_permission

    finance_doc = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(
            ["FINANCE-LEAVE-MARKER Finance annual leave is 20 days; approval by Finance head."]
        ),
        filename="finance.pdf",
        document_name="Finance Leave.pdf",
        department="HR",
    )
    hr_doc = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(
            ["HR-LEAVE-MARKER HR annual leave is 18 days; approval by HR manager."]
        ),
        filename="hr.pdf",
        document_name="HR Leave.pdf",
        department="HR",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"decompose-employee-{uuid4()}@example.com",
        username="decompuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )
    grant_document_permission(
        client,
        admin_token,
        hr_doc["document_id"],
        role_name="EMPLOYEE",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": COMPLEX, "top_k": 10, "search_mode": "hybrid"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [source["document_id"] for source in body["sources"]] == [
        hr_doc["document_id"]
    ]
    assert "FINANCE-LEAVE-MARKER" not in llm.calls[0].context
    assert "HR-LEAVE-MARKER" in llm.calls[0].context
    assert finance_doc["document_id"] not in {
        source["document_id"] for source in body["sources"]
    }
