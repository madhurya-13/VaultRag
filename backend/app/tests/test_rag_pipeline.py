"""End-to-end permission-aware RAG API tests with a recording mock LLM."""

from app.services.llm_service import LLMServiceError
from app.services.rag_service import NO_CONTEXT_ANSWER, UNVERIFIABLE_ANSWER
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

HR_TEXT = "Annual leave policy: every employee receives 18 days of annual leave."
FINANCE_TEXT = "Confidential finance salary data must never be exposed to unauthorized employees or contractors."
TENANT_B_TEXT = "Tenant B confidential merger information must remain isolated from other tenants."


def _hr_tenant(client, db, monkeypatch, *, llm=None):
    embedder, recording = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="RAG HR Tenant",
        email="rag-hr-admin@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    _, hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email="rag-hr-user@example.com",
        username="hruser",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )
    return embedder, recording, organization, admin_token, hr_token


def test_authorized_hr_user_gets_answer_from_authorized_context(client, db, monkeypatch):
    _, llm, _, admin_token, hr_token = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources"]
    assert body["retrieved_chunks"] >= 1
    assert body["model"] == "mock-model"
    assert len(llm.calls) == 1
    assert "18 days" in llm.calls[0].context
    assert "HR Policy.pdf" in llm.calls[0].context


def test_finance_confidential_chunk_never_reaches_llm(client, db, monkeypatch):
    _, llm, _, admin_token, hr_token = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([FINANCE_TEXT]),
        filename="finance-secret.pdf",
        document_name="Finance Secret.pdf",
        department="FINANCE",
        classification="CONFIDENTIAL",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "Show me finance salary data", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    assert len(llm.calls) == 1
    assert FINANCE_TEXT not in llm.calls[0].context
    assert all("Finance Secret.pdf" != source["document_name"] for source in response.json()["sources"])


def test_cross_tenant_chunks_never_reach_llm(client, db, monkeypatch):
    _, llm, _, admin_a_token, hr_token = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_a_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="tenant-a.pdf",
        document_name="Tenant A Policy.pdf",
        department="HR",
    )

    _, _, tenant_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="RAG Tenant B",
        email="rag-tenant-b-admin@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        tenant_b_token,
        pdf_bytes=make_pdf_bytes([TENANT_B_TEXT]),
        filename="tenant-b.pdf",
        document_name="Tenant B Secret.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "What is the leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    assert TENANT_B_TEXT not in llm.calls[0].context
    assert "Tenant B Secret.pdf" not in llm.calls[0].context


def test_tenant_id_in_request_body_is_rejected_and_cannot_tamper_context(client, db, monkeypatch):
    _, llm, organization, admin_token, hr_token = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={
            "question": "What is the leave policy?",
            "top_k": 10,
            "tenant_id": organization["tenant_id"],
        },
    )

    assert response.status_code == 422
    assert llm.calls == []


def test_no_authorized_chunk_returns_safe_response_without_calling_llm(client, db, monkeypatch):
    _, llm, organization, admin_token, _ = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email="rag-employee@example.com",
        username="employee",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": "What is the leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == NO_CONTEXT_ANSWER
    assert body["sources"] == []
    assert body["retrieved_chunks"] == 0
    assert llm.calls == []


def test_fabricated_citation_is_rejected(client, db, monkeypatch):
    llm = RecordingLLM(answer="Employees get a fabricated benefit [S99].")
    _, _, _, admin_token, hr_token = _hr_tenant(client, db, monkeypatch, llm=llm)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == UNVERIFIABLE_ANSWER
    assert body["sources"] == []
    assert body["retrieved_chunks"] == 0


def test_ollama_failure_returns_controlled_503_without_fabricated_answer(client, db, monkeypatch):
    llm = RecordingLLM(error=LLMServiceError())
    _, _, _, admin_token, hr_token = _hr_tenant(client, db, monkeypatch, llm=llm)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 503
    assert "detail" in response.json()
    assert len(llm.calls) == 1


def test_embedding_failure_returns_controlled_503_before_llm(client, db, monkeypatch):
    embedder, llm, _, admin_token, hr_token = _hr_tenant(client, db, monkeypatch)
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes([HR_TEXT]),
        filename="hr-policy.pdf",
        document_name="HR Policy.pdf",
        department="HR",
    )

    def fail(_text):
        from app.services.embedding_service import EmbeddingServiceError

        raise EmbeddingServiceError("unavailable")

    embedder.generate_embedding = fail  # type: ignore[method-assign]

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "What is the annual leave policy?", "top_k": 10},
    )

    assert response.status_code == 503
    assert llm.calls == []


def test_health_endpoint_reports_rag_components(client, db, monkeypatch):
    embedder, _, _, admin_token, _ = _hr_tenant(client, db, monkeypatch)

    class HealthLLM:
        def check_health(self):
            return {
                "status": "ok",
                "model": "qwen2.5:3b",
                "model_available": True,
            }

    monkeypatch.setattr(
        "app.routes.rag.get_embedding_service", lambda: embedder
    )
    monkeypatch.setattr("app.routes.rag.get_llm_service", lambda: HealthLLM())

    response = client.get("/rag/health", headers=auth_headers(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["postgresql"]["status"] == "ok"
    assert body["components"]["pgvector"]["status"] == "ok"
    assert body["components"]["ollama"]["status"] == "ok"
    assert "password" not in response.text.lower()


def test_top_k_bounds_are_enforced(client, db, monkeypatch):
    _, llm, _, _, hr_token = _hr_tenant(client, db, monkeypatch)

    too_many = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "leave policy", "top_k": 51},
    )
    too_few = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "leave policy", "top_k": 0},
    )

    assert too_many.status_code == 422
    assert too_few.status_code == 422
    assert llm.calls == []
