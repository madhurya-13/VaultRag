"""Authentication, password storage and token-security tests."""

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models import Organization, User
from app.tests.helpers import (
    PASSWORD,
    auth_headers,
    create_admin_bundle,
    login,
    public_register,
    register_organization,
    use_tenant,
)


def _login_request(client, tenant_id, email, password=PASSWORD):
    return client.post(
        "/auth/login",
        json={"tenant_id": str(tenant_id), "email": email, "password": password},
    )


def test_public_registration_is_pending_and_tenant_scoped(client, db):
    """TEST 2 - signup associates the user with the requested tenant, but grants
    no access until an administrator approves the account.

    Vulnerability checked: privilege/access granted by public self-registration.
    """
    first = register_organization(client, "TechNova")
    second = register_organization(client, "BioCore")

    user_a = public_register(
        client, first["tenant_id"], email="hr@technova.com", username="hr_user"
    )
    user_b = public_register(
        client, second["tenant_id"], email="hr@biocore.com", username="hr_user"
    )

    assert user_a["tenant_id"] == first["tenant_id"]
    assert user_b["tenant_id"] == second["tenant_id"]
    assert user_a["is_active"] is False
    assert user_a["roles"] == []

    stored = db.scalar(select(User).where(User.user_id == UUID(user_a["user_id"])))
    assert stored is None  # no tenant context: RLS denies by default

    use_tenant(db, first["tenant_id"])
    stored = db.scalar(select(User).where(User.user_id == UUID(user_a["user_id"])))
    assert stored is not None
    assert stored.tenant_id == UUID(first["tenant_id"])

    # A pending account cannot authenticate.
    assert _login_request(client, first["tenant_id"], "hr@technova.com").status_code == 401


def test_same_email_allowed_in_different_organizations(client):
    """TEST 3 - email uniqueness is scoped to the tenant, not global.

    Vulnerability checked: accidental cross-tenant identity collision / an
    information leak that reveals another organization's users.
    """
    first = register_organization(client, "TechNova")
    second = register_organization(client, "BioCore")

    a = public_register(
        client, first["tenant_id"], email="shared@example.com", username="shared_a"
    )
    b = public_register(
        client, second["tenant_id"], email="shared@example.com", username="shared_b"
    )

    assert a["email"] == b["email"] == "shared@example.com"
    assert a["user_id"] != b["user_id"]


def test_same_email_rejected_within_one_organization(client):
    """TEST 4 - duplicate email inside a tenant is rejected."""
    organization = register_organization(client, "TechNova")

    public_register(
        client, organization["tenant_id"], email="hr@technova.com", username="hr_user"
    )

    duplicate_email = client.post(
        "/auth/register",
        json={
            "tenant_id": organization["tenant_id"],
            "username": "hr_user_2",
            "email": "HR@TechNova.com",  # case-insensitive duplicate
            "password": PASSWORD,
        },
    )
    assert duplicate_email.status_code == 409

    duplicate_username = client.post(
        "/auth/register",
        json={
            "tenant_id": organization["tenant_id"],
            "username": "hr_user",
            "email": "other@technova.com",
            "password": PASSWORD,
        },
    )
    assert duplicate_username.status_code == 409


def test_login_returns_signed_jwt_with_expected_claims(client, db):
    """TEST 5 - correct credentials produce a verifiable JWT.

    Vulnerability checked: broken token issuance / missing tenant binding.
    """
    organization, admin, token = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    settings = get_settings()
    claims = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )

    assert claims["sub"] == str(admin.user_id)
    assert claims["tenant_id"] == organization["tenant_id"]
    assert claims["roles"] == ["ADMIN"]
    assert claims["type"] == "access"
    assert claims["iss"] == settings.jwt_issuer
    assert claims["aud"] == settings.jwt_audience
    assert claims["exp"] > claims["iat"]

    me = client.get("/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["user_id"] == str(admin.user_id)
    assert me.json()["tenant_id"] == organization["tenant_id"]
    assert me.json()["organization_name"] == "TechNova"
    assert me.json()["roles"] == ["ADMIN"]


def test_login_rejects_wrong_password_without_account_disclosure(client, db):
    """TEST 6 - incorrect password is rejected, and unknown accounts look the same.

    Vulnerability checked: user enumeration through differing error messages.
    """
    organization, _, _ = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    wrong_password = _login_request(
        client, organization["tenant_id"], "admin@technova.com", "WrongPassword123!"
    )
    unknown_email = _login_request(
        client, organization["tenant_id"], "nobody@technova.com"
    )

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_login_requires_the_correct_organization(client, db):
    """An email from tenant A cannot authenticate against tenant B."""
    first, _, _ = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )
    second = register_organization(client, "BioCore")

    response = _login_request(client, second["tenant_id"], "admin@technova.com")
    assert response.status_code == 401

    # The same credentials succeed in the correct tenant.
    assert _login_request(client, first["tenant_id"], "admin@technova.com").status_code == 200


