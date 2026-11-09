"""Tenant-filtered pgvector candidate retrieval."""

from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError
from app.services.query_embedding import generate_query_embedding
from app.services.vector_retrieval import (
    fetch_chunk_texts,
    retrieve_candidate_chunks,
    validate_top_k,
)
from app.tests.helpers import (
    admin_bundle_with_attributes,
    make_pdf_bytes,
)
from app.tests.rag_helpers import (
    RecordingLLM,
    load_context,
    patch_rag_runtime,
    upload_processed_pdf,
)


def _candidate_setup(client, db, monkeypatch):
    embedder, _ = patch_rag_runtime(
        monkeypatch,
        llm=RecordingLLM(),
        rag_min_similarity=0.0,
    )
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Vector Tenant {uuid4()}",
        email=f"vector-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy for employees and contractors is documented here."]),
        filename="policy.pdf",
        document_name="Policy.pdf",
        department="HR",
    )
    context = load_context(
        db,
        tenant_id=organization["tenant_id"],
        user_id=admin.user_id,
        roles=("ADMIN",),
    )
    return embedder, context, document, token


def test_retrieval_returns_metadata_without_chunk_text(client, db, monkeypatch):
    embedder, context, document, _ = _candidate_setup(client, db, monkeypatch)
    query = generate_query_embedding("annual leave", embedding_service=embedder)

    candidates = retrieve_candidate_chunks(
        db,
        context,
        query.vector,
        top_k=10,
        model_label=query.model_label,
        role_ids=frozenset(),
    )

    assert candidates
    assert str(candidates[0].document_id) == document["document_id"]
    assert not hasattr(candidates[0], "text")
    assert not hasattr(candidates[0], "chunk_text")

    texts = fetch_chunk_texts(db, context, [candidate.chunk_id for candidate in candidates])
    assert texts
    assert all("annual leave" in item.text.lower() for item in texts.values())


def test_embedding_model_mismatch_excludes_candidates(client, db, monkeypatch):
    embedder, context, _, _ = _candidate_setup(client, db, monkeypatch)
    query = generate_query_embedding("annual leave", embedding_service=embedder)

    candidates = retrieve_candidate_chunks(
        db,
        context,
        query.vector,
        top_k=10,
        model_label="different-model@v1",
        role_ids=frozenset(),
    )

    assert candidates == []


def test_top_k_bounds_are_validated():
    assert validate_top_k(1) == 1
    assert validate_top_k(50) == 50
    with pytest.raises(BadRequestError):
        validate_top_k(51)
    with pytest.raises(BadRequestError):
        validate_top_k(0)
