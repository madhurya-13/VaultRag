"""Phase 3: feedback ownership, duplicate handling, review and isolation."""

from uuid import UUID

from app.core.database import set_tenant_context
from app.models import Conversation, ConversationMessage
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    tenant_user_with_attributes,
)
from app.core.security_constants import ROLE_EMPLOYEE


def _message_for(db, tenant_id, user_id, *, content="Employees receive 18 days."):
    set_tenant_context(db, UUID(tenant_id))
    conversation = Conversation(tenant_id=UUID(tenant_id), user_id=UUID(user_id))
    db.add(conversation)
    db.flush()
    message = ConversationMessage(
        tenant_id=UUID(tenant_id),
        conversation_id=conversation.conversation_id,
        user_id=UUID(user_id),
        role="assistant",
        content=content,
        evidence_level="moderate",
    )
    db.add(message)
    db.commit()
    return conversation, message


def test_feedback_owner_flow_and_admin_review(client, db):
    org, admin, admin_token = admin_bundle_with_attributes(
        client, db, organization_name="Feedback Corp", email="fb-admin@example.com", username="admin"
    )
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        org["tenant_id"],
        email="fb-user@example.com",
        username="fbuser",
        role_names=[ROLE_EMPLOYEE],
    )
    conversation, message = _message_for(db, org["tenant_id"], user["user_id"])

    first = client.post(
        "/feedback",
        headers=auth_headers(user_token),
        json={
            "message_id": str(message.message_id),
            "category": "missing_citation",
            "sentiment": "negative",
            "comment": "No source was shown.",
        },
    )
    assert first.status_code == 201, first.text
    feedback_id = first.json()["feedback_id"]

    duplicate = client.post(
        "/feedback",
        headers=auth_headers(user_token),
        json={
            "message_id": str(message.message_id),
            "category": "incomplete_answer",
            "sentiment": "negative",
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["feedback_id"] == feedback_id
    assert duplicate.json()["category"] == "INCOMPLETE_ANSWER"

    mine = client.get("/feedback/mine", headers=auth_headers(user_token))
    assert mine.status_code == 200
    assert len(mine.json()["feedback"]) == 1

    forbidden = client.get("/admin/feedback", headers=auth_headers(user_token))
    assert forbidden.status_code == 403

    listing = client.get("/admin/feedback", headers=auth_headers(admin_token))
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = client.get(
        f"/admin/feedback/{feedback_id}", headers=auth_headers(admin_token)
    )
    assert detail.status_code == 200
    assert detail.json()["question"] is None  # no user message exists in fixture
    assert "18 days" in detail.json()["answer"]

    reviewed = client.post(
        f"/admin/feedback/{feedback_id}/review",
        headers=auth_headers(admin_token),
        json={"status": "resolved", "review_notes": "Acknowledged."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == "RESOLVED"
    assert reviewed.json()["reviewer_id"] == str(admin.user_id)

    summary = client.get(
        "/admin/feedback/summary", headers=auth_headers(admin_token)
    )
    assert summary.status_code == 200
    assert summary.json()["total"] == 1
    assert summary.json()["positive"] == 0

    retracted = client.delete(
        f"/feedback/{feedback_id}", headers=auth_headers(user_token)
    )
    assert retracted.status_code == 204
    assert client.get("/feedback/mine", headers=auth_headers(user_token)).json()["total"] == 0
    assert conversation.conversation_id


def test_feedback_cannot_target_another_tenants_message(client, db):
    org_a, _, admin_a = admin_bundle_with_attributes(
        client, db, organization_name="Feedback A", email="a@example.com", username="admina"
    )
    user_a, user_a_token = tenant_user_with_attributes(
        client,
        admin_a,
        org_a["tenant_id"],
        email="usera@example.com",
        username="usera",
        role_names=[ROLE_EMPLOYEE],
    )
    org_b, _, admin_b = admin_bundle_with_attributes(
        client, db, organization_name="Feedback B", email="b@example.com", username="adminb"
    )
    user_b, _ = tenant_user_with_attributes(
        client,
        admin_b,
        org_b["tenant_id"],
        email="userb@example.com",
        username="userb",
        role_names=[ROLE_EMPLOYEE],
    )
    _, message = _message_for(db, org_b["tenant_id"], user_b["user_id"])

    response = client.post(
        "/feedback",
        headers=auth_headers(user_a_token),
        json={
            "message_id": str(message.message_id),
            "category": "other",
            "sentiment": "negative",
        },
    )
    assert response.status_code == 404
    assert user_a["user_id"]
