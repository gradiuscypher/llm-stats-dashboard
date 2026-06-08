"""API integration tests for log ingest and retrieval."""

_VALID_LOG = {
    "provider": "openai",
    "model": "gpt-4o",
    "conversation_id": "test-conv-001",
    "request": {"messages": [{"role": "user", "content": "What is 2+2?"}]},
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
    ingest_resp = auth_client.post(
        "/api/v1/logs", json=_VALID_LOG, headers={"x-api-key": write_key}
    )
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


def test_list_conversations_groups_calls(auth_client):
    """Ingest calls with the same conversation_id; assert aggregation is correct."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    # Two calls in one conversation, one call in another
    for _ in range(2):
        auth_client.post(
            "/api/v1/logs",
            json={**_VALID_LOG, "conversation_id": "conv-a"},
            headers={"x-api-key": write_key},
        )
    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "conv-b"},
        headers={"x-api-key": write_key},
    )

    resp = auth_client.get("/api/v1/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    convs = {c["conversation_id"]: c for c in data["conversations"]}

    # conv-a has 2 calls, conv-b has 1 call
    assert convs["conv-a"]["call_count"] == 2
    assert convs["conv-a"]["total_tokens"] == 30  # 15 + 15
    assert convs["conv-b"]["call_count"] == 1

    # models / providers should be deduped
    assert convs["conv-a"]["models"] == ["gpt-4o"]
    assert convs["conv-a"]["providers"] == ["openai"]


def test_list_conversations_filter_model(auth_client):
    """Filter by model — only conversations containing that model appear."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "conv-oai", "model": "gpt-4o"},
        headers={"x-api-key": write_key},
    )
    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "conv-claude", "model": "claude-3-opus"},
        headers={"x-api-key": write_key},
    )

    resp = auth_client.get("/api/v1/conversations?model=gpt-4o")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["conversations"][0]["conversation_id"] == "conv-oai"


