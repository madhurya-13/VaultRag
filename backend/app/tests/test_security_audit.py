"""Security audit logging: decisions, append-only storage and tenant isolation."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.models import SecurityAuditLog
from app.tests.helpers import (
    access_check,
    admin_bundle_with_attributes,
    admin_create_user,
    auth_headers,
    create_document,
    get_audit_logs,
    grant_document_permission,
    list_documents,
    read_document,
    revoke_document_permission,
    set_user_attributes,
    tenant_user_with_attributes,
    use_tenant,
)


def _events_for(events, **filters):
    return [
        event
        for event in events
        if all(event.get(key) == value for key, value in filters.items())
    ]


def test_allowed_and_denied_document_checks_are_audited(client, db):
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

    assert read_document(client, admin_token, document["document_id"]).status_code == 200
    assert read_document(client, user_token, document["document_id"]).status_code == 403

    events = get_audit_logs(
        client,
        admin_token,
        resource_id=document["document_id"],
        action="READ",
    )
    assert len(events) == 2, events
    decisions = {event["decision"] for event in events}
    assert decisions == {"ALLOW", "DENY"}
    assert all(event["resource_type"] == "DOCUMENT" for event in events)
    assert all(event["policy"] for event in events)


def test_permission_grant_and_revocation_are_audited(client, db):
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
    revoke_document_permission(
        client, admin_token, document["document_id"], permission["permission_id"]
    )

    events = get_audit_logs(
        client,
        admin_token,
        resource_id=permission["permission_id"],
        resource_type="DOCUMENT_PERMISSION",
    )
    actions = {event["action"] for event in events}
    assert {"PERMISSION_GRANT", "PERMISSION_REVOKE"} <= actions
    assert all(event["decision"] == "ALLOW" for event in events)


def test_administrative_changes_are_audited(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    user = admin_create_user(
        client,
        admin_token,
        email="hr@alpha.com",
        username="hr_user",
        role_names=["HR"],
    )

    assign = client.post(
        f"/users/{user['user_id']}/roles",
        headers=auth_headers(admin_token),
        json={"role_name": "EMPLOYEE"},
    )
    assert assign.status_code == 200, assign.text

    remove = client.delete(
        f"/users/{user['user_id']}/roles/EMPLOYEE",
        headers=auth_headers(admin_token),
    )
    assert remove.status_code == 204, remove.text

    status_response = client.patch(
        f"/users/{user['user_id']}/status",
        headers=auth_headers(admin_token),
        json={"is_active": True},
    )
    assert status_response.status_code == 200, status_response.text

    set_user_attributes(
        client,
        admin_token,
        user["user_id"],
        {
            "department": "HR",
            "clearance_level": "2",
            "employment_status": "ACTIVE",
        },
    )

    events = get_audit_logs(client, admin_token, resource_id=user["user_id"])
    actions = {event["action"] for event in events}
    assert {
        "USER_CREATE",
        "ROLE_ASSIGN",
        "ROLE_REVOKE",
        "USER_STATUS_UPDATE",
        "ATTRIBUTE_UPDATE",
    } <= actions
    assert all(event["decision"] == "ALLOW" for event in events)


def test_audit_logs_are_tenant_isolated_and_admin_only(client, db):
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
    document_a = create_document(client, admin_a_token, document_name="Alpha only")
    document_b = create_document(client, admin_b_token, document_name="Beta only")
    assert read_document(client, admin_a_token, document_a["document_id"]).status_code == 200
    assert read_document(client, admin_b_token, document_b["document_id"]).status_code == 200

    user_a, user_a_token = tenant_user_with_attributes(
        client,
        admin_a_token,
        org_a["tenant_id"],
        email="hr@alpha.com",
        username="alpha_hr",
        role_names=["HR"],
    )

    events_a = get_audit_logs(client, admin_a_token)
    assert events_a
    assert all(event["tenant_id"] == org_a["tenant_id"] for event in events_a)
    assert not _events_for(events_a, resource_id=document_b["document_id"])

    # Tenant B cannot filter its way into tenant A's events.
    cross_filter = get_audit_logs(
        client, admin_b_token, resource_id=document_a["document_id"]
    )
    assert cross_filter == []

    # A normal employee cannot read audit logs at all.
    denied = client.get(
        "/security/audit-logs", headers=auth_headers(user_a_token)
    )
    assert denied.status_code == 403


def test_audit_rows_are_append_only_for_the_runtime_role(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    assert read_document(client, admin_token, document["document_id"]).status_code == 200

    events = get_audit_logs(
        client,
        admin_token,
        resource_id=document["document_id"],
        action="READ",
    )
    assert events
    event_id = UUID(events[0]["event_id"])

    use_tenant(db, org["tenant_id"])
    with pytest.raises(DBAPIError):
        db.execute(
            update(SecurityAuditLog)
            .where(SecurityAuditLog.event_id == event_id)
            .values(reason="tampered")
        )
        db.flush()
    db.rollback()

    use_tenant(db, org["tenant_id"])
    with pytest.raises(DBAPIError):
        db.execute(
            delete(SecurityAuditLog).where(SecurityAuditLog.event_id == event_id)
        )
        db.flush()
    db.rollback()

    # The row is still present and unchanged.
    still_there = get_audit_logs(
        client,
        admin_token,
        resource_id=document["document_id"],
        action="READ",
    )
    assert any(event["event_id"] == str(event_id) for event in still_there)
    assert all(event["reason"] != "tampered" for event in still_there)


def test_audit_failure_fails_closed_and_rolls_back_the_business_action(
    client, db, migration_db
):
    """TEST - a security action must not succeed without its audit record."""
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )

    migration_db.execute(
        text("REVOKE INSERT ON security_audit_logs FROM vaultrag_app")
    )
    migration_db.commit()
    try:
        with pytest.raises(DBAPIError):
            client.post(
                "/documents",
                headers=auth_headers(admin_token),
                json={
                    "document_name": "Must roll back",
                    "document_type": "policy",
                    "department": "ENGINEERING",
                    "classification": "INTERNAL",
                },
            )
    finally:
        migration_db.execute(
            text("GRANT INSERT ON security_audit_logs TO vaultrag_app")
        )
        migration_db.commit()

    # The insert was flushed before the audit write failed, so this proves the
    # request transaction (not just the audit statement) was rolled back.
    assert list_documents(client, admin_token) == []


def test_request_id_correlates_api_response_and_audit_row(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )
    document = create_document(client, admin_token)
    request_id = uuid4()

    response = client.get(
        f"/documents/{document['document_id']}",
        headers={
            **auth_headers(admin_token),
            "X-Request-ID": str(request_id),
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Request-ID"] == str(request_id)

    events = get_audit_logs(
        client,
        admin_token,
        resource_id=document["document_id"],
        action="READ",
    )
    assert any(event["request_id"] == str(request_id) for event in events)


def test_reading_audit_logs_is_itself_audited(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Alpha Corp", email="admin@alpha.com", username="alpha_admin"
    )

    first = get_audit_logs(client, admin_token)
    assert first == []  # nothing except the view event being written after this query

    second = get_audit_logs(client, admin_token, resource_type="SECURITY_AUDIT_LOG")
    assert second
    assert second[0]["action"] == "VIEW"
    assert second[0]["decision"] == "ALLOW"
