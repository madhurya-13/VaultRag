"""Shared test helpers. Kept out of conftest so tests can import them normally."""

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import set_tenant_context
from app.models import User, UserAttribute
from app.services.bootstrap import provision_initial_admin

PASSWORD = "SecurePassword123!"


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
