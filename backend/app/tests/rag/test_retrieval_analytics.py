"""Retrieval analytics: tenant scoping, privacy and authorization."""

from uuid import uuid4

from app.models import RetrievalAnalytics
from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    make_pdf_bytes,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import patch_rag_runtime, upload_processed_pdf


def _ask(client, token, question="What is the annual leave policy?"):
    return client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={"question": question, "top_k": 10, "search_mode": "hybrid"},
    )


def test_analytics_are_recorded_and_aggregated_for_the_tenant(client, db, monkeypatch):
    _, _ = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Analytics {uuid4()}",
        email=f"analytics-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy: 18 days."]),
        filename="analytics.pdf",
        document_name="Analytics.pdf",
        department="HR",
    )
    assert _ask(client, token).status_code == 200

    response = client.get(
        "/rag/analytics", headers=auth_headers(token), params={"days": 7}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_queries"] >= 1
    assert any(item["mode"] == "hybrid" for item in body["by_search_mode"])
    assert 0.0 <= body["citation_validation_rate"] <= 1.0
    assert 0.0 <= body["insufficient_evidence_rate"] <= 1.0
    assert 0.0 <= body["retrieval_error_rate"] <= 1.0
    assert body["average_total_ms"] >= 0
    assert body["daily"]


def test_analytics_require_the_admin_capability(client, db, monkeypatch):
    _, _ = patch_rag_runtime(monkeypatch, rag_min_similarity=0.0)
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Analytics gate {uuid4()}",
        email=f"analytics-gate-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    _, employee_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"analytics-employee-{uuid4()}@example.com",
        username="analyticuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )

    denied = client.get("/rag/analytics", headers=auth_headers(employee_token))
    allowed = client.get("/rag/analytics", headers=auth_headers(admin_token))

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_analytics_are_isolated_between_tenants(client, db, monkeypatch):
    _, _ = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    _, _, token_a = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Analytics A {uuid4()}",
        email=f"analytics-a-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token_a,
        pdf_bytes=make_pdf_bytes(["Annual leave policy: 18 days."]),
        filename="a.pdf",
        document_name="A.pdf",
        department="HR",
    )
    assert _ask(client, token_a).status_code == 200

    _, _, token_b = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Analytics B {uuid4()}",
        email=f"analytics-b-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )

    response = client.get("/rag/analytics", headers=auth_headers(token_b))

    assert response.status_code == 200, response.text
    assert response.json()["total_queries"] == 0


def test_analytics_schema_contains_no_question_or_document_text():
    columns = {column.name for column in RetrievalAnalytics.__table__.columns}

    for forbidden in (
        "question",
        "answer",
        "chunk_text",
        "prompt",
        "content",
        "document_name",
        "token",
    ):
        assert forbidden not in columns


def test_analytics_rows_are_tenant_scoped_in_the_database(client, db, monkeypatch):
    from app.core.database import set_tenant_context
    from sqlalchemy import func, select

    _, _ = patch_rag_runtime(
        monkeypatch, rag_min_similarity=0.0, rag_max_context_tokens=1000
    )
    organization, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Analytics RLS {uuid4()}",
        email=f"analytics-rls-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(["Annual leave policy: 18 days."]),
        filename="rls.pdf",
        document_name="RLS.pdf",
        department="HR",
    )
    assert _ask(client, token).status_code == 200

    from uuid import UUID

    set_tenant_context(db, UUID(str(organization["tenant_id"])))
    count = db.scalar(
        select(func.count()).select_from(RetrievalAnalytics)
    )
    assert count and count >= 1
    rows = db.scalars(select(RetrievalAnalytics)).all()
    assert all(
        str(row.tenant_id) == str(organization["tenant_id"]) for row in rows
    )
