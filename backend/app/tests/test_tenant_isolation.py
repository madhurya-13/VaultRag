"""Cross-tenant isolation, role escalation and database-constraint tests."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.models import Role, User, UserRole
from app.tests.helpers import (
    PASSWORD,
    admin_create_user,
    auth_headers,
    create_admin_bundle,
    login,
    public_register,
)


def _two_tenants(client, db):
    a_org, a_admin, a_token = create_admin_bundle(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    b_org, b_admin, b_token = create_admin_bundle(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )
    a_user = admin_create_user(
        client,
        a_token,
        email="hr@alpha.com",
        username="alpha_hr",
        role_names=["HR"],
    )
    b_user = admin_create_user(
        client,
        b_token,
        email="hr@beta.com",
        username="beta_hr",
        role_names=["HR"],
    )
    return {
        "a_org": a_org,
        "a_admin": a_admin,
        "a_token": a_token,
        "a_user": a_user,
        "b_org": b_org,
        "b_admin": b_admin,
        "b_token": b_token,
        "b_user": b_user,
    }


def test_user_listing_is_scoped_to_the_authenticated_tenant(client, db):
    """TEST 9 - tenant A sees only tenant A users, even with spoofed parameters.

    Vulnerability checked: cross-tenant data exposure through query parameters
    or custom headers.
    """
    data = _two_tenants(client, db)

    response = client.get(
        "/users",
        headers=auth_headers(data["a_token"]),
        params={"tenant_id": data["b_org"]["tenant_id"]},  # attacker-controlled
    )
    assert response.status_code == 200

    users = response.json()
    ids = {user["user_id"] for user in users}
    assert ids == {str(data["a_admin"].user_id), data["a_user"]["user_id"]}
    assert data["b_user"]["user_id"] not in ids
    assert all(user["tenant_id"] == data["a_org"]["tenant_id"] for user in users)

    # A custom header is not proof of identity either.
    header_spoof = client.get(
        "/users",
        headers={
            **auth_headers(data["a_token"]),
            "X-Tenant-ID": data["b_org"]["tenant_id"],
        },
    )
    assert header_spoof.status_code == 200
    assert {u["user_id"] for u in header_spoof.json()} == ids


def test_cross_tenant_user_lookup_returns_404(client, db):
    """Vulnerability checked: IDOR (insecure direct object reference)."""
    data = _two_tenants(client, db)

    response = client.get(
        f"/users/{data['b_user']['user_id']}",
        headers=auth_headers(data["a_token"]),
    )
    assert response.status_code == 404

    own = client.get(
        f"/users/{data['a_user']['user_id']}",
        headers=auth_headers(data["a_token"]),
    )
    assert own.status_code == 200
    assert own.json()["tenant_id"] == data["a_org"]["tenant_id"]


def test_non_admin_cannot_list_users(client, db):
    """Vulnerability checked: missing authorisation check on an admin endpoint."""
    data = _two_tenants(client, db)
    user_token = login(client, data["a_org"]["tenant_id"], "hr@alpha.com")

    response = client.get("/users", headers=auth_headers(user_token))
    assert response.status_code == 403


def test_non_admin_cannot_grant_themselves_admin(client, db):
    """TEST 11 (part 2) - a normal user cannot promote themselves.

    Vulnerability checked: vertical privilege escalation via the role endpoint.
    """
    data = _two_tenants(client, db)
    user_token = login(client, data["a_org"]["tenant_id"], "hr@alpha.com")
    user_id = data["a_user"]["user_id"]

    response = client.post(
        f"/users/{user_id}/roles",
        headers=auth_headers(user_token),
        json={"role_name": "ADMIN"},
    )
    assert response.status_code == 403

    db.expire_all()
    admin_role_id = db.scalar(
        select(Role.role_id).where(
            Role.tenant_id == UUID(data["a_org"]["tenant_id"]),
            Role.role_name == "ADMIN",
        )
    )
    links = db.scalars(
        select(UserRole).where(UserRole.user_id == UUID(user_id))
    ).all()
    assert admin_role_id not in {link.role_id for link in links}


def test_admin_cannot_modify_users_in_another_tenant(client, db):
    """Vulnerability checked: an admin's authority leaking across tenants."""
    data = _two_tenants(client, db)
    headers = auth_headers(data["a_token"])
    foreign_user = data["b_user"]["user_id"]

    assert (
        client.patch(
            f"/users/{foreign_user}/status", headers=headers, json={"is_active": False}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/users/{foreign_user}/roles", headers=headers, json={"role_name": "HR"}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/users/{foreign_user}/roles/HR", headers=headers).status_code
        == 404
    )


