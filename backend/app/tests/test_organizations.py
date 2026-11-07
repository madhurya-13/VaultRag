"""Tests for organization registration and tenant metadata endpoints."""

from uuid import UUID

from app.tests.helpers import (
    auth_headers,
    login,
    provision_admin,
    register_organization,
)


def test_register_two_organizations_receive_distinct_uuids(client):
    """TEST 1 - a tenant's identity is generated server-side and is unique.

    Vulnerability checked: tenant collision / attacker influence over the
    tenant identifier. If two organizations could share a tenant_id, every
    isolation guarantee downstream would be meaningless.
    """
    first = register_organization(client, "TechNova")
    second = register_organization(client, "BioCore")

    assert first["tenant_id"] != second["tenant_id"]

    # They must be valid UUIDs, not predictable sequential integers.
    UUID(first["tenant_id"])
    UUID(second["tenant_id"])

    assert first["is_active"] is True
    assert first["roles"] == ["ADMIN", "EMPLOYEE", "ENGINEERING", "FINANCE", "HR"]


def test_organization_registration_creates_default_roles(client):
    """Every tenant gets its own role rows, not shared global roles."""
    organization = register_organization(client, "TechNova")
    assert set(organization["roles"]) == {
        "ADMIN",
        "HR",
        "FINANCE",
        "ENGINEERING",
        "EMPLOYEE",
    }


def test_organization_registration_rejects_mass_assignment(client):
    """Public registration must not accept hidden state such as is_active/roles."""
    response = client.post(
        "/organizations/register",
        json={"organization_name": "EvilCorp", "is_active": False},
    )
    assert response.status_code == 422

    response = client.post(
        "/organizations/register",
        json={"organization_name": "EvilCorp", "roles": ["ADMIN"]},
    )
    assert response.status_code == 422


def test_organization_me_requires_authentication(client):
    """Missing tenant context must deny, not default to unrestricted access."""
    response = client.get("/organizations/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_organization_me_returns_only_the_authenticated_tenant(client, db):
    """The tenant comes from the token's user row, not from any request input."""
    organization = register_organization(client, "TechNova")
    other = register_organization(client, "BioCore")

    provision_admin(db, organization["tenant_id"], email="admin@technova.com")
    token = login(client, organization["tenant_id"], "admin@technova.com")

    response = client.get("/organizations/me", headers=auth_headers(token))
    assert response.status_code == 200

    body = response.json()
    assert body["tenant_id"] == organization["tenant_id"]
    assert body["tenant_id"] != other["tenant_id"]
    assert body["organization_name"] == "TechNova"
