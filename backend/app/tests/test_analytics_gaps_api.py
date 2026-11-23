"""Phases 4 and 6: gap classification/privacy and analytics access."""

from app.services.knowledge_gaps import classify, normalize_question, topic_key
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    tenant_user_with_attributes,
)
from app.core.security_constants import ROLE_EMPLOYEE


def test_permission_denied_is_never_a_content_gap():
    classification = classify(
        question="What is the executive salary band?",
        candidate_count=4,
        authorized_count=0,
        denied_count=4,
        retrieved_chunks=0,
    )
    assert classification == "INSUFFICIENT_PERMISSIONS"

    missing = classify(
        question="What is the moon base policy?",
        candidate_count=0,
        authorized_count=0,
        denied_count=0,
        retrieved_chunks=0,
    )
    assert missing == "MISSING_KNOWLEDGE"
    poor = classify(
        question="How many leave days?",
        candidate_count=10,
        authorized_count=10,
        denied_count=0,
        retrieved_chunks=0,
    )
    assert poor == "POOR_RETRIEVAL"


def test_gap_normalization_redacts_identity_and_numbers():
    normalized = normalize_question("Email jane.doe@example.com about ticket 123456 today")
    assert "[email]" in normalized
    assert "[number]" in normalized
    assert "jane.doe" not in normalized
    assert topic_key("How many annual leave days do employees receive?") == "annual leave days employees receive"


def test_admin_analytics_and_gap_endpoints_are_tenant_scoped_and_gated(client, db):
    org, _, token = admin_bundle_with_attributes(
        client, db, organization_name="Analytics Corp", email="analytics@example.com", username="admin"
    )
    for path in (
        "/analytics/overview",
        "/analytics/usage",
        "/analytics/documents",
        "/analytics/retrieval",
        "/analytics/integrations",
        "/analytics/feedback",
        "/analytics/knowledge-gaps",
        "/analytics/security",
    ):
        response = client.get(path, headers=auth_headers(token))
        assert response.status_code == 200, (path, response.text)
        assert response.json()["days"] == 30

    gaps = client.get("/admin/knowledge-gaps", headers=auth_headers(token))
    assert gaps.status_code == 200
    assert gaps.json()["total"] == 0
    summary = client.get("/admin/knowledge-gaps/summary", headers=auth_headers(token))
    assert summary.status_code == 200
    assert summary.json()["minimum_group_size"] == 3

    _, employee_token = tenant_user_with_attributes(
        client,
        token,
        org["tenant_id"],
        email="analytics-employee@example.com",
        username="employee",
        role_names=[ROLE_EMPLOYEE],
    )
    assert client.get("/analytics/overview", headers=auth_headers(employee_token)).status_code == 403
    assert client.get("/admin/knowledge-gaps", headers=auth_headers(employee_token)).status_code == 403
