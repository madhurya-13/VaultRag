"""Shared fixtures for Milestone 4 RAG tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import set_tenant_context
from app.dependencies.authentication import AuthenticatedContext
from app.models import DocumentPermission, Organization, Role, User
from app.services.llm_service import LLMResponse
from app.tests.helpers import (
    FakeEmbeddingService,
    process_document as process_document_api,
    upload_pdf,
)


@dataclass(frozen=True, slots=True)
class LLMCall:
    question: str
    context: str


class RecordingLLM:
    """Mock LLM that records the exact authorized context it receives."""

    def __init__(
        self,
        answer: str = "The accessible policy is described by the supplied source [S1].",
        *,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[LLMCall] = []

    def generate_answer(self, question: str, authorized_context: str) -> LLMResponse:
        self.calls.append(LLMCall(question=question, context=authorized_context))
        if self.error is not None:
            raise self.error
        return LLMResponse(answer=self.answer, model="mock-model")


def patch_rag_runtime(
    monkeypatch,
    embedder: FakeEmbeddingService | None = None,
    llm: RecordingLLM | None = None,
    **settings_overrides,
):
    """Patch ingestion/retrieval embedding and LLM access for API tests."""
    fake_embedder = embedder or FakeEmbeddingService()
    fake_llm = llm or RecordingLLM()
    monkeypatch.setattr(
        "app.services.ingestion_service.get_embedding_service",
        lambda: fake_embedder,
    )
    monkeypatch.setattr(
        "app.services.rag_service.get_embedding_service",
        lambda: fake_embedder,
    )
    monkeypatch.setattr(
        "app.services.rag_service.get_llm_service",
        lambda: fake_llm,
    )

    if settings_overrides:
        settings = get_settings().model_copy(update=settings_overrides)
        monkeypatch.setattr("app.services.rag_service.get_settings", lambda: settings)

    return fake_embedder, fake_llm


def upload_processed_pdf(
    client,
    token: str,
    *,
    pdf_bytes: bytes,
    filename: str,
    document_name: str,
    department: str = "HR",
    classification: str = "INTERNAL",
    required_clearance_level: int | None = None,
):
    extra_form = {}
    if required_clearance_level is not None:
        extra_form["required_clearance_level"] = str(required_clearance_level)
    upload = upload_pdf(
        client,
        token,
        pdf_bytes,
        filename=filename,
        document_name=document_name,
        department=department,
        classification=classification,
        extra_form=extra_form or None,
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()
    processed = process_document_api(client, token, document["document_id"])
    assert processed.status_code == 200, processed.text
    return document


def load_context(
    db: Session,
    *,
    tenant_id: str | UUID,
    user_id: str | UUID,
    roles: tuple[str, ...] | list[str],
) -> AuthenticatedContext:
    tenant_uuid = UUID(str(tenant_id))
    user_uuid = UUID(str(user_id))
    set_tenant_context(db, tenant_uuid)
    db.expire_all()
    user = db.scalar(
        select(User).where(
            User.tenant_id == tenant_uuid,
            User.user_id == user_uuid,
        )
    )
    organization = db.scalar(
        select(Organization).where(Organization.tenant_id == tenant_uuid)
    )
    assert user is not None
    assert organization is not None
    return AuthenticatedContext(
        user=user,
        organization=organization,
        roles=tuple(roles),
    )


def expire_permission(
    db: Session,
    *,
    tenant_id: str | UUID,
    permission_id: str | UUID,
) -> None:
    tenant_uuid = UUID(str(tenant_id))
    permission_uuid = UUID(str(permission_id))
    set_tenant_context(db, tenant_uuid)
    permission = db.scalar(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == tenant_uuid,
            DocumentPermission.permission_id == permission_uuid,
        )
    )
    assert permission is not None
    now = datetime.now(timezone.utc)
    permission.granted_at = now - timedelta(days=2)
    permission.expires_at = now - timedelta(days=1)
    db.commit()


def find_role_permission(
    db: Session,
    *,
    tenant_id: str | UUID,
    document_id: str | UUID,
    role_name: str,
    permission_type: str = "READ",
) -> DocumentPermission:
    tenant_uuid = UUID(str(tenant_id))
    document_uuid = UUID(str(document_id))
    set_tenant_context(db, tenant_uuid)
    permission = db.scalar(
        select(DocumentPermission)
        .join(
            Role,
            (Role.tenant_id == DocumentPermission.tenant_id)
            & (Role.role_id == DocumentPermission.role_id),
        )
        .where(
            DocumentPermission.tenant_id == tenant_uuid,
            DocumentPermission.document_id == document_uuid,
            DocumentPermission.permission_type == permission_type,
            Role.role_name == role_name,
            DocumentPermission.is_active.is_(True),
        )
    )
    assert permission is not None
    return permission
