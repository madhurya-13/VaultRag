"""Chunk validation, transactional storage and cross-tenant constraint tests."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.database import SessionLocal, set_tenant_context
from app.models import Document, DocumentChunk
from app.services.chunk_metadata import (
    PermissionSnapshot,
    build_chunk_metadata,
)
from app.services.chunk_storage import (
    ChunkStorageError,
    ChunkValidationError,
    store_chunks,
    validate_chunk_batch,
)
from app.services.chunking import ChunkDraft
from app.tests.helpers import create_admin_bundle, create_document, use_tenant

EMBEDDING = [0.01] * 384


def _draft(index: int, *, text: str = "A valid chunk", page_start: int = 1, page_end: int = 1):
    return ChunkDraft(
        chunk_index=index,
        chunk_text=text,
        page_start=page_start,
        page_end=page_end,
        token_count=len(text.split()),
    )


def _snapshot() -> PermissionSnapshot:
    return PermissionSnapshot(
        allowed_roles=(),
        allowed_role_ids=(),
        allowed_user_ids=(),
        snapshot_at=datetime.now(timezone.utc),
    )


def _setup_document(client, db, *, name: str, email: str):
    organization, admin, token = create_admin_bundle(
        client,
        db,
        organization_name=name,
        email=email,
    )
    document_body = create_document(client, token)
    use_tenant(db, organization["tenant_id"])
    document = db.scalar(
        select(Document).where(
            Document.tenant_id == UUID(organization["tenant_id"]),
            Document.document_id == UUID(document_body["document_id"]),
        )
    )
    assert document is not None
    document.total_pages = 2
    db.commit()
    use_tenant(db, organization["tenant_id"])
    return organization, admin, token, document


def _metadata(document: Document):
    return build_chunk_metadata(
        document,
        _snapshot(),
        embedding_model="fake-model",
        embedding_model_version="v1",
    )


def test_validate_chunk_batch_accepts_consistent_chunks(client, db):
    _, _, _, document = _setup_document(
        client, db, name="Chunk Validation Tenant", email="chunk-validation@example.com"
    )
    chunks = [_draft(0), _draft(1, page_end=2)]
    embeddings = [EMBEDDING, EMBEDDING]

    validate_chunk_batch(
        document=document,
        chunks=chunks,
        embeddings=embeddings,
        metadata=_metadata(document),
        snapshot=_snapshot(),
        embedding_dimension=384,
        total_pages=2,
    )


def test_validate_chunk_batch_rejects_wrong_dimension_and_non_finite():
    # This is a pure validation test; the document shape only needs the fields
    # used by validate_metadata_against_document.
    document = Document(
        tenant_id=uuid4(),
        document_name="Validation",
        document_type="PDF",
        department="ENGINEERING",
        classification="INTERNAL",
        required_clearance_level=1,
        uploaded_by=uuid4(),
        is_active=True,
    )
    snapshot = _snapshot()
    metadata = build_chunk_metadata(
        document,
        snapshot,
        embedding_model="fake-model",
        embedding_model_version="v1",
    )
    chunks = [_draft(0)]

    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=chunks,
            embeddings=[[0.0] * 383],
            metadata=metadata,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )

    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=chunks,
            embeddings=[[float("nan")] * 384],
            metadata=metadata,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )


def test_validate_chunk_batch_rejects_page_range_duplicate_index_and_extra_metadata(
    client, db
):
    _, _, _, document = _setup_document(
        client, db, name="Chunk Invalid Tenant", email="chunk-invalid@example.com"
    )
    snapshot = _snapshot()
    metadata = _metadata(document)

    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=[_draft(0, page_end=3)],
            embeddings=[EMBEDDING],
            metadata=metadata,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )

    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=[_draft(0), _draft(0)],
            embeddings=[EMBEDDING, EMBEDDING],
            metadata=metadata,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )

    extra = dict(metadata)
    extra["is_admin"] = True
    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=[_draft(0)],
            embeddings=[EMBEDDING],
            metadata=extra,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )

    wrong_tenant = dict(metadata)
    wrong_tenant["tenant_id"] = str(uuid4())
    with pytest.raises(ChunkValidationError):
        validate_chunk_batch(
            document=document,
            chunks=[_draft(0)],
            embeddings=[EMBEDDING],
            metadata=wrong_tenant,
            snapshot=snapshot,
            embedding_dimension=384,
            total_pages=2,
        )


def test_store_chunks_inserts_tenant_owned_rows(client, db):
    organization, _, _, document = _setup_document(
        client, db, name="Chunk Store Tenant", email="chunk-store@example.com"
    )
    chunks = [_draft(0), _draft(1, page_end=2)]
    metadata = _metadata(document)

    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, UUID(organization["tenant_id"]))
            loaded = session.scalar(
                select(Document).where(
                    Document.tenant_id == UUID(organization["tenant_id"]),
                    Document.document_id == document.document_id,
                )
            )
            assert loaded is not None
            count = store_chunks(
                session,
                document=loaded,
                chunks=chunks,
                embeddings=[EMBEDDING, EMBEDDING],
                metadata=metadata,
            )
            assert count == 2

    use_tenant(db, organization["tenant_id"])
    rows = db.scalars(
        select(DocumentChunk)
        .where(
            DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
            DocumentChunk.document_id == document.document_id,
        )
        .order_by(DocumentChunk.chunk_index)
    ).all()
    assert len(rows) == 2
    assert [row.chunk_index for row in rows] == [0, 1]
    assert all(row.tenant_id == UUID(organization["tenant_id"]) for row in rows)
    assert all(row.document_id == document.document_id for row in rows)
    assert all(len(row.embedding) == 384 for row in rows)
    assert all(row.chunk_metadata["tenant_id"] == str(document.tenant_id) for row in rows)


def test_store_chunks_rolls_back_entire_batch_on_constraint_failure(client, db):
    organization, _, _, document = _setup_document(
        client, db, name="Chunk Rollback Tenant", email="chunk-rollback@example.com"
    )
    metadata = _metadata(document)
    duplicate_chunks = [_draft(0), _draft(0)]

    with SessionLocal() as session:
        with session.begin():
            set_tenant_context(session, UUID(organization["tenant_id"]))
            loaded = session.scalar(
                select(Document).where(
                    Document.tenant_id == UUID(organization["tenant_id"]),
                    Document.document_id == document.document_id,
                )
            )
            assert loaded is not None
            with pytest.raises(ChunkStorageError):
                store_chunks(
                    session,
                    document=loaded,
                    chunks=duplicate_chunks,
                    embeddings=[EMBEDDING, EMBEDDING],
                    metadata=metadata,
                )
            remaining = session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(
                    DocumentChunk.tenant_id == UUID(organization["tenant_id"]),
                    DocumentChunk.document_id == document.document_id,
                )
            )
            assert remaining == 0


def test_cross_tenant_document_id_is_rejected_by_composite_foreign_key(client, db):
    first_org, _, _, _ = _setup_document(
        client, db, name="Chunk FK Tenant A", email="chunk-fk-a@example.com"
    )
    _, _, _, second_document = _setup_document(
        client, db, name="Chunk FK Tenant B", email="chunk-fk-b@example.com"
    )

    with SessionLocal() as session:
        set_tenant_context(session, UUID(first_org["tenant_id"]))
        session.add(
            DocumentChunk(
                tenant_id=UUID(first_org["tenant_id"]),
                document_id=second_document.document_id,
                chunk_index=0,
                chunk_text="cross tenant",
                embedding=EMBEDDING,
                page_start=1,
                page_end=1,
                chunk_metadata={},
            )
        )
        with pytest.raises((IntegrityError, ProgrammingError)):
            session.flush()
        session.rollback()


def test_rls_rejects_chunk_row_for_another_tenant(client, db):
    first_org, _, _, _ = _setup_document(
        client, db, name="Chunk RLS Tenant A", email="chunk-rls-a@example.com"
    )
    second_org, _, _, second_document = _setup_document(
        client, db, name="Chunk RLS Tenant B", email="chunk-rls-b@example.com"
    )

    with SessionLocal() as session:
        set_tenant_context(session, UUID(first_org["tenant_id"]))
        session.add(
            DocumentChunk(
                tenant_id=UUID(second_org["tenant_id"]),
                document_id=second_document.document_id,
                chunk_index=0,
                chunk_text="cross tenant rls",
                embedding=EMBEDDING,
                page_start=1,
                page_end=1,
                chunk_metadata={},
            )
        )
        with pytest.raises((IntegrityError, ProgrammingError)):
            session.flush()
        session.rollback()
