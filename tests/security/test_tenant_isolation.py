"""Cross-tenant access and pre-LLM isolation attack simulations."""
from app.tests.test_rag_pipeline import (
    test_cross_tenant_chunks_never_reach_llm,
    test_tenant_id_in_request_body_is_rejected_and_cannot_tamper_context,
)
from app.tests.test_rag_security import test_concurrent_tenant_queries_do_not_leak
from app.tests.test_tenant_isolation import test_cross_tenant_document_access_and_audit_are_isolated
from uuid import uuid4

from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    read_document,
    revoke_document_permission,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    find_role_permission,
    patch_rag_runtime,
    upload_processed_pdf,
)

__all__ = [
    "test_cross_tenant_chunks_never_reach_llm",
    "test_tenant_id_in_request_body_is_rejected_and_cannot_tamper_context",
    "test_concurrent_tenant_queries_do_not_leak",
    "test_cross_tenant_document_access_and_audit_are_isolated",
    "test_two_tenant_retrieval_then_immediate_permission_revocation",
]


def test_two_tenant_retrieval_then_immediate_permission_revocation(
    client, db, monkeypatch
):
    """One sequence proves cross-tenant exclusion and same-query revocation."""
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    org_a, _, admin_a_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Finance demo {uuid4()}",
        email=f"finance-admin-{uuid4()}@example.com",
        username="alice",
        department="FINANCE",
        clearance="5",
    )
    org_b, _, admin_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"HR demo {uuid4()}",
        email=f"hr-admin-{uuid4()}@example.com",
        username="charlie",
        department="HR",
        clearance="5",
    )
    _, finance_employee_token = tenant_user_with_attributes(
        client,
        admin_a_token,
        org_a["tenant_id"],
        email=f"finance-user-{uuid4()}@example.com",
        username="bob",
        role_names=["FINANCE"],
        department="FINANCE",
        clearance="3",
    )
    finance_doc = upload_processed_pdf(
        client,
        admin_a_token,
        pdf_bytes=make_pdf_bytes(
            ["Synthetic travel expense policy requires a receipt and manager approval. FINANCE-ONLY-ALPHA"]
        ),
        filename="finance-policy.pdf",
        document_name="Finance Policy Synthetic.pdf",
        department="FINANCE",
    )
    hr_doc = upload_processed_pdf(
        client,
        admin_b_token,
        pdf_bytes=make_pdf_bytes(
            [
                "Synthetic HR confidential leave policy explains employee leave "
                "approval and annual balances. HR-ONLY-BETA"
            ]
        ),
        filename="hr-policy.pdf",
        document_name="HR Policy Synthetic.pdf",
        department="HR",
    )

    query = {
        "question": "What is required for an expense policy approval?",
        "top_k": 10,
    }
    before_revoke = client.post(
        "/rag/ask", headers=auth_headers(finance_employee_token), json=query
    )
    assert before_revoke.status_code == 200, before_revoke.text
    assert [item["document_id"] for item in before_revoke.json()["sources"]] == [
        finance_doc["document_id"]
    ]
    assert "FINANCE-ONLY-ALPHA" in llm.calls[0].context
    assert "HR-ONLY-BETA" not in llm.calls[0].context
    assert "HR Policy Synthetic.pdf" not in llm.calls[0].context

    guessed_document = read_document(
        client, finance_employee_token, hr_doc["document_id"]
    )
    assert guessed_document.status_code == 404
    tenant_tamper = client.post(
        "/rag/ask",
        headers=auth_headers(finance_employee_token),
        json={**query, "tenant_id": org_b["tenant_id"]},
    )
    assert tenant_tamper.status_code == 422
    assert len(llm.calls) == 1

    permission = find_role_permission(
        db,
        tenant_id=org_a["tenant_id"],
        document_id=finance_doc["document_id"],
        role_name="FINANCE",
    )
    revoke_document_permission(
        client,
        admin_a_token,
        finance_doc["document_id"],
        permission.permission_id,
    )
    after_revoke = client.post(
        "/rag/ask", headers=auth_headers(finance_employee_token), json=query
    )
    assert after_revoke.status_code == 200, after_revoke.text
    assert after_revoke.json()["sources"] == []
    assert len(llm.calls) == 1
