"""End-to-end ingestion pipeline tests with a deterministic fake embedder."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.exceptions import ConflictError
from app.core.request_context import RequestMeta
from app.dependencies.authentication import AuthenticatedContext
from app.models import (
    Document,
    DocumentChunk,
    DocumentIngestionJob,
    Organization,
    User,
)
from app.services.chunk_storage import ChunkStorageError
from app.services.ingestion_service import process_document
from app.services.storage import get_private_storage
from app.tests.helpers import (
    FakeEmbeddingService,
    admin_bundle_with_attributes,
    document_chunks,
    document_status,
    list_documents,
    make_pdf_bytes,
    process_document as process_document_api,
    upload_pdf,
    use_tenant,
)


def _long_pdf() -> bytes:
    pages = [
        " ".join(f"token{page}_{index:03d}" for index in range(100))
        for page in range(6)
    ]
    return make_pdf_bytes(pages)


def _patch_fake_embedder(monkeypatch):
    fake = FakeEmbeddingService()
    monkeypatch.setattr(
        "app.services.ingestion_service.get_embedding_service",
        lambda: fake,
    )
    return fake


def _upload_long_pdf(client, db, *, name: str, email: str):
    organization, admin, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=name,
        email=email,
        username="admin",
    )
    response = upload_pdf(client, token, _long_pdf())
    assert response.status_code == 201, response.text
    return organization, admin, token, response.json()


def _load_document(db, tenant_id, document_id) -> Document:
    # API calls commit in their own sessions; expire the test session's
    # identity map so assertions see the committed status rather than a
    # stale UPLOADED object.
    db.expire_all()
    use_tenant(db, tenant_id)
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(str(tenant_id)),
            Document.document_id == UUID(str(document_id)),
        )
    )
    assert document is not None
    return document


def test_process_pdf_end_to_end_and_status_apis(client, db, monkeypatch):
    organization, admin, token, uploaded = _upload_long_pdf(
        client,
        db,
        name="Pipeline Tenant",
        email="pipeline-admin@example.com",
    )
    fake = _patch_fake_embedder(monkeypatch)

    response = process_document_api(client, token, uploaded["document_id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processing_status"] == "COMPLETED"
    assert body["job_status"] == "COMPLETED"
    assert body["total_pages"] == 6
    assert body["total_chunks"] >= 2
    assert body["embedding_model"] == "fake-test-model@test"
    assert body["error_code"] is None
    assert fake.calls

    document = _load_document(db, organization["tenant_id"], uploaded["document_id"])
    assert document.processing_status == "COMPLETED"
    assert document.total_pages == 6
    assert document.total_chunks == body["total_chunks"]
    assert document.embedding_model == "fake-test-model@test"

    job = db.scalar(
        select(DocumentIngestionJob).where(
            DocumentIngestionJob.tenant_id == UUID(organization["tenant_id"]),
            DocumentIngestionJob.document_id == document.document_id,
        )
    )
    assert job is not None
    assert job.status == "COMPLETED"
    assert job.pages_processed == 6
    assert job.chunks_created == body["total_chunks"]
    assert job.completed_at is not None

    chunks = db.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
            DocumentChunk.document_id == document.document_id,
        )
        .order_by(DocumentChunk.chunk_index)
    ).all()
    assert len(chunks) == body["total_chunks"]
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.chunk_text.strip() for chunk in chunks)
    assert all(1 <= chunk.page_start <= chunk.page_end <= 6 for chunk in chunks)
    assert all(len(chunk.embedding) == 384 for chunk in chunks)
    assert all(
        chunk.chunk_metadata["document_id"] == str(document.document_id)
        for chunk in chunks
    )
    assert all(
        chunk.chunk_metadata["tenant_id"] == str(document.tenant_id)
        for chunk in chunks
    )
    assert all(
        set(chunk.chunk_metadata["allowed_roles"]) == {"ADMIN", "ENGINEERING"}
        for chunk in chunks
    )

    status_response = document_status(client, token, document.document_id)
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["processing_status"] == "COMPLETED"

    listed = list_documents(client, token)
    listed_document = next(
        item for item in listed if item["document_id"] == str(document.document_id)
    )
    assert listed_document["processing_status"] == "COMPLETED"
    assert listed_document["total_chunks"] == body["total_chunks"]

    chunks_response = document_chunks(client, token, document.document_id)
    assert chunks_response.status_code == 200, chunks_response.text
    chunks_body = chunks_response.json()
    assert len(chunks_body) == body["total_chunks"]
    assert chunks_body[0]["metadata"]["tenant_id"] == str(document.tenant_id)
    assert "embedding" not in chunks_body[0]

    # Completed documents cannot be silently reprocessed.
    second = process_document_api(client, token, document.document_id)
    assert second.status_code == 409


def test_missing_private_file_marks_document_failed(client, db, monkeypatch):
    organization, _, token, uploaded = _upload_long_pdf(
        client,
        db,
        name="Missing File Tenant",
        email="missing-file-admin@example.com",
    )
    _patch_fake_embedder(monkeypatch)
    document = _load_document(db, organization["tenant_id"], uploaded["document_id"])
    assert document.storage_path is not None
    get_private_storage().delete_key(document.storage_path)

    response = process_document_api(client, token, uploaded["document_id"])
    assert response.status_code == 422

    document = _load_document(db, organization["tenant_id"], uploaded["document_id"])
    assert document.processing_status == "FAILED"
    assert document.total_chunks == 0
    assert document.embedding_model is None

    job = db.scalar(
        select(DocumentIngestionJob)
        .where(
            DocumentIngestionJob.tenant_id == UUID(organization["tenant_id"]),
            DocumentIngestionJob.document_id == document.document_id,
        )
        .order_by(DocumentIngestionJob.created_at.desc())
    )
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "DOCUMENT_HAS_NO_FILE"
    chunks = db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
            DocumentChunk.document_id == document.document_id,
        )
    )
    assert chunks == 0


def test_chunk_storage_failure_rolls_back_and_marks_failed(client, db, monkeypatch):
    organization, _, token, uploaded = _upload_long_pdf(
        client,
        db,
        name="Rollback Tenant",
        email="rollback-admin@example.com",
    )
    _patch_fake_embedder(monkeypatch)

    def fail_store(*args, **kwargs):
        raise ChunkStorageError("forced storage failure")

    monkeypatch.setattr(
        "app.services.ingestion_service.store_chunks",
        fail_store,
    )

    response = process_document_api(client, token, uploaded["document_id"])
    assert response.status_code == 503

    document = _load_document(db, organization["tenant_id"], uploaded["document_id"])
    assert document.processing_status == "FAILED"
    assert document.total_pages == 0
    assert document.total_chunks == 0
    assert document.embedding_model is None

    chunks = db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
            DocumentChunk.document_id == document.document_id,
        )
    )
    assert chunks == 0

    job = db.scalar(
        select(DocumentIngestionJob)
        .where(
            DocumentIngestionJob.tenant_id == UUID(organization["tenant_id"]),
            DocumentIngestionJob.document_id == document.document_id,
        )
        .order_by(DocumentIngestionJob.created_at.desc())
    )
    assert job is not None
    assert job.status == "FAILED"
    assert job.error_code == "CHUNK_STORAGE_FAILED"


def test_concurrent_processing_is_rejected_by_active_job_guard(client, db):
    organization, admin, token, uploaded = _upload_long_pdf(
        client,
        db,
        name="Concurrency Tenant",
        email="concurrency-admin@example.com",
    )
    use_tenant(db, organization["tenant_id"])
    admin_user = db.scalar(select(User).where(User.user_id == admin.user_id))
    organization_row = db.scalar(
        select(Organization).where(
            Organization.tenant_id == UUID(organization["tenant_id"])
        )
    )
    assert admin_user is not None and organization_row is not None
    context = AuthenticatedContext(
        user=admin_user,
        organization=organization_row,
        roles=("ADMIN",),
    )
    request_meta = RequestMeta(request_id=uuid4(), ip_address="127.0.0.1")

    started = threading.Event()
    release = threading.Event()

    class BlockingEmbeddingService(FakeEmbeddingService):
        def generate_embeddings(self, texts):
            started.set()
            assert release.wait(10)
            return super().generate_embeddings(texts)

    blocking = BlockingEmbeddingService()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            process_document,
            context,
            UUID(uploaded["document_id"]),
            request_meta=request_meta,
            embedding_service=blocking,
            session_factory=SessionLocal,
        )
        assert started.wait(10)
        with pytest.raises(ConflictError):
            process_document(
                context,
                UUID(uploaded["document_id"]),
                request_meta=request_meta,
                embedding_service=FakeEmbeddingService(),
                session_factory=SessionLocal,
            )
        release.set()
        result = first.result(timeout=20)

    assert result.processing_status == "COMPLETED"
    document = _load_document(db, organization["tenant_id"], uploaded["document_id"])
    assert document.processing_status == "COMPLETED"


def test_process_metadata_only_document_is_rejected_without_chunks(client, db, monkeypatch):
    from app.tests.helpers import create_document

    organization, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="No File Tenant",
        email="no-file-admin@example.com",
        username="admin",
    )
    body = create_document(client, token)
    _patch_fake_embedder(monkeypatch)

    response = process_document_api(client, token, body["document_id"])
    assert response.status_code == 422

    use_tenant(db, organization["tenant_id"])
    chunks = db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
            DocumentChunk.document_id == UUID(body["document_id"]),
        )
    )
    assert chunks == 0
