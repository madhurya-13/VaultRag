"""Document permission grant tests (role grants, direct grants, exact matching)."""

from uuid import UUID

from sqlalchemy import select

from app.main import app  # noqa: F401  (ensures routes are registered for TestClient)
from app.models import Document, DocumentPermission
from app.tests.helpers import (
    access_check,
    admin_create_user,
    create_admin_bundle,
    create_document,
    grant_document_permission,
    list_documents,
    login,
    read_document,
    seed_user_attributes,
    set_user_attributes,
    use_tenant,
)


def _admin_with_attributes(
    client,
    db,
    *,
    organization_name: str = "Alpha Corp",
    email: str = "admin@alpha.com",
    username: str = "alpha_admin",
    department: str = "ENGINEERING",
    clearance: str = "5",
):
    org, admin, token = create_admin_bundle(
        client, db, organization_name=organization_name, email=email, username=username
    )
    seed_user_attributes(
        db,
        org["tenant_id"],
        admin.user_id,
        {
            "department": department,
            "clearance_level": clearance,
            "employment_status": "ACTIVE",
        },
    )
    return org, admin, token


def _make_user(
    client,
    admin_token: str,
    tenant_id,
    *,
    email: str,
    username: str,
    role_name: str,
    department: str = "ENGINEERING",
    clearance: str = "3",
    employment_status: str = "ACTIVE",
):
    user = admin_create_user(
        client,
        admin_token,
        email=email,
        username=username,
        role_names=[role_name],
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
    token = login(client, tenant_id, email)
    return user, token


def test_role_permission_grants_access_to_every_user_with_that_role(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token, department="ENGINEERING")

    hr_a, hr_a_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="hr.a@alpha.com",
        username="hr_a",
        role_name="HR",
    )
    hr_b, hr_b_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="hr.b@alpha.com",
        username="hr_b",
        role_name="HR",
    )
    finance, finance_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="finance@alpha.com",
        username="finance_user",
        role_name="FINANCE",
    )

    permission = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        role_name="HR",
    )
    assert permission["role_id"] is not None
    assert permission["user_id"] is None

    for user, token in ((hr_a, hr_a_token), (hr_b, hr_b_token)):
        check = access_check(client, token, document["document_id"], "READ")
        assert check["allowed"] is True, (user, check)
        assert read_document(client, token, document["document_id"]).status_code == 200

    denied = access_check(client, finance_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "RBAC_POLICY"
    assert read_document(client, finance_token, document["document_id"]).status_code == 403


def test_direct_user_permission_does_not_grant_to_other_users(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token, department="ENGINEERING")

    allowed_user, allowed_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="allowed@alpha.com",
        username="allowed_user",
        role_name="HR",
    )
    other_user, other_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="other@alpha.com",
        username="other_user",
        role_name="HR",
    )

    permission = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=allowed_user["user_id"],
    )
    assert permission["user_id"] == allowed_user["user_id"]
    assert permission["role_id"] is None

    assert access_check(
        client, allowed_token, document["document_id"], "READ"
    )["allowed"] is True
    assert access_check(
        client, other_token, document["document_id"], "READ"
    )["allowed"] is False


def test_manage_permissions_is_exact_match_and_does_not_imply_read(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token, department="ENGINEERING")

    manager, manager_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="manager@alpha.com",
        username="manager_user",
        role_name="HR",
    )
    reader, reader_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="reader@alpha.com",
        username="reader_user",
        role_name="HR",
    )

    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="MANAGE_PERMISSIONS",
        user_id=manager["user_id"],
    )

    check = access_check(client, manager_token, document["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "RBAC_POLICY"

    # MANAGE_PERMISSIONS does allow the manager to grant READ to someone else.
    response = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "permission_type": "READ",
            "user_id": reader["user_id"],
        },
    )
    assert response.status_code == 201, response.text
    assert access_check(
        client, reader_token, document["document_id"], "READ"
    )["allowed"] is True


def test_grant_requires_exactly_one_subject(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token)

    both = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "permission_type": "READ",
            "role_name": "HR",
            "user_id": str(admin.user_id),
        },
    )
    assert both.status_code == 422, both.text

    neither = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"permission_type": "READ"},
    )
    assert neither.status_code == 422, neither.text


def test_duplicate_active_permission_is_rejected(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token)

    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        role_name="HR",
    )

    duplicate = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"permission_type": "READ", "role_name": "HR"},
    )
    assert duplicate.status_code == 409, duplicate.text

    use_tenant(db, org["tenant_id"])
    rows = db.scalars(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == UUID(org["tenant_id"]),
            DocumentPermission.document_id == UUID(document["document_id"]),
        )
    ).all()
    assert len(rows) == 1


def test_cross_tenant_document_and_user_grant_are_rejected(client, db):
    org_a, admin_a, admin_a_token = _admin_with_attributes(
        client,
        db,
        organization_name="Alpha Corp",
        email="admin@alpha.com",
        username="alpha_admin",
    )
    org_b, admin_b, admin_b_token = _admin_with_attributes(
        client,
        db,
        organization_name="Beta Corp",
        email="admin@beta.com",
        username="beta_admin",
    )

    document_a = create_document(client, admin_a_token, document_name="Alpha doc")

    # Tenant B cannot even see tenant A's document.
    assert read_document(client, admin_b_token, document_a["document_id"]).status_code == 404
    check = access_check(client, admin_b_token, document_a["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "RESOURCE_NOT_FOUND_POLICY"

    cross_grant = client.post(
        f"/documents/{document_a['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_b_token}"},
        json={"permission_type": "READ", "role_name": "HR"},
    )
    assert cross_grant.status_code == 404

    # Tenant A cannot grant a document to a user in tenant B.
    user_b = admin_create_user(
        client,
        admin_b_token,
        email="hr@beta.com",
        username="beta_hr",
        role_names=["HR"],
    )
    response = client.post(
        f"/documents/{document_a['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_a_token}"},
        json={"permission_type": "READ", "user_id": user_b["user_id"]},
    )
    assert response.status_code == 404, response.text

    use_tenant(db, org_a["tenant_id"])
    assert db.scalar(
        select(DocumentPermission).where(
            DocumentPermission.tenant_id == UUID(org_a["tenant_id"]),
            DocumentPermission.document_id == UUID(document_a["document_id"]),
        )
    ) is None


def test_inactive_document_denies_access_and_new_grants(client, db):
    org, admin, admin_token = _admin_with_attributes(client, db)
    document = create_document(client, admin_token, department="ENGINEERING")

    hr, hr_token = _make_user(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_name="HR",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        role_name="HR",
    )
    assert access_check(client, hr_token, document["document_id"], "READ")["allowed"] is True

    # App role has UPDATE on documents, but RLS still requires tenant context.
    use_tenant(db, org["tenant_id"])
    row = db.get(Document, UUID(document["document_id"]))
    assert row is not None
    row.is_active = False
    db.commit()

    check = access_check(client, hr_token, document["document_id"], "READ")
    assert check["allowed"] is False
    assert check["policy"] == "DOCUMENT_ACTIVE_POLICY"
    assert read_document(client, hr_token, document["document_id"]).status_code == 403
    assert document["document_id"] not in {
        item["document_id"] for item in list_documents(client, hr_token)
    }

    blocked_grant = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"permission_type": "UPDATE", "role_name": "HR"},
    )
    # The central engine denies before the service layer, even for ADMIN.
    assert blocked_grant.status_code == 403, blocked_grant.text
