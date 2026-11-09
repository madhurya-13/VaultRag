"""Current-permission filtering before any chunk text is loaded."""

from uuid import uuid4

from app.services.query_embedding import generate_query_embedding
from app.services.retrieval_authorization import filter_authorized_chunks
from app.services.vector_retrieval import retrieve_candidate_chunks
from app.tests.helpers import (
    admin_bundle_with_attributes,
    make_pdf_bytes,
    revoke_document_permission,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    find_role_permission,
    load_context,
    patch_rag_runtime,
    upload_processed_pdf,
)


def test_filter_uses_current_permissions_and_revocation(client, db, monkeypatch):
    embedder, _ = patch_rag_runtime(
        monkeypatch,
        llm=RecordingLLM(),
        rag_min_similarity=0.0,
    )
    organization, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Retrieval Auth {uuid4()}",
        email=f"retrieval-auth-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(["Annual leave is available to employees and contractors in the organization."]),
        filename="policy.pdf",
        document_name="Policy.pdf",
        department="HR",
    )
    hr_user, _hr_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"retrieval-hr-{uuid4()}@example.com",
        username="hruser",
        role_names=["HR"],
        department="HR",
        clearance="3",
    )

    admin_context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    hr_context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=hr_user["user_id"],
        roles=("HR",),
    )
    query = generate_query_embedding("annual leave", embedding_service=embedder)
    candidates = retrieve_candidate_chunks(
        db,
        admin_context,
        query.vector,
        top_k=10,
        model_label=query.model_label,
        role_ids=frozenset(),
    )
    assert candidates

    allowed = filter_authorized_chunks(
        db,
        hr_context,
        candidates,
        model_label=query.model_label,
    )
    assert allowed.authorized
    assert allowed.denied == ()

    permission = find_role_permission(
        db,
        tenant_id=organization["tenant_id"],
        document_id=document["document_id"],
        role_name="HR",
    )
    revoke_document_permission(
        client, admin_token, document["document_id"], permission.permission_id
    )
    db.expire_all()

    denied = filter_authorized_chunks(
        db,
        hr_context,
        candidates,
        model_label=query.model_label,
    )
    assert denied.authorized == ()
    assert denied.denied
