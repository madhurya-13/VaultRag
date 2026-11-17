"""Conversation ownership, persistence, clearing and revocation behavior."""

from uuid import uuid4

from app.tests.helpers import (
    admin_bundle_with_attributes,
    auth_headers,
    grant_document_permission,
    make_pdf_bytes,
    revoke_document_permission,
    tenant_user_with_attributes,
)
from app.tests.rag_helpers import (
    find_role_permission,
    patch_rag_runtime,
    upload_processed_pdf,
)


def _setup(client, db, monkeypatch, *, llm=None):
    _, recording = patch_rag_runtime(
        monkeypatch,
        llm=llm,
        rag_min_similarity=0.0,
        rag_max_context_tokens=1000,
    )
    organization, _, admin_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Conversation {uuid4()}",
        email=f"conversation-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )
    document = upload_processed_pdf(
        client,
        admin_token,
        pdf_bytes=make_pdf_bytes(
            ["Annual leave policy: employees receive 18 days of annual leave."]
        ),
        filename="leave.pdf",
        document_name="Leave Policy.pdf",
        department="HR",
    )
    user, user_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"conversation-user-{uuid4()}@example.com",
        username="conversationuser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )
    grant_document_permission(
        client,
        admin_token,
        document["document_id"],
        role_name="EMPLOYEE",
    )
    return organization, document, admin_token, user, user_token, recording


def _create(client, token, title="Leave questions"):
    response = client.post(
        "/rag/conversations",
        headers=auth_headers(token),
        json={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def test_conversation_lifecycle_and_message_persistence(client, db, monkeypatch):
    _, document, _, _, token, _ = _setup(client, db, monkeypatch)
    conversation_id = _create(client, token)

    answer = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "What is the annual leave policy?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["conversation_id"] == conversation_id
    assert body["message_id"]
    assert body["sources"]

    listing = client.get("/rag/conversations", headers=auth_headers(token))
    assert listing.status_code == 200
    entries = listing.json()["conversations"]
    assert len(entries) == 1
    assert entries[0]["conversation_id"] == conversation_id
    assert entries[0]["message_count"] == 2

    detail = client.get(
        f"/rag/conversations/{conversation_id}", headers=auth_headers(token)
    )
    assert detail.status_code == 200, detail.text
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    citations = messages[1]["citations"]
    assert citations
    assert citations[0]["document_id"] == document["document_id"]
    # Only references are persisted, never chunk text.
    assert "text" not in citations[0]

    renamed = client.patch(
        f"/rag/conversations/{conversation_id}",
        headers=auth_headers(token),
        json={"title": "Renamed leave questions"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed leave questions"

    history = client.get("/rag/history", headers=auth_headers(token))
    assert history.status_code == 200
    assert history.json()["persistent"] is True
    assert history.json()["items"][0]["question"] == "What is the annual leave policy?"

    deleted = client.delete(
        f"/rag/conversations/{conversation_id}", headers=auth_headers(token)
    )
    assert deleted.status_code == 204
    assert (
        client.get(
            f"/rag/conversations/{conversation_id}", headers=auth_headers(token)
        ).status_code
        == 404
    )
    assert (
        client.get("/rag/conversations", headers=auth_headers(token)).json()[
            "conversations"
        ]
        == []
    )


def test_clear_conversation_soft_deletes_messages(client, db, monkeypatch):
    _, _, _, _, token, _ = _setup(client, db, monkeypatch)
    conversation_id = _create(client, token)
    client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "What is the annual leave policy?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )

    cleared = client.post(
        f"/rag/conversations/{conversation_id}/clear",
        headers=auth_headers(token),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cleared"] == 2

    detail = client.get(
        f"/rag/conversations/{conversation_id}", headers=auth_headers(token)
    )
    assert detail.json()["messages"] == []


def test_conversations_are_invisible_to_other_users(client, db, monkeypatch):
    organization, _, admin_token, _, owner_token, _ = _setup(
        client, db, monkeypatch
    )
    conversation_id = _create(client, owner_token)
    client.post(
        "/rag/ask",
        headers=auth_headers(owner_token),
        json={
            "question": "What is the annual leave policy?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )

    _, other_token = tenant_user_with_attributes(
        client,
        admin_token,
        organization["tenant_id"],
        email=f"other-user-{uuid4()}@example.com",
        username="otheruser",
        role_names=["EMPLOYEE"],
        department="HR",
        clearance="3",
    )

    assert (
        client.get(
            f"/rag/conversations/{conversation_id}", headers=auth_headers(other_token)
        ).status_code
        == 404
    )
    assert (
        client.get("/rag/conversations", headers=auth_headers(other_token)).json()[
            "conversations"
        ]
        == []
    )


def test_revoked_permission_invalidates_previous_conversation_evidence(
    client, db, monkeypatch
):
    organization, document, admin_token, _, token, llm = _setup(
        client, db, monkeypatch
    )
    conversation_id = _create(client, token)

    first = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "What is the annual leave policy?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )
    assert first.status_code == 200
    assert first.json()["sources"]
    assert len(llm.calls) == 1

    permission = find_role_permission(
        db,
        tenant_id=organization["tenant_id"],
        document_id=document["document_id"],
        role_name="EMPLOYEE",
    )
    revoke_document_permission(
        client, admin_token, document["document_id"], permission.permission_id
    )

    followup = client.post(
        "/rag/ask",
        headers=auth_headers(token),
        json={
            "question": "How many days can I take?",
            "top_k": 10,
            "conversation_id": conversation_id,
        },
    )

    assert followup.status_code == 200, followup.text
    assert followup.json()["sources"] == []
    assert followup.json()["insufficient_evidence"] is True
    # History must not be treated as fresh evidence after revocation.
    assert len(llm.calls) == 1

    detail = client.get(
        f"/rag/conversations/{conversation_id}", headers=auth_headers(token)
    )
    assert detail.status_code == 200
    assistant_messages = [
        message for message in detail.json()["messages"] if message["role"] == "assistant"
    ]
    assert all(message["citations"] == [] for message in assistant_messages)


def test_conversation_with_other_tenant_is_not_found(client, db, monkeypatch):
    _, _, _, _, token, _ = _setup(client, db, monkeypatch)
    conversation_id = _create(client, token)

    _, _, other_token = admin_bundle_with_attributes(
        client,
        db,
        organization_name=f"Other tenant {uuid4()}",
        email=f"other-tenant-{uuid4()}@example.com",
        username="admin",
        department="HR",
        clearance="5",
    )

    assert (
        client.get(
            f"/rag/conversations/{conversation_id}",
            headers=auth_headers(other_token),
        ).status_code
        == 404
    )
    assert (
        client.get("/rag/conversations", headers=auth_headers(other_token)).json()[
            "conversations"
        ]
        == []
    )
