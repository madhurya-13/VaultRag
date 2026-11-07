"""Cross-tenant isolation, role escalation and database-constraint tests."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.models import Document, DocumentPermission, Role, User, UserRole
from app.tests.helpers import (
    PASSWORD,
    access_check,
    admin_bundle_with_attributes,
    admin_create_user,
    auth_headers,
    create_admin_bundle,
    create_document,
    get_audit_logs,
    grant_document_permission,
    login,
    public_register,
    read_document,
    use_tenant,
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
    use_tenant(db, data["a_org"]["tenant_id"])
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

    use_tenant(db, data["b_org"]["tenant_id"])
    foreign_role = db.scalar(
        select(Role).where(
            Role.tenant_id == UUID(data["b_org"]["tenant_id"]),
            Role.role_name == "HR",
        )
    )
    assert foreign_role is not None
    foreign_role_id = foreign_role.role_id

    # Switch the same transaction to tenant A. Tenant B's role is no longer
    # visible, but the row ID is enough to attempt the bad association.
    use_tenant(db, data["a_org"]["tenant_id"])
    bad_link = UserRole(
        user_id=data["a_admin"].user_id,
        role_id=foreign_role_id,  # role belongs to tenant B
        tenant_id=data["a_admin"].tenant_id,  # single shared tenant value
    )
    db.add(bad_link)

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Both halves exist individually; only the tenant-consistency rule failed.
    use_tenant(db, data["a_org"]["tenant_id"])
    assert db.get(User, data["a_admin"].user_id) is not None
    use_tenant(db, data["b_org"]["tenant_id"])
    assert db.get(Role, foreign_role_id) is not None


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


def test_cross_tenant_document_access_and_audit_are_isolated(client, db):
    """TEST 18 (part 1) - documents and their audit trails never cross tenants."""
    org_a, admin_a, admin_a_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    org_b, admin_b, admin_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )
    document_a = create_document(client, admin_a_token, document_name="Alpha secret")

    # Tenant B cannot read or manipulate tenant A's document.
    assert read_document(client, admin_b_token, document_a["document_id"]).status_code == 404
    check = access_check(client, admin_b_token, document_a["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "RESOURCE_NOT_FOUND_POLICY"

    grant = client.post(
        f"/documents/{document_a['document_id']}/permissions",
        headers=auth_headers(admin_b_token),
        json={"permission_type": "READ", "role_name": "HR"},
    )
    assert grant.status_code == 404

    # The denial is recorded in tenant B's audit trail, not tenant A's.
    events_b = get_audit_logs(client, admin_b_token, resource_id=document_a["document_id"])
    assert events_b
    assert all(event["tenant_id"] == org_b["tenant_id"] for event in events_b)
    assert any(event["decision"] == "DENY" for event in events_b)

    events_a = get_audit_logs(client, admin_a_token)
    assert all(event["tenant_id"] == org_a["tenant_id"] for event in events_a)
    assert not [
        event for event in events_a if event["resource_id"] == document_a["document_id"] and event["decision"] == "DENY"
    ]


def test_cross_tenant_admin_actions_are_denied_and_audited(client, db):
    """TEST 18 (part 2) - an admin's authority stops at the tenant boundary."""
    org_a, admin_a, admin_a_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    org_b, admin_b, admin_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )
    foreign_user = admin_create_user(
        client,
        admin_b_token,
        email="hr@beta.com",
        username="beta_hr",
        role_names=["HR"],
    )
    headers = auth_headers(admin_a_token)

    assert (
        client.patch(
            f"/users/{foreign_user['user_id']}/status",
            headers=headers,
            json={"is_active": False},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/users/{foreign_user['user_id']}/roles",
            headers=headers,
            json={"role_name": "ADMIN"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/users/{foreign_user['user_id']}/roles/HR",
            headers=headers,
        ).status_code
        == 404
    )

    # The foreign user is untouched.
    verify = client.get(
        f"/users/{foreign_user['user_id']}", headers=auth_headers(admin_b_token)
    )
    assert verify.status_code == 200
    assert verify.json()["is_active"] is True
    assert verify.json()["roles"] == ["HR"]

    # The attempts are visible only in tenant A's audit trail.
    events_a = get_audit_logs(
        client, admin_a_token, resource_id=foreign_user["user_id"]
    )
    denied_actions = {
        event["action"]
        for event in events_a
        if event["decision"] == "DENY"
    }
    assert {"ROLE_ASSIGN", "ROLE_REVOKE", "USER_STATUS_UPDATE"} <= denied_actions
    assert all(event["tenant_id"] == org_a["tenant_id"] for event in events_a)

    events_b = get_audit_logs(
        client, admin_b_token, resource_id=foreign_user["user_id"]
    )
    assert all(event["tenant_id"] == org_b["tenant_id"] for event in events_b)
    assert all(event["decision"] == "ALLOW" for event in events_b if event["action"] == "USER_CREATE")


def test_cross_tenant_document_uploader_fk_is_rejected_by_the_database(client, db):
    """The composite FK prevents a document from claiming a foreign uploader."""
    org_a, admin_a, admin_a_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    org_b, admin_b, admin_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )

    use_tenant(db, org_a["tenant_id"])
    db.add(
        Document(
            tenant_id=UUID(org_a["tenant_id"]),
            document_name="Bad uploader",
            document_type="policy",
            department="ENGINEERING",
            classification="INTERNAL",
            required_clearance_level=1,
            uploaded_by=admin_b.user_id,  # belongs to tenant B
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    use_tenant(db, org_a["tenant_id"])
    assert (
        db.scalar(
            select(Document).where(Document.document_name == "Bad uploader")
        )
        is None
    )


def test_cross_tenant_document_permission_fk_is_rejected_by_the_database(client, db):
    """A permission row cannot reference a document or user from another tenant."""
    org_a, admin_a, admin_a_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    org_b, admin_b, admin_b_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )
    document_a = create_document(client, admin_a_token)
    foreign_user_id = admin_b.user_id
    local_admin_id = admin_a.user_id

    use_tenant(db, org_a["tenant_id"])
    db.add(
        DocumentPermission(
            tenant_id=UUID(org_a["tenant_id"]),
            document_id=UUID(document_a["document_id"]),
            user_id=foreign_user_id,  # foreign user
            permission_type="READ",
            granted_by=local_admin_id,
            is_active=True,
            cross_department_allowed=False,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    use_tenant(db, org_a["tenant_id"])
    assert (
        db.scalar(
            select(DocumentPermission).where(
                DocumentPermission.document_id == UUID(document_a["document_id"]),
                DocumentPermission.user_id == foreign_user_id,
            )
        )
        is None
    )