def test_expired_jwt_is_rejected(client, db):
    """TEST 7 - expired tokens cannot access protected routes."""
    _, admin, _ = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    expired_token, _ = create_access_token(
        user_id=admin.user_id,
        tenant_id=admin.tenant_id,
        roles=["ADMIN"],
        expires_delta=timedelta(minutes=-5),
    )

    response = client.get("/auth/me", headers=auth_headers(expired_token))
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_modified_jwt_is_rejected(client, db):
    """TEST 8 - changing claims or signing with another key invalidates the token.

    Vulnerability checked: signature bypass / tampering with tenant or role
    claims to cross a tenant boundary.
    """
    first, admin, token = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )
    second = register_organization(client, "BioCore")

    header, payload, signature = token.split(".")

    def b64decode(segment: str) -> bytes:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

    claims = json.loads(b64decode(payload))
    claims["tenant_id"] = second["tenant_id"]  # attempt to switch tenant
    forged_payload = (
        base64.urlsafe_b64encode(
            json.dumps(claims, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    forged_token = f"{header}.{forged_payload}.{signature}"

    response = client.get("/auth/me", headers=auth_headers(forged_token))
    assert response.status_code == 401

    # A token signed with an attacker-controlled secret is equally invalid.
    now = datetime.now(timezone.utc)
    wrong_key_token = jwt.encode(
        {
            "sub": str(admin.user_id),
            "tenant_id": first["tenant_id"],
            "roles": ["ADMIN"],
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        "attacker-secret-that-is-at-least-32-characters-long",
        algorithm="HS256",
    )
    response = client.get("/auth/me", headers=auth_headers(wrong_key_token))
    assert response.status_code == 401


def test_inactive_user_cannot_authenticate_or_use_existing_token(client, db):
    """TEST 10 - deactivating a user blocks login AND existing tokens.

    Vulnerability checked: deactivated employees retaining access until token
    expiry.
    """
    organization, admin, token = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    assert client.get("/auth/me", headers=auth_headers(token)).status_code == 200

    use_tenant(db, organization["tenant_id"])
    admin.is_active = False
    db.commit()

    assert _login_request(client, organization["tenant_id"], "admin@technova.com").status_code == 401
    assert client.get("/auth/me", headers=auth_headers(token)).status_code == 401


def test_inactive_organization_blocks_login_and_tokens(client, db, migration_db):
    """Vulnerability checked: suspended tenant users keeping access."""
    organization, _, token = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    stored = db.get(Organization, UUID(organization["tenant_id"]))
    assert stored is None  # no context

    use_tenant(migration_db, organization["tenant_id"])
    stored = migration_db.get(Organization, UUID(organization["tenant_id"]))
    assert stored is not None
    stored.is_active = False
    migration_db.commit()

    assert _login_request(client, organization["tenant_id"], "admin@technova.com").status_code == 401
    assert client.get("/auth/me", headers=auth_headers(token)).status_code == 401

    # Public signup into a suspended tenant is refused as well.
    registration = client.post(
        "/auth/register",
        json={
            "tenant_id": organization["tenant_id"],
            "username": "new_user",
            "email": "new@technova.com",
            "password": PASSWORD,
        },
    )
    assert registration.status_code == 403


def test_passwords_are_stored_as_argon2id_hashes(client, db):
    """Vulnerability checked: plaintext or reversible password storage."""
    _, admin, _ = create_admin_bundle(
        client, db, organization_name="TechNova", email="admin@technova.com"
    )

    use_tenant(db, admin.tenant_id)
    db.refresh(admin)
    assert admin.password_hash != PASSWORD
    assert PASSWORD not in admin.password_hash
    assert admin.password_hash.startswith("$argon2id$")


def test_public_registration_cannot_assign_roles_or_activation(client):
    """TEST 11 (part 1) - mass assignment of role/activation fields is refused.

    Vulnerability checked: a user granting themselves ADMIN at signup.
    """
    organization = register_organization(client, "TechNova")
    base = {
        "tenant_id": organization["tenant_id"],
        "username": "attacker",
        "email": "attacker@technova.com",
        "password": PASSWORD,
    }

    for extra in (
        {"role_names": ["ADMIN"]},
        {"role_id": "00000000-0000-0000-0000-000000000000"},
        {"roles": ["ADMIN"]},
        {"is_active": True},
    ):
        response = client.post("/auth/register", json={**base, **extra})
        assert response.status_code == 422, extra


def test_weak_passwords_are_rejected(client):
    organization = register_organization(client, "TechNova")
    for password in ("short1", "letterswithoutdigits", "1234567890123"):
        response = client.post(
            "/auth/register",
            json={
                "tenant_id": organization["tenant_id"],
                "username": "weak_user",
                "email": "weak@technova.com",
                "password": password,
            },
        )
        assert response.status_code == 422, password


def test_missing_or_malformed_bearer_token_is_rejected(client):
    """Missing tenant context must always deny access."""
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code
        == 401
    )
    assert (
        client.get("/auth/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}).status_code
        == 401
    )
