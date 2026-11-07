"""ABAC attribute validation and policy evaluation tests."""

from uuid import UUID

from sqlalchemy import select

from app.models import UserAttribute
from app.tests.helpers import (
    access_check,
    admin_bundle_with_attributes,
    admin_create_user,
    create_document,
    get_user_attributes,
    grant_document_permission,
    login,
    read_document,
    set_user_attributes,
    tenant_user_with_attributes,
    use_tenant,
)


def test_missing_attributes_fail_closed(client, db):
    """Vulnerability checked: missing ABAC data silently granting access."""
    from app.tests.helpers import create_admin_bundle

    org, admin, admin_token = create_admin_bundle(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    document = create_document(client, admin_token)

    # ADMIN bypasses the permission check, but ABAC has no attributes to
    # evaluate. Missing mandatory attributes must therefore deny.
    check = access_check(client, admin_token, document["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "ATTRIBUTE_VALIDATION_POLICY"

    assert read_document(client, admin_token, document["document_id"]).status_code == 403


def test_attribute_api_self_service_read_and_admin_write(client, db):
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
        department="HR",
        clearance="2",
    )
    other, other_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="other@alpha.com",
        username="other_user",
        role_names=["HR"],
        department="HR",
        clearance="2",
    )

    own = get_user_attributes(client, user_token, user["user_id"])
    assert {row["attribute_name"] for row in own["attributes"]} == {
        "department",
        "clearance_level",
        "employment_status",
    }

    # A normal user cannot read somebody else's attributes.
    forbidden = client.get(
        f"/users/{other['user_id']}/attributes",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert forbidden.status_code == 403

    # ...and cannot write attributes at all.
    forbidden_write = client.put(
        f"/users/{other['user_id']}/attributes",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"attributes": {"department": "HR"}},
    )
    assert forbidden_write.status_code == 403

    # ADMIN can read another user's attributes.
    admin_view = get_user_attributes(client, admin_token, user["user_id"])
    assert admin_view["attributes"]

    # ADMIN cannot change their OWN clearance, even with a valid request.
    self_clearance = client.put(
        f"/users/{admin.user_id}/attributes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"attributes": {"clearance_level": "5"}},
    )
    assert self_clearance.status_code == 400, self_clearance.text

    # Invalid values are rejected before they can change policy outcomes.
    bad_department = client.put(
        f"/users/{user['user_id']}/attributes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"attributes": {"department": "MARS"}},
    )
    assert bad_department.status_code == 400

    bad_clearance = client.put(
        f"/users/{user['user_id']}/attributes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"attributes": {"clearance_level": "99"}},
    )
    assert bad_clearance.status_code == 400

    unknown_attribute = client.put(
        f"/users/{user['user_id']}/attributes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"attributes": {"favourite_colour": "blue"}},
    )
    assert unknown_attribute.status_code == 422


def test_department_mismatch_and_explicit_cross_department_override(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
        department="ENGINEERING",
        clearance="5",
    )
    document = create_document(
        client, admin_token, department="FINANCE", classification="INTERNAL"
    )

    mismatched, mismatched_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="eng1@alpha.com",
        username="eng_one",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    crossed, crossed_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="eng2@alpha.com",
        username="eng_two",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    native, native_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="fin@alpha.com",
        username="fin_user",
        role_names=["FINANCE"],
        department="FINANCE",
        clearance="3",
    )

    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=mismatched["user_id"],
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=crossed["user_id"],
        cross_department_allowed=True,
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=native["user_id"],
    )

    denied = access_check(client, mismatched_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "DEPARTMENT_POLICY"

    allowed = access_check(client, crossed_token, document["document_id"], "READ")
    assert allowed["allowed"] is True

    native_allowed = access_check(client, native_token, document["document_id"], "READ")
    assert native_allowed["allowed"] is True


def test_clearance_floor_and_insufficient_clearance(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
        department="ENGINEERING",
        clearance="2",
    )

    # CONFIDENTIAL has an automatic floor of 3, even if the caller asks for 1.
    invalid_floor = client.post(
        "/documents",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "document_name": "Too visible",
            "document_type": "policy",
            "department": "ENGINEERING",
            "classification": "CONFIDENTIAL",
            "required_clearance_level": 1,
        },
    )
    assert invalid_floor.status_code == 400, invalid_floor.text

    document = create_document(
        client,
        admin_token,
        department="FINANCE",
        classification="CONFIDENTIAL",
    )
    assert document["required_clearance_level"] == 3

    # ADMIN bypasses department, but NOT clearance.
    admin_check = access_check(client, admin_token, document["document_id"], "READ")
    assert admin_check["allowed"] is False
    assert admin_check["policy"] == "CLEARANCE_POLICY"

    # Give the administrator sufficient clearance for setup. (In production a
    # second administrator or operator performs this bootstrap step.)
    from app.tests.helpers import seed_user_attributes

    seed_user_attributes(
        db,
        org["tenant_id"],
        admin.user_id,
        {"clearance_level": "5"},
    )

    low, low_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="low@alpha.com",
        username="low_clearance",
        role_names=["FINANCE"],
        department="FINANCE",
        clearance="2",
    )
    high, high_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="high@alpha.com",
        username="high_clearance",
        role_names=["FINANCE"],
        department="FINANCE",
        clearance="3",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=low["user_id"],
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=high["user_id"],
    )

    denied = access_check(client, low_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "CLEARANCE_POLICY"

    allowed = access_check(client, high_token, document["document_id"], "READ")
    assert allowed["allowed"] is True


def test_inactive_employment_status_always_denies(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token, department="ENGINEERING")

    former, former_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="former@alpha.com",
        username="former_user",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="5",
        employment_status="TERMINATED",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=former["user_id"],
    )

    check = access_check(client, former_token, document["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "EMPLOYMENT_STATUS_POLICY"
    assert read_document(client, former_token, document["document_id"]).status_code == 403


def test_malformed_database_attribute_fails_closed(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token, department="ENGINEERING")

    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="bad@alpha.com",
        username="bad_attr",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )

    # The API would reject this value. Simulate a compromised/buggy writer that
    # bypasses validation but not the database check constraints.
    use_tenant(db, org["tenant_id"])
    row = db.scalar(
        select(UserAttribute).where(
            UserAttribute.tenant_id == UUID(org["tenant_id"]),
            UserAttribute.user_id == UUID(user["user_id"]),
            UserAttribute.attribute_name == "department",
        )
    )
    assert row is not None
    row.attribute_value = "MARS"
    db.commit()

    check = access_check(client, user_token, document["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "ATTRIBUTE_VALIDATION_POLICY"


def test_cross_tenant_attribute_access_is_not_found(client, db):
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
    user_b = admin_create_user(
        client,
        admin_b_token,
        email="hr@beta.com",
        username="beta_hr",
        role_names=["HR"],
    )

    # Tenant A cannot write or read tenant B's user attributes.
    write = client.put(
        f"/users/{user_b['user_id']}/attributes",
        headers={"Authorization": f"Bearer {admin_a_token}"},
        json={"attributes": {"department": "HR"}},
    )
    assert write.status_code == 404, write.text

    read = client.get(
        f"/users/{user_b['user_id']}/attributes",
        headers={"Authorization": f"Bearer {admin_a_token}"},
    )
    assert read.status_code == 404, read.text

    # A tenant B admin also cannot see tenant A's admin attributes.
    read_a = client.get(
        f"/users/{admin_a.user_id}/attributes",
        headers={"Authorization": f"Bearer {admin_b_token}"},
    )
    assert read_a.status_code == 404, read_a.text
