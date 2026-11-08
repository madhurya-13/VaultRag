"""Shared test helpers. Kept out of conftest so tests can import them normally."""

import re
import tempfile
from pathlib import Path
from uuid import UUID

import fitz
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import set_tenant_context
from app.models import User, UserAttribute
from app.services.bootstrap import provision_initial_admin

PASSWORD = "SecurePassword123!"
DEFAULT_PDF_TEXT = (
    "VaultRAG stores documents privately and derives every security field "
    "from trusted server-side records. This paragraph is deliberately long "
    "enough to produce extractable text and multiple embedding chunks. "
) * 4


def use_tenant(db: Session, tenant_id: str | UUID) -> None:
    """Set the transaction-local RLS tenant on a direct test session."""
    set_tenant_context(db, UUID(str(tenant_id)))


def seed_user_attributes(
    db: Session,
    tenant_id: str | UUID,
    user_id: str | UUID,
    attributes: dict[str, str],
) -> None:
    """Test setup: write ABAC attributes directly, then commit.

    RLS still applies, so the tenant context is established first. The API path
    for attribute updates is exercised separately.
    """
    from sqlalchemy import select

    use_tenant(db, tenant_id)
    for name, value in attributes.items():
        row = db.scalar(
            select(UserAttribute).where(
                UserAttribute.tenant_id == UUID(str(tenant_id)),
                UserAttribute.user_id == UUID(str(user_id)),
                UserAttribute.attribute_name == name,
            )
        )
        if row is None:
            db.add(
                UserAttribute(
                    tenant_id=UUID(str(tenant_id)),
                    user_id=UUID(str(user_id)),
                    attribute_name=name,
                    attribute_value=value,
                )
            )
        else:
            row.attribute_value = value
    db.commit()