def test_list_conversations_search(auth_client):
    """Substring search on conversation_id."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "session-abc"},
        headers={"x-api-key": write_key},
    )
    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "session-xyz"},
        headers={"x-api-key": write_key},
    )

    resp = auth_client.get("/api/v1/conversations?conversation_id=abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["conversations"][0]["conversation_id"] == "session-abc"


def test_list_conversations_excludes_null_conversation(auth_client):
    """A log with no conversation_id does not appear in the conversations list."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    auth_client.post(
        "/api/v1/logs",
        json={**{k: v for k, v in _VALID_LOG.items() if k != "conversation_id"}},
        headers={"x-api-key": write_key},
    )

    resp = auth_client.get("/api/v1/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["conversations"] == []


def test_list_conversations_pagination(auth_client):
    """Pagination over groups — limit/offset + total is the full group count."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    for i in range(5):
        auth_client.post(
            "/api/v1/logs",
            json={**_VALID_LOG, "conversation_id": f"conv-{i}"},
            headers={"x-api-key": write_key},
        )

    # Page 1: limit=2
    resp = auth_client.get("/api/v1/conversations?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["conversations"]) == 2

    # Page 2: offset=2, limit=2
    resp2 = auth_client.get("/api/v1/conversations?limit=2&offset=2")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 5
    assert len(data2["conversations"]) == 2

    # Page 3: offset=4, limit=2
    resp3 = auth_client.get("/api/v1/conversations?limit=2&offset=4")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["total"] == 5
    assert len(data3["conversations"]) == 1


def test_list_conversations_sort(auth_client):
    """Sort by total_tokens ascending orders correctly."""
    write_key = _create_api_key(auth_client, ["logs:write"])

    # conv-small has one call (15 tokens), conv-large has two calls (30 tokens)
    auth_client.post(
        "/api/v1/logs",
        json={**_VALID_LOG, "conversation_id": "conv-small"},
        headers={"x-api-key": write_key},
    )
    for _ in range(2):
        auth_client.post(
            "/api/v1/logs",
            json={**_VALID_LOG, "conversation_id": "conv-large"},
            headers={"x-api-key": write_key},
        )

    resp = auth_client.get("/api/v1/conversations?sort=total_tokens&order=asc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    names = [c["conversation_id"] for c in data["conversations"]]
    assert names == ["conv-small", "conv-large"]


# ---------------------------------------------------------------------------
# Transcript trailing‑reply tests
# ---------------------------------------------------------------------------


def _ingest(auth_client, write_key: str, **overrides) -> dict:
    """Ingest a log entry with defaults and return the parsed JSON."""
    payload = {**_VALID_LOG, **overrides}
    resp = auth_client.post("/api/v1/logs", json=payload, headers={"x-api-key": write_key})
    assert resp.status_code == 201, resp.json()
    return resp.json()


def test_transcript_single_call_ends_with_response(auth_client):
    """A single‑call conversation transcript ends with the assistant reply."""
    wk = _create_api_key(auth_client, ["logs:write"])
    _ingest(
        auth_client,
        wk,
        conversation_id="tc-trailing",
        request={
            "messages": [
                {"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hi"},
            ]
        },
        response={
            "message": {"role": "assistant", "content": "Hello there!"},
            "finish_reason": "stop",
        },
    )

    t = auth_client.get("/api/v1/conversations/tc-trailing/transcript").json()
    assert len(t["trunk"]) == 3  # system, user, synthetic-assistant
    last = t["trunk"][-1]
    assert last["role"] == "assistant"
    assert last["content"] == "Hello there!"
    # No divider should fire for the synthetic reply.
    assert last["introduced_by_entry_id"] is None


def test_transcript_multi_turn_ends_with_last_response(auth_client):
    """Two‑turn conversation: trunk ends with the second call's reply."""
    wk = _create_api_key(auth_client, ["logs:write"])

    # Turn 1
    _ingest(
        auth_client,
        wk,
        conversation_id="tc-two-turn",
        request={"messages": [{"role": "user", "content": "Q1"}]},
        response={"message": {"role": "assistant", "content": "A1"}, "finish_reason": "stop"},
    )
    # Turn 2 — the request includes Turn 1's reply as history.
    _ingest(
        auth_client,
        wk,
        conversation_id="tc-two-turn",
        request={
            "messages": [
                {"role": "user", "content": "Q1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "Q2"},
            ]
        },
        response={"message": {"role": "assistant", "content": "A2"}, "finish_reason": "stop"},
    )

    t = auth_client.get("/api/v1/conversations/tc-two-turn/transcript").json()
    # Trunk: user(Q1) → assistant(A1) → user(Q2) → synthetic(A2)
    assert len(t["trunk"]) == 4
    last = t["trunk"][-1]
    assert last["role"] == "assistant"
    assert last["content"] == "A2"
    # The earlier assistant reply is also present (from request history of turn 2).
    roles = [m["role"] for m in t["trunk"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_transcript_error_entry_skips_trailing_reply(auth_client):
    """Error entries have empty response content → no trailing reply."""
    wk = _create_api_key(auth_client, ["logs:write"])
    _ingest(
        auth_client,
        wk,
        conversation_id="tc-error",
        request={"messages": [{"role": "user", "content": "crash"}]},
        response={"message": {"role": "assistant", "content": ""}, "finish_reason": None},
        status="error",
        error="timeout",
    )

    t = auth_client.get("/api/v1/conversations/tc-error/transcript").json()
    # Only the user message; no synthetic assistant appended.
    assert len(t["trunk"]) == 1
    assert t["trunk"][0]["role"] == "user"


def test_transcript_empty_response_content_skipped(auth_client):
    """Response with message.content = "" → no trailing reply."""
    wk = _create_api_key(auth_client, ["logs:write"])
    _ingest(
        auth_client,
        wk,
        conversation_id="tc-empty",
        request={"messages": [{"role": "user", "content": "test"}]},
        # Content is an empty string (e.g. error / tool‑only response).
        response={"message": {"role": "assistant", "content": ""}, "finish_reason": "tool_calls"},
    )

    t = auth_client.get("/api/v1/conversations/tc-empty/transcript").json()
    assert len(t["trunk"]) == 1
    assert t["trunk"][0]["role"] == "user"