def test_cross_tenant_user_role_row_is_rejected_by_the_database(client, db):
    """TEST 12 - the composite foreign key blocks the bad association.

    Vulnerability checked: a buggy script/console linking a user in tenant A to
    a role in tenant B. Application checks are bypassed here on purpose; the
    database itself must refuse the row.
    """
    data = _two_tenants(client, db)

    foreign_role = db.scalar(
        select(Role).where(
            Role.tenant_id == UUID(data["b_org"]["tenant_id"]),
            Role.role_name == "HR",
        )
    )
    assert foreign_role is not None

    bad_link = UserRole(
        user_id=data["a_admin"].user_id,
        role_id=foreign_role.role_id,  # role belongs to tenant B
        tenant_id=data["a_admin"].tenant_id,  # single shared tenant value
    )
    db.add(bad_link)

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Both halves exist individually; only the tenant-consistency rule failed.
    assert db.get(User, data["a_admin"].user_id) is not None
    assert db.get(Role, foreign_role.role_id) is not None


def test_token_with_mismatched_tenant_claim_is_rejected(client, db):
    """Defence in depth: even a correctly signed token cannot move a user to a
    different tenant. The database row is authoritative."""
    data = _two_tenants(client, db)

    token, _ = create_access_token(
        user_id=data["a_admin"].user_id,
        tenant_id=UUID(data["b_org"]["tenant_id"]),  # forged/mis-issued claim
        roles=["ADMIN"],
    )
    response = client.get("/auth/me", headers=auth_headers(token))
    assert response.status_code == 401


def test_role_revocation_takes_effect_immediately(client, db):
    """Vulnerability checked: stale roles inside a valid, unexpired JWT."""
    data = _two_tenants(client, db)

    second_admin = admin_create_user(
        client,
        data["a_token"],
        email="second.admin@alpha.com",
        username="second_admin",
        role_names=["ADMIN"],
    )
    second_token = login(client, data["a_org"]["tenant_id"], "second.admin@alpha.com")

    # The new admin can use the admin API.
    assert client.get("/users", headers=auth_headers(second_token)).status_code == 200

    revoke = client.delete(
        f"/users/{second_admin['user_id']}/roles/ADMIN",
        headers=auth_headers(data["a_token"]),
    )
    assert revoke.status_code == 204

    # The OLD token still verifies, but its ADMIN claim is no longer trusted.
    me = client.get("/auth/me", headers=auth_headers(second_token))
    assert me.status_code == 200
    assert me.json()["roles"] == []
    assert client.get("/users", headers=auth_headers(second_token)).status_code == 403


def test_admin_cannot_lock_the_tenant_out(client, db):
    """Guardrails against accidental self-inflicted denial of service."""
    data = _two_tenants(client, db)
    headers = auth_headers(data["a_token"])

    deactivate_self = client.patch(
        f"/users/{data['a_admin'].user_id}/status",
        headers=headers,
        json={"is_active": False},
    )
    assert deactivate_self.status_code == 400

    remove_own_last_admin = client.delete(
        f"/users/{data['a_admin'].user_id}/roles/ADMIN", headers=headers
    )
    assert remove_own_last_admin.status_code == 400


def test_public_signup_cannot_access_until_admin_approval_and_role_assignment(client, db):
    """Full secure onboarding flow, including live role reload."""
    data = _two_tenants(client, db)

    pending = public_register(
        client,
        data["a_org"]["tenant_id"],
        email="new.hire@alpha.com",
        username="new_hire",
    )
    assert pending["is_active"] is False

    # Pending accounts cannot log in.
    assert (
        client.post(
            "/auth/login",
            json={
                "tenant_id": data["a_org"]["tenant_id"],
                "email": "new.hire@alpha.com",
                "password": PASSWORD,
            },
        ).status_code
        == 401
    )

    activate = client.patch(
        f"/users/{pending['user_id']}/status",
        headers=auth_headers(data["a_token"]),
        json={"is_active": True},
    )
    assert activate.status_code == 200

    user_token = login(client, data["a_org"]["tenant_id"], "new.hire@alpha.com")
    assert client.get("/auth/me", headers=auth_headers(user_token)).json()["roles"] == []
    assert client.get("/users", headers=auth_headers(user_token)).status_code == 403

    assign = client.post(
        f"/users/{pending['user_id']}/roles",
        headers=auth_headers(data["a_token"]),
        json={"role_name": "HR"},
    )
    assert assign.status_code == 200
    assert assign.json()["roles"] == ["HR"]

    # The token issued BEFORE the assignment now sees the new role, because
    # roles are loaded from the database on every request.
    me = client.get("/auth/me", headers=auth_headers(user_token))
    assert me.status_code == 200
    assert me.json()["roles"] == ["HR"]


def test_tenant_identifier_cannot_be_supplied_when_creating_a_user(client, db):
    """Mass-assignment protection: the tenant comes from the admin's token."""
    data = _two_tenants(client, db)

    response = client.post(
        "/users",
        headers=auth_headers(data["a_token"]),
        json={
            "username": "smuggled",
            "email": "smuggled@alpha.com",
            "password": PASSWORD,
            "tenant_id": data["b_org"]["tenant_id"],
        },
    )
    assert response.status_code == 422


def test_missing_tenant_context_is_denied(client, db):
    """No token/tenant context must never mean 'show everything'."""
    data = _two_tenants(client, db)

    assert client.get("/users").status_code == 401
    assert (
        client.get(
            "/users",
            headers={"X-Tenant-ID": data["b_org"]["tenant_id"]},
        ).status_code
        == 401
    )
