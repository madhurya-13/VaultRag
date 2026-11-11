"""Role catalogue API: tenant ownership, capabilities and ADMIN-only access."""

from app.core.security_constants import (
    CAP_CREATE_DOCUMENT,
    CAP_MANAGE_ATTRIBUTES,
    CAP_MANAGE_ROLES,
    CAP_MANAGE_USERS,
    CAP_PROCESS_DOCUMENT,
    CAP_VIEW_AUDIT_LOGS,
    CAP_VIEW_CHUNKS,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_ENGINEERING,
    ROLE_FINANCE,
    ROLE_HR,
)
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    tenant_user_with_attributes,
)

EXPECTED_ROLE_NAMES = [
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_ENGINEERING,
    ROLE_FINANCE,
    ROLE_HR,
]
ADMIN_CAPABILITIES = {
    CAP_CREATE_DOCUMENT,
    CAP_MANAGE_ATTRIBUTES,
    CAP_MANAGE_ROLES,
    CAP_MANAGE_USERS,
    CAP_PROCESS_DOCUMENT,
    CAP_VIEW_AUDIT_LOGS,
    CAP_VIEW_CHUNKS,
}


def test_role_catalogue_returns_capabilities_and_user_counts(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=[ROLE_HR, ROLE_EMPLOYEE],
    )

    response = client.get("/roles", headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    roles = response.json()

    assert [role["role_name"] for role in roles] == EXPECTED_ROLE_NAMES
    by_name = {role["role_name"]: role for role in roles}

    assert set(by_name[ROLE_ADMIN]["capabilities"]) == ADMIN_CAPABILITIES
    assert by_name[ROLE_ADMIN]["user_count"] == 1
    assert by_name[ROLE_ADMIN]["description"]

    for role_name in (ROLE_EMPLOYEE, ROLE_ENGINEERING, ROLE_FINANCE, ROLE_HR):
        assert by_name[role_name]["capabilities"] == []

    assert by_name[ROLE_HR]["user_count"] == 1
    assert by_name[ROLE_EMPLOYEE]["user_count"] == 1
    assert by_name[ROLE_ENGINEERING]["user_count"] == 0
    assert all(role["role_id"] for role in roles)


def test_role_catalogue_is_tenant_isolated(client, db):
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

    tenant_user_with_attributes(
        client,
        admin_a_token,
        org_a["tenant_id"],
        email="hr@alpha.com",
        username="alpha_hr",
        role_names=[ROLE_HR],
    )

    roles_a = client.get("/roles", headers=auth_headers(admin_a_token)).json()
    roles_b = client.get("/roles", headers=auth_headers(admin_b_token)).json()

    assert {role["role_id"] for role in roles_a}.isdisjoint(
        {role["role_id"] for role in roles_b}
    )
    assert {role["role_name"]: role["user_count"] for role in roles_a}[
        ROLE_HR
    ] == 1
    assert {role["role_name"]: role["user_count"] for role in roles_b}[
        ROLE_HR
    ] == 0
    assert {role["role_name"]: role["user_count"] for role in roles_b}[
        ROLE_ADMIN
    ] == 1


def test_role_catalogue_requires_manage_roles_capability(client, db):
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
        email="employee@alpha.com",
        username="employee_user",
        role_names=[ROLE_EMPLOYEE],
    )

    assert user["user_id"]
    denied = client.get("/roles", headers=auth_headers(user_token))
    assert denied.status_code == 403

    anonymous = client.get("/roles")
    assert anonymous.status_code == 401
