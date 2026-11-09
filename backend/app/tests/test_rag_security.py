"""Milestone 4 security regressions: revocation, ABAC, RLS and concurrency."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.database import SessionLocal, set_tenant_context
from app.core.request_context import RequestMeta
from app.dependencies.authentication import AuthenticatedContext
from app.models import Document, Organization, User
from app.services.query_embedding import generate_query_embedding
from app.services.rag_service import NO_CONTEXT_ANSWER, answer_question
from app.services.vector_retrieval import retrieve_candidate_chunks
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    grant_document_permission,
    make_pdf_bytes,
    revoke_document_permission,
    tenant_user_with_attributes,
    upload_pdf,
    use_tenant,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    expire_permission,
    find_role_permission,
    load_context,
    patch_rag_runtime,
    upload_processed_pdf,
)

HR_TEXT = "Annual leave policy: every employee receives 18 days of annual leave."
TENANT_A_TEXT = "Tenant A annual leave marker for concurrent retrieval testing."
TENANT_B_TEXT = "Tenant B confidential marker for concurrent retrieval isolation testing."


def _setup(client, db, monkeypatch, *, llm=None, create_document: bool = True):
    embedder, recording = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"RAG Security {uuid4()}",
        email=f"rag-security-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = None
    if create_document:
        document = upload_processed_pdf(
            client,
            admin_token,
            pdf_bytes=make_pdf_bytes([HR_TEXT]),
            filename="hr-policy.pdf",
            document_name="HR Policy.pdf",
            department="HR",
        )
    return embedder, recording, organization, admin, admin_token, document


def test_expired_document_permission_excludes_chunk(client, db, monkeypatch):
    _, llm, organization, _, admin_token, document = _setup(client, db, monkeypatch)
    employee, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"expired-{uuid4()}@example.com",
        username="expireduser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )
    grant = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        user_id=UUID(employee["user_id"]),
    )
    expire_permission(
        db,
        tenant_id=organization["tenant_id"],
        permission_id=grant["permission_id"],
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(employee_token),
        json={"question": "annual leave", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    assert response.json()["sources"] == []
    assert llm.calls == []


def test_revocation_after_ingestion_denies_without_reembedding(client, db, monkeypatch):
    _, llm, organization, _, admin_token, document = _setup(client, db, monkeypatch)
    _, hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"revoke-{uuid4()}@example.com",
        username="revokeuser",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )

    before = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "annual leave", "top_k": 10},
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
        client, admin_token, document["document_id"], permission.permission_id
    )

    after = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "annual leave", "top_k": 10},
    )

    assert after.status_code == 200
    assert after.json()["sources"] == []
    assert len(llm.calls) == 1  # only the first, pre-revocation query called it


def test_insufficient_clearance_excludes_chunk_before_llm(client, db, monkeypatch):
    _, llm, organization, _, admin_token, _ = _setup(
        client, db, monkeypatch, create_document=False
    )
    upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["Confidential executive compensation details are restricted to authorized executives only."]),
        filename="confidential.pdf",
        document_name="Confidential.pdf",
        department="HR",
        classification="CONFIDENTIAL",
    )
    _, hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"clearance-{uuid4()}@example.com",
        username="lowclearance",
        role_names=["HR"],
        department="HR",
        clearance="1",
    )

    response = client.post(
        "/rag/ask",
        headers=auth_headers(hr_token),
        json={"question": "executive compensation", "top_k": 10},
    )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert llm.calls == []


def test_inactive_document_is_excluded(client, db, monkeypatch):
    _, llm, organization, _, admin_token, document = _setup(client, db, monkeypatch)
    use_tenant(db, organization["tenant_id"])
    db.expire_all()
    row = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(str(organization["tenant_id"])),
            Document.document_id == UUID(str(document["document_id"])),
        )
    )
    assert row is not None
    row.is_active = False
    db.commit()

    response = client.post(
        "/rag/ask",
        headers=auth_headers(admin_token),
        json={"question": "annual leave", "top_k": 10},
    )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert llm.calls == []


def test_unprocessed_document_is_excluded(client, db, monkeypatch):
    _, llm, _, _, admin_token, _ = _setup(
        client, db, monkeypatch, create_document=False
    )
    upload = upload_pdf(
        client,
        admin_token,
        make_pdf_bytes(["This document was uploaded but not processed and contains unprocessed policy text."]),
        filename="unprocessed.pdf",
        document_name="Unprocessed.pdf",
        department="HR",
    )
    assert upload.status_code == 201, upload.text

    response = client.post(
        "/rag/ask",
        headers=auth_headers(admin_token),
        json={"question": "unprocessed policy", "top_k": 10},
    )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert response.json()["answer"] == NO_CONTEXT_ANSWER
    assert llm.calls == []


def test_rls_without_transaction_context_returns_no_chunks(client, db, monkeypatch):
    embedder, _, organization, admin, _, _ = _setup(client, db, monkeypatch)
    context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    query = generate_query_embedding("annual leave", embedding_service=embedder)

    with SessionLocal() as session:
        # Deliberately no set_tenant_context().
        candidates = retrieve_candidate_chunks(
            session,
            context,
            query.vector,
            top_k=10,
            model_label=query.model_label,
            role_ids=frozenset(),
        )

    assert candidates == []


def _detached_context(client, tenant_id, user_id, roles) -> AuthenticatedContext:
    with SessionLocal() as session:
        set_tenant_context(session, UUID(str(tenant_id)))
        user = session.scalar(
            select(User).where(
                User.tenant_id == UUID(str(tenant_id)),
                User.user_id == UUID(str(user_id)),
            )
        )
        organization = session.scalar(
            select(Organization).where(
                Organization.tenant_id == UUID(str(tenant_id))
            )
        )
        assert user is not None and organization is not None
        return AuthenticatedContext(
            user=user, organization=organization, roles=tuple(roles)
        )


def test_concurrent_tenant_queries_do_not_leak(client, db, monkeypatch):
    embedder, _ = patch_rag_runtime(
        monkeypatch,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    org_a, admin_a, token_a = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Concurrent A {uuid4()}",
        email=f"concurrent-a-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token_a,
        pdf_bytes=make_pdf_bytes([TENANT_A_TEXT]),
        filename="a.pdf",
        document_name="Tenant A.pdf",
        department="HR",
    )
    org_b, admin_b, token_b = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Concurrent B {uuid4()}",
        email=f"concurrent-b-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token_b,
        pdf_bytes=make_pdf_bytes([TENANT_B_TEXT]),
        filename="b.pdf",
        document_name="Tenant B.pdf",
        department="HR",
    )

    context_a = _detached_context(client, org_a["tenant_id"], admin_a.user_id, ("ADMIN",))
    context_b = _detached_context(client, org_b["tenant_id"], admin_b.user_id, ("ADMIN",))
    llm_a = RecordingLLM(answer="Tenant A answer [S1].")
    llm_b = RecordingLLM(answer="Tenant B answer [S1].")

    def run(context, llm):
        return answer_question(
            context,
            "annual leave",
            top_k=10,
            request_meta=RequestMeta(request_id=uuid4(), ip_address=None),
            embedding_service=embedder,
            llm_service=llm,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        result_a = executor.submit(run, context_a, llm_a)
        result_b = executor.submit(run, context_b, llm_b)
        result_a.result()
        result_b.result()

    assert TENANT_B_TEXT not in llm_a.calls[0].context
    assert TENANT_A_TEXT not in llm_b.calls[0].context


def test_history_is_authenticated_user_scoped(client, db, monkeypatch):
    _, _, organization, _, admin_token, _ = _setup(
        client, db, monkeypatch, create_document=False
    )
    _, hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"history-{uuid4()}@example.com",
        username="historyuser",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )

    admin_history = client.get("/rag/history", headers=auth_headers(admin_token))
    hr_history = client.get(
        "/rag/history",
        headers=auth_headers(hr_token),
        params={"user_id": str(uuid4())},
    )

    assert admin_history.status_code == 200
    assert hr_history.status_code == 200
    assert admin_history.json()["items"] == []
    assert hr_history.json()["items"] == []
    assert admin_history.json()["persistent"] is False
