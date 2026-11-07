"""RBAC capability tests and immediate role-revocation behaviour."""

from app.core.security_constants import (
    CAP_CREATE_DOCUMENT,
    CAP_MANAGE_ATTRIBUTES,
    CAP_MANAGE_ROLES,
    CAP_MANAGE_USERS,
    CAP_VIEW_AUDIT_LOGS,
    ROLE_CAPABILITIES,
)
from app.services.rbac import has_capability
from app.tests.helpers import (
    access_check,
    admin_bundle_with_attributes,
    auth_headers,
    create_document,
    grant_document_permission,
    tenant_user_with_attributes,
)


def test_admin_capability_catalogue_is_explicit_and_least_privilege():
    admin = ROLE_CAPABILITIES["ADMIN"]
    assert {
        CAP_MANAGE_USERS,
        CAP_MANAGE_ROLES,
        CAP_CREATE_DOCUMENT,
        CAP_VIEW_AUDIT_LOGS,
        CAP_MANAGE_ATTRIBUTES,
    } <= admin

    for role_name in ("HR", "FINANCE", "ENGINEERING", "EMPLOYEE"):
        assert ROLE_CAPABILITIES[role_name] == frozenset()
        for capability in admin:
            assert has_capability((role_name,), capability) is False

    assert has_capability(("ADMIN",), CAP_MANAGE_USERS) is True
    assert has_capability(("HR", "FINANCE"), CAP_MANAGE_USERS) is False
    # Unknown roles never grant anything.
    assert has_capability(("SUPERUSER",), CAP_MANAGE_USERS) is False


def test_non_admin_cannot_use_administrator_endpoints(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )

    assert client.get("/users", headers=auth_headers(user_token)).status_code == 403
    assert (
        client.post(
            "/documents",
            headers=auth_headers(user_token),
            json={
                "document_name": "Unauthorized",
                "document_type": "policy",
                "department": "HR",
                "classification": "INTERNAL",
            },
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/users/{admin.user_id}/attributes",
            headers=auth_headers(user_token),
            json={"attributes": {"department": "HR"}},
        ).status_code
        == 403
    )
    assert (
        client.get("/security/audit-logs", headers=auth_headers(user_token)).status_code
        == 403
    )

    # ADMIN (with the required attributes) can use them.
    assert client.get("/users", headers=auth_headers(admin_token)).status_code == 200
    assert (
        client.get("/security/audit-logs", headers=auth_headers(admin_token)).status_code
        == 200
    )


def test_admin_has_implicit_document_access_but_non_admin_does_not(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    document = create_document(client, admin_token, department="FINANCE")

    # No document_permissions row was created for ADMIN.
    check = access_check(client, admin_token, document["document_id"], "READ")
    assert check["allowed"] is True
    assert check["policy"] == "ALLOW"

    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="employee@alpha.com",
        username="employee_user",
        role_names=["EMPLOYEE"],
        department="FINANCE",
        clearance="3",
    )
    denied = access_check(client, user_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "RBAC_POLICY"


def test_role_grant_takes_effect_immediately_when_revoked(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    document = create_document(client, admin_token, department="ENGINEERING")
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )

    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        role_name="HR",
    )
    assert access_check(client, user_token, document["document_id"], "READ")["allowed"] is True

    revoke = client.delete(
        f"/users/{user['user_id']}/roles/HR",
        headers=auth_headers(admin_token),
    )
    assert revoke.status_code == 204, revoke.text

    # The old JWT is still cryptographically valid, but the dependency reloads
    # roles from PostgreSQL and the permission no longer matches.
    denied = access_check(client, user_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "RBAC_POLICY"

    # Admin endpoints are also denied immediately.
    assert client.get("/users", headers=auth_headers(user_token)).status_code == 403


def test_document_permission_does_not_grant_administrative_capability(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    document = create_document(client, admin_token, department="ENGINEERING")
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="manager@alpha.com",
        username="manager_user",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="MANAGE_PERMISSIONS",
        user_id=user["user_id"],
    )

    # Document permission does not leak into tenant administration.
    assert client.get("/users", headers=auth_headers(user_token)).status_code == 403
    assert (
        client.get("/security/audit-logs", headers=auth_headers(user_token)).status_code
        == 403
    )
