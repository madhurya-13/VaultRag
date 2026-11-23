"""Phase 5: explainability payload and history citation verification."""

from app.tests.helpers import admin_bundle_with_attributes, auth_headers, make_pdf_bytes
from app.tests.rag_helpers import (
    RecordingLLM,
    patch_rag_runtime,
    upload_processed_pdf,
)


def test_rag_explanation_is_bounded_and_sources_are_verified(client, db, monkeypatch):
    _, _, token = admin_bundle_with_attributes(
        client,
        db,
        organization_name="Explain Corp",
        email="explain@example.com",
        username="admin",
    )
    llm = RecordingLLM(answer="Employees receive 18 days of annual leave [S1].")
    patch_rag_runtime(monkeypatch, llm=llm)
    upload_processed_pdf(
        client,
        token,
        pdf_bytes=make_pdf_bytes(
            ["Acme Employee Handbook. Every permanent employee receives 18 days of annual leave each year."]
        ),
        filename="handbook.pdf",
        document_name="Acme Employee Handbook",
    )

    conversation = client.post(
        "/rag/conversations", headers=auth_headers(token), json={}
    ).json()
    response = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "annual leave days",
            "top_k": 5,
            "conversation_id": conversation["conversation_id"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["explanation"]["mode"] in {"semantic", "keyword", "hybrid", "graph", "graph_hybrid"}
    assert body["explanation"]["authorized_candidates"] >= 1
    assert "prompt" not in str(body).lower()
    assert "system" not in str(body["explanation"]).lower()
    assert body["sources"]
    assert all(source["citation_verified"] is True for source in body["sources"])
    assert body["feedback_enabled"] is True

    history = client.get(
        f"/rag/conversations/{body['conversation_id']}", headers=auth_headers(token)
    )
    assert history.status_code == 200
    citations = history.json()["messages"][-1]["citations"]
    assert citations
    assert all(citation["citation_verified"] is True for citation in citations)
