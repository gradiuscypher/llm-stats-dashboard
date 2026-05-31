"""API integration tests for log ingest and retrieval."""



_VALID_LOG = {
    "provider": "openai",
    "model": "gpt-4o",
    "conversation_id": "test-conv-001",
    "request": {
        "messages": [{"role": "user", "content": "What is 2+2?"}]
    },
    "response": {
        "message": {"role": "assistant", "content": "4"},
        "finish_reason": "stop",
    },
    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    "status": "ok",
}


def _create_api_key(auth_client, scopes: list[str]) -> str:
    csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    resp = auth_client.post(
        "/api/v1/api-keys",
        json={"name": "ingest-key", "scopes": scopes},
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code == 201
    return resp.json()["raw_key"]


def test_ingest_requires_api_key(client):
    resp = client.post("/api/v1/logs", json=_VALID_LOG)
    assert resp.status_code == 401


def test_ingest_requires_write_scope(auth_client):
    read_key = _create_api_key(auth_client, ["logs:read"])
    resp = auth_client.post(
        "/api/v1/logs",
        json=_VALID_LOG,
        headers={"x-api-key": read_key},
    )
    assert resp.status_code == 403


def test_ingest_success(auth_client):
    write_key = _create_api_key(auth_client, ["logs:write"])
    resp = auth_client.post(
        "/api/v1/logs",
        json=_VALID_LOG,
        headers={"x-api-key": write_key},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["provider"] == "openai"
    assert data["model"] == "gpt-4o"
    assert data["total_tokens"] == 15
    return data


def test_list_logs(auth_client):
    write_key = _create_api_key(auth_client, ["logs:write"])
    # Ingest within this test so it's independent
    ingest_resp = auth_client.post("/api/v1/logs", json=_VALID_LOG, headers={"x-api-key": write_key})
    assert ingest_resp.status_code == 201

    resp = auth_client.get("/api/v1/logs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_get_log_detail(auth_client):
    write_key = _create_api_key(auth_client, ["logs:write"])
    created = auth_client.post(
        "/api/v1/logs", json=_VALID_LOG, headers={"x-api-key": write_key}
    ).json()

    resp = auth_client.get(f"/api/v1/logs/{created['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert "request" in data
    assert data["request"]["messages"][0]["content"] == "What is 2+2?"


def test_get_conversation(auth_client):
    write_key = _create_api_key(auth_client, ["logs:write"])
    for _ in range(2):
        auth_client.post("/api/v1/logs", json=_VALID_LOG, headers={"x-api-key": write_key})

    resp = auth_client.get("/api/v1/conversations/test-conv-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == "test-conv-001"
    assert len(data["entries"]) >= 2


def test_stats_summary(auth_client):
    resp = auth_client.get("/api/v1/stats/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_calls" in data
    assert "by_model" in data


def test_ingest_invalid_payload(auth_client):
    write_key = _create_api_key(auth_client, ["logs:write"])
    resp = auth_client.post(
        "/api/v1/logs",
        json={"provider": "openai"},  # missing required fields
        headers={"x-api-key": write_key},
    )
    assert resp.status_code == 422
