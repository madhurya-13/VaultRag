"""Shared test helpers. Kept out of conftest so tests can import them normally."""

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.services.bootstrap import provision_initial_admin

PASSWORD = "SecurePassword123!"


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
    return provision_initial_admin(
        db,
        tenant_id=UUID(str(tenant_id)),
        username=username,
        email=email,
        password=password,
    )


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
