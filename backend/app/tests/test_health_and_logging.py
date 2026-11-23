"""Phase 9: liveness, readiness and structured request fields."""

def test_liveness_does_not_require_the_database(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "probe": "live"}


def test_readiness_reports_dependencies_without_secrets(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["database"] == "ready"
    assert body["components"]["pgvector"] == "ready"
    assert body["components"]["full_text_search"] == "ready"
    assert "jwt" not in str(body).lower()
    assert "password" not in str(body).lower()


def test_request_log_contains_no_content(client, monkeypatch):
    records: list[str] = []
    monkeypatch.setattr(
        "app.main.logger.info",
        lambda message, *args: records.append(message % args),
    )
    response = client.get(
        "/health", params={"question": "secret question text"},
        headers={"X-Request-ID": "not-a-uuid"},
    )
    assert response.status_code == 200
    requests = [message for message in records if message.startswith("request ")]
    assert requests
    assert "question=" not in requests[0]
    assert "request_id=" in requests[0]
    assert "duration_ms=" in requests[0]