def register_organization(client: TestClient, name: str) -> dict:
    response = client.post(
        "/organizations/register", json={"organization_name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()


def provision_admin(
    db: Session,
    tenant_id: str | UUID,
    *,
    email: str,
    username: str = "admin",
    password: str = PASSWORD,
) -> User:
    """Exercise the same code path as the operator CLI."""
    user = provision_initial_admin(
        db,
        tenant_id=UUID(str(tenant_id)),
        username=username,
        email=email,
        password=password,
    )
    # The CLI commits after provisioning. Tests that share a direct session with
    # the API must do the same so the separate request session can see the user.
    db.commit()
    return user


def login(
    client: TestClient,
    tenant_id: str | UUID,
    email: str,
    *,
    password: str = PASSWORD,
) -> str:
    response = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tenant_id),
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_admin_bundle(
    client: TestClient,
    db: Session,
    *,
    organization_name: str,
    email: str,
    username: str = "admin",
) -> tuple[dict, User, str]:
    """Create tenant + active ADMIN + token. Returns (org, admin, token)."""
    organization = register_organization(client, organization_name)
    admin = provision_admin(db, organization["tenant_id"], email=email, username=username)
    token = login(client, organization["tenant_id"], email)
    return organization, admin, token


def public_register(
    client: TestClient,
    tenant_id: str | UUID,
    *,
    email: str,
    username: str,
    password: str = PASSWORD,
) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "tenant_id": str(tenant_id),
            "username": username,
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def admin_create_user(
    client: TestClient,
    admin_token: str,
    *,
    email: str,
    username: str,
    role_names: list[str] | None = None,
    password: str = PASSWORD,
) -> dict:
    response = client.post(
        "/users",
        headers=auth_headers(admin_token),
        json={
            "username": username,
            "email": email,
            "password": password,
            "role_names": role_names or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_document(
    client: TestClient,
    token: str,
    *,
    document_name: str = "Policy Document",
    document_type: str = "policy",
    department: str = "ENGINEERING",
    classification: str = "INTERNAL",
    required_clearance_level: int | None = None,
) -> dict:
    payload = {
        "document_name": document_name,
        "document_type": document_type,
        "department": department,
        "classification": classification,
    }
    if required_clearance_level is not None:
        payload["required_clearance_level"] = required_clearance_level
    response = client.post(
        "/documents", headers=auth_headers(token), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def set_user_attributes(
    client: TestClient,
    admin_token: str,
    user_id: str | UUID,
    attributes: dict[str, str],
) -> dict:
    response = client.put(
        f"/users/{user_id}/attributes",
        headers=auth_headers(admin_token),
        json={"attributes": attributes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def get_user_attributes(
    client: TestClient, token: str, user_id: str | UUID
) -> dict:
    response = client.get(
        f"/users/{user_id}/attributes", headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def grant_document_permission(
    client: TestClient,
    token: str,
    document_id: str | UUID,
    *,
    permission_type: str = "READ",
    role_name: str | None = None,
    user_id: str | UUID | None = None,
    expires_at: str | None = None,
    cross_department_allowed: bool = False,
) -> dict:
    payload = {
        "permission_type": permission_type,
        "role_name": role_name,
        "user_id": str(user_id) if user_id is not None else None,
        "cross_department_allowed": cross_department_allowed,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    response = client.post(
        f"/documents/{document_id}/permissions",
        headers=auth_headers(token),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def revoke_document_permission(
    client: TestClient,
    token: str,
    document_id: str | UUID,
    permission_id: str | UUID,
) -> None:
    response = client.delete(
        f"/documents/{document_id}/permissions/{permission_id}",
        headers=auth_headers(token),
    )
    assert response.status_code == 204, response.text


def access_check(
    client: TestClient,
    token: str,
    document_id: str | UUID,
    action: str = "READ",
) -> dict:
    response = client.get(
        f"/documents/{document_id}/access-check",
        headers=auth_headers(token),
        params={"action": action},
    )
    assert response.status_code == 200, response.text
    return response.json()


def list_documents(client: TestClient, token: str) -> list[dict]:
    response = client.get("/documents", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def read_document(client: TestClient, token: str, document_id: str | UUID):
    return client.get(f"/documents/{document_id}", headers=auth_headers(token))


def get_audit_logs(
    client: TestClient, token: str, **params
) -> list[dict]:
    response = client.get(
        "/security/audit-logs", headers=auth_headers(token), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def admin_bundle_with_attributes(
    client: TestClient,
    db: Session,
    *,
    organization_name: str,
    email: str,
    username: str,
    department: str = "ENGINEERING",
    clearance: str = "5",
    employment_status: str = "ACTIVE",
):
    """Create a tenant + admin and seed the admin's ABAC attributes directly.

    The API deliberately prevents an administrator from setting their own
    clearance, so test bootstrap uses the database. The attribute API itself is
    covered by separate tests that use one admin to update another user.
    """
    organization, admin, token = create_admin_bundle(
        client,
        db,
        organization_name=organization_name,
        email=email,
        username=username,
    )
    seed_user_attributes(
        db,
        organization["tenant_id"],
        admin.user_id,
        {
            "department": department,
            "clearance_level": clearance,
            "employment_status": employment_status,
        },
    )
    return organization, admin, token


def tenant_user_with_attributes(
    client: TestClient,
    admin_token: str,
    tenant_id: str | UUID,
    *,
    email: str,
    username: str,
    role_names: list[str],
    department: str = "ENGINEERING",
    clearance: str = "3",
    employment_status: str = "ACTIVE",
    password: str = PASSWORD,
) -> tuple[dict, str]:
    user = admin_create_user(
        client,
        admin_token,
        email=email,
        username=username,
        role_names=role_names,
        password=password,
    )
    set_user_attributes(
        client,
        admin_token,
        user["user_id"],
        {
            "department": department,
            "clearance_level": clearance,
            "employment_status": employment_status,
        },
    )
    token = login(client, tenant_id, email, password=password)
    return user, token


# ---------------------------------------------------------------------------
# Milestone 3 test helpers
# ---------------------------------------------------------------------------


def make_pdf_bytes(
    pages: list[str] | None = None,
    *,
    encrypt_password: str | None = None,
    image_only: bool = False,
) -> bytes:
    """Create a real PDF in memory for upload/extraction tests."""
    page_texts = pages or [DEFAULT_PDF_TEXT]
    document = fitz.open()
    try:
        for text in page_texts:
            page = document.new_page()
            if image_only:
                pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 32, 32), 0)
                pixmap.clear_with(255)
                page.insert_image(fitz.Rect(72, 72, 240, 240), pixmap=pixmap)
            else:
                page.insert_textbox(
                    fitz.Rect(36, 36, 560, 800),
                    text,
                    fontsize=9,
                    align=0,
                )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)
        try:
            save_kwargs = {}
            if encrypt_password:
                save_kwargs = {
                    "encryption": fitz.PDF_ENCRYPT_AES_256,
                    "user_pw": encrypt_password,
                    "owner_pw": encrypt_password,
                }
            document.save(str(path), **save_kwargs)
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
    finally:
        document.close()


def make_blank_pdf_bytes() -> bytes:
    """A real PDF with one page and no extractable text."""
    document = fitz.open()
    try:
        page = document.new_page()
        page.draw_rect(fitz.Rect(72, 72, 300, 300), fill=(0.5, 0.5, 0.5))
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            path = Path(handle.name)
        try:
            document.save(str(path))
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
    finally:
        document.close()


def upload_pdf(
    client: TestClient,
    token: str,
    pdf_bytes: bytes | None = None,
    *,
    filename: str = "policy.pdf",
    document_name: str = "Policy Document",
    department: str = "ENGINEERING",
    classification: str = "INTERNAL",
    content_type: str = "application/pdf",
    extra_form: dict[str, str] | None = None,
):
    form = {
        "document_name": document_name,
        "department": department,
        "classification": classification,
    }
    if extra_form:
        form.update(extra_form)
    return client.post(
        "/documents/upload",
        headers=auth_headers(token),
        data=form,
        files={
            "file": (
                filename,
                make_pdf_bytes() if pdf_bytes is None else pdf_bytes,
                content_type,
            )
        },
    )


def process_document(client: TestClient, token: str, document_id: str | UUID):
    return client.post(
        f"/documents/{document_id}/process",
        headers=auth_headers(token),
    )


def document_status(client: TestClient, token: str, document_id: str | UUID):
    return client.get(
        f"/documents/{document_id}/status",
        headers=auth_headers(token),
    )


def document_chunks(client: TestClient, token: str, document_id: str | UUID):
    return client.get(
        f"/documents/{document_id}/chunks",
        headers=auth_headers(token),
    )


class WhitespaceTokenizer:
    """Deterministic word-level tokenizer used by unit tests and fake embeddings."""

    def __init__(self) -> None:
        self._word_to_id: dict[str, int] = {}
        self._id_to_word: dict[int, str] = {}

    def _intern(self, word: str) -> int:
        if word not in self._word_to_id:
            token_id = len(self._word_to_id) + 1
            self._word_to_id[word] = token_id
            self._id_to_word[token_id] = word
        return self._word_to_id[word]

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return [self._intern(word) for word in re.findall(r"\S+", text)]

    def decode(
        self,
        token_ids: list[int],
        skip_special_tokens: bool = True,
    ) -> str:
        return " ".join(
            self._id_to_word[token_id]
            for token_id in token_ids
            if token_id in self._id_to_word
        )


class FakeEmbeddingService:
    """Fast deterministic embedding service for pipeline/API tests."""

    model_name = "fake-test-model"
    model_version = "test"
    model_label = "fake-test-model@test"

    def __init__(self, dimension: int = 384, tokenizer=None) -> None:
        self.dimension = dimension
        self.tokenizer = tokenizer or WhitespaceTokenizer()
        self.calls: list[list[str]] = []

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def generate_embedding(self, text: str) -> list[float]:
        return self.generate_embeddings([text])[0]

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for index, character in enumerate(text):
                vector[index % self.dimension] += (ord(character) % 31) / 31.0
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors
