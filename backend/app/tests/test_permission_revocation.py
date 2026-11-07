"""Permission revocation, expiry and re-grant tests."""

import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models import DocumentPermission
from app.tests.helpers import (
    access_check,
    admin_bundle_with_attributes,
    auth_headers,
    create_document,
    grant_document_permission,
    list_documents,
    revoke_document_permission,
    tenant_user_with_attributes,
    use_tenant,
)


def _permission_rows(db, tenant_id, document_id):
    use_tenant(db, tenant_id)
    return list(
        db.scalars(
            select(DocumentPermission)
            .where(
                DocumentPermission.tenant_id == UUID(str(tenant_id)),
                DocumentPermission.document_id == UUID(str(document_id)),
            )
            .order_by(DocumentPermission.granted_at)
        ).all()
    )


def test_revocation_is_immediate_and_preserves_history(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
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
    permission = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )
    assert access_check(client, user_token, document["document_id"], "READ")["allowed"] is True

    revoke_document_permission(
        client, admin_token, document["document_id"], permission["permission_id"]
    )

    denied = access_check(client, user_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "PERMISSION_VALIDITY_POLICY"

    rows = _permission_rows(db, org["tenant_id"], document["document_id"])
    assert len(rows) == 1
    assert rows[0].is_active is False
    assert rows[0].revoked_at is not None
    assert rows[0].revoked_by == admin.user_id


def test_revoke_unknown_permission_is_404_and_double_revoke_is_409(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    user, _ = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )
    permission = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )

    unknown = client.delete(
        f"/documents/{document['document_id']}/permissions/{uuid4()}",
        headers=auth_headers(admin_token),
    )
    assert unknown.status_code == 404

    revoke_document_permission(
        client, admin_token, document["document_id"], permission["permission_id"]
    )
    second = client.delete(
        f"/documents/{document['document_id']}/permissions/{permission['permission_id']}",
        headers=auth_headers(admin_token),
    )
    assert second.status_code == 409, second.text


def test_expired_permission_is_denied_and_hidden_from_list(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
        expires_at=expires_at.isoformat(),
    )
    assert access_check(client, user_token, document["document_id"], "READ")["allowed"] is True

    # A short real wait keeps the database's expires_at > granted_at constraint
    # intact while proving the authorization engine ignores expired grants.
    time.sleep(1.3)

    denied = access_check(client, user_token, document["document_id"], "READ")
    assert denied["allowed"] is False
    assert denied["policy"] == "PERMISSION_VALIDITY_POLICY"
    assert document["document_id"] not in {
        item["document_id"] for item in list_documents(client, user_token)
    }


def test_past_expiry_is_rejected_at_grant_time(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    user, _ = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )

    response = client.post(
        f"/documents/{document['document_id']}/permissions",
        headers=auth_headers(admin_token),
        json={
            "permission_type": "READ",
            "user_id": user["user_id"],
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        },
    )
    assert response.status_code == 400, response.text


def test_regrant_after_revocation_creates_a_new_active_row(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )
    first = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )
    revoke_document_permission(
        client, admin_token, document["document_id"], first["permission_id"]
    )
    assert access_check(client, user_token, document["document_id"], "READ")["allowed"] is False

    second = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=user["user_id"],
    )
    assert second["permission_id"] != first["permission_id"]
    assert access_check(client, user_token, document["document_id"], "READ")["allowed"] is True

    rows = _permission_rows(db, org["tenant_id"], document["document_id"])
    assert len(rows) == 2
    assert sum(1 for row in rows if row.is_active) == 1


def test_non_admin_without_manage_cannot_revoke_and_manager_can(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    reader, reader_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="reader@alpha.com",
        username="reader_user",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    manager, manager_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="manager@alpha.com",
        username="manager_user",
        role_names=["HR"],
        department="ENGINEERING",
        clearance="3",
    )
    permission = grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="READ",
        user_id=reader["user_id"],
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        permission_type="MANAGE_PERMISSIONS",
        user_id=manager["user_id"],
    )

    denied = client.delete(
        f"/documents/{document['document_id']}/permissions/{permission['permission_id']}",
        headers=auth_headers(reader_token),
    )
    assert denied.status_code == 403

    allowed = client.delete(
        f"/documents/{document['document_id']}/permissions/{permission['permission_id']}",
        headers=auth_headers(manager_token),
    )
    assert allowed.status_code == 204, allowed.text
    assert access_check(client, reader_token, document["document_id"], "READ")["allowed"] is False


def test_cross_tenant_revocation_is_not_found(client, db):
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
    document = create_document(client, admin_a_token)
    user_a, _ = tenant_user_with_attributes(
        client,
        admin_a_token,
        org_a["tenant_id"],
        email="hr@alpha.com",
        username="alpha_hr",
        role_names=["HR"],
    )
    permission = grant_document_permission(
        client,
        admin_a_token,
        document["document_id"],
        permission_type="READ",
        user_id=user_a["user_id"],
    )

    response = client.delete(
        f"/documents/{document['document_id']}/permissions/{permission['permission_id']}",
        headers=auth_headers(admin_b_token),
    )
    assert response.status_code == 404

    # The original grant is untouched.
    rows = _permission_rows(db, org_a["tenant_id"], document["document_id"])
    assert len(rows) == 1
    assert rows[0].is_active is True
