"""Safe, deterministic query rewriting and expansion."""

from uuid import uuid4

from app.services.rag.query_rewriter import expand_queries, rewrite_question
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


def test_ambiguous_followup_is_rewritten_with_domain_context():
    analysis = analyze_query("What about the leave policy?")
    assert analysis.refers_to_context is True

    rewritten = rewrite_question("What about the leave policy?", analysis)

    assert "leave" in rewritten.lower()
    assert "annual leave allowance" in rewritten
    assert "approval process" in rewritten


def test_rewrite_preserves_identifiers_and_bounds_length():
    analysis = analyze_query("What about policy EMP-4471?")
    rewritten = rewrite_question("What about policy EMP-4471?", analysis, max_chars=80)

    assert "EMP-4471" in rewritten
    assert len(rewritten) <= 80


def test_rewrite_uses_conversation_topic_only_for_context_references():
    analysis = analyze_query("How many days can I take?")
    assert analysis.refers_to_context is True

    rewritten = rewrite_question(
        "How many days can I take?",
        analysis,
        conversation_topic="annual leave policy",
        max_chars=200,
    )
    assert rewritten.lower().startswith("annual leave policy")

    standalone = analyze_query("What is the capital gains tax rate?")
    assert standalone.refers_to_context is False
    standalone_rewritten = rewrite_question(
        "What is the capital gains tax rate?", standalone
    )
    # Standalone questions may lose a leading filler ("What is the") but must
    # never inherit a conversation topic they do not refer to.
    assert "capital gains tax rate" in standalone_rewritten.lower()
    assert "annual leave" not in standalone_rewritten.lower()


def test_expansion_is_capped_and_does_not_duplicate_the_rewrite():
    question = "Employee reimbursement rules"
    analysis = analyze_query(question)
    expansions = expand_queries(question, analysis, max_expansions=2)

    assert len(expansions) == 2
    assert all("reimburs" in item.lower() for item in expansions)
    assert all(item.lower() not in question.lower() for item in expansions)
    assert expand_queries(question, analysis, max_expansions=0) == ()


def test_rewriting_produces_search_text_only_not_authorization_fields():
    analysis = analyze_query("What about the leave policy?")
    rewritten = rewrite_question("What about the leave policy?", analysis)

    lowered = rewritten.lower()
    for forbidden in ("tenant", "role", "permission", "clearance", "document_id"):
        assert forbidden not in lowered


def test_conversation_topic_drives_followup_retrieval(client, db, monkeypatch):
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Rewrite {uuid4()}",
        email=f"rewrite-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(
            ["Annual leave policy: employees receive 18 days of annual leave."]
        ),
        filename="leave.pdf",
        document_name="Leave Policy.pdf",
        department="HR",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"rewrite-employee-{uuid4()}@example.com",
        username="rewriteuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )
    from app.tests.helpers import grant_document_permission

    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        user_id=None,
        role_name="EMPLOYEE",
    )

    conversation = client.post(
        "/rag/conversations",
        headers=auth_headers(employee_token),
        json={"title": "Leave questions"},
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["conversation_id"]

    first = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={
            "question": "What is the employee leave policy?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["sources"]

    second = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={
            "question": "How many days can I take?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )

    assert second.status_code == 200, second.text
    assert second.json()["sources"]
    assert len(llm.calls) == 2
