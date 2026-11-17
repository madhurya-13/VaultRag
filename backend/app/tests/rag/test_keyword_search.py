"""PostgreSQL full-text keyword retrieval."""

from uuid import uuid4

from app.core.database import SessionLocal
from app.services.rag.keyword_retriever import (
    normalize_keyword_query,
    retrieve_keyword_candidates,
)
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    revoke_document_permission,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    find_role_permission,
    load_context,
    patch_rag_runtime,
    upload_processed_pdf,
)


def test_keyword_query_is_bounded_and_strips_control_characters():
    assert normalize_keyword_query("EMP-4471\x00 policy") == "EMP-4471 policy"
    assert normalize_keyword_query("   ") == ""
    assert normalize_keyword_query("?!..") == ""
    assert len(normalize_keyword_query("a" * 2000)) == 500


def _tenant(client, db, monkeypatch):
    _, llm = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Keyword {uuid4()}",
        email=f"keyword-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    return organization, admin, token, llm


def test_keyword_mode_finds_exact_identifier(client, db, monkeypatch):
    organization, admin, token, _ = _tenant(client, db, monkeypatch)
    document = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(
            ["Policy EMP4471 governs contractor expense claims and approvals."]
        ),
        filename="contractor.pdf",
        document_name="Contractor Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": "EMP4471", "top_k": 10, "search_mode": "keyword"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["document_id"] for item in body["sources"]] == [
        document["document_id"]
    ]
    assert body["search_mode"] == "keyword"
    assert body["sources"][0]["matched_by"] == ["keyword"]


def test_keyword_mode_returns_empty_for_punctuation_only_query(client, db, monkeypatch):
    _, _, token, llm = _tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy for employees."]),
        filename="policy.pdf",
        document_name="Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": "???", "top_k": 10, "search_mode": "keyword"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == []
    assert llm.calls == []


def test_revocation_removes_keyword_results_without_reembedding(client, db, monkeypatch):
    organization, admin, token, llm = _tenant(client, db, monkeypatch)
    document = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["REVOKE-KEYWORD annual leave is 18 days."]),
        filename="revoke.pdf",
        document_name="Revoke Policy.pdf",
        department="HR",
    )
    _, hr_token = tenant_user_with_attributes(
        client,
        token,
        organization["tenant_id"],
        email=f"hr-{uuid4()}@example.com",
        username="hruser",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )
    before = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "REVOKE-KEYWORD annual leave", "top_k": 10, "search_mode": "keyword"},
    )
    assert before.status_code == 200
    assert before.json()["sources"]

    permission = find_role_permission(
        db,
        tenant_id=organization["tenant_id"],
        document_id=document["document_id"],
        role_name="HR",
    )
    revoke_document_permission(
        client, token, document["document_id"], permission.permission_id
    )

    after = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "REVOKE-KEYWORD annual leave", "top_k": 10, "search_mode": "keyword"},
    )
    assert after.status_code == 200
    assert after.json()["sources"] == []
    assert len(llm.calls) == 1


def test_keyword_retriever_returns_no_text_before_authorization(client, db, monkeypatch):
    organization, admin, token, _ = _tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["METADATA-ONLY annual leave policy."]),
        filename="meta.pdf",
        document_name="Meta Policy.pdf",
        department="HR",
    )
    context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    with SessionLocal() as session:
        from app.core.database import set_tenant_context
        from uuid import UUID

        set_tenant_context(session, UUID(str(organization["tenant_id"])))
        candidates = retrieve_keyword_candidates(
            session,
            context,
            "METADATA-ONLY",
            top_k=10,
            model_label="fake-test-model@test",
            role_ids=frozenset(),
        )

    assert candidates
    assert not hasattr(candidates[0], "text")
    assert not hasattr(candidates[0], "chunk_text")
