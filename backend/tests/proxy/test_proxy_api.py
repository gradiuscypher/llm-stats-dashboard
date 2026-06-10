"""API integration tests for proxy routes — OpenRouter mocked via respx."""

import json

import httpx
import respx

from app.config import settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_api_key(auth_client, scopes: list[str]) -> str:
    """Create an API key via the API and return the raw key string."""
    csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]
    resp = auth_client.post(
        "/api/v1/api-keys",
        json={"name": "proxy-test-key", "scopes": scopes},
        headers={"x-csrf-token": csrf},
    )
    assert resp.status_code == 201, f"Failed to create key: {resp.json()}"
    return resp.json()["raw_key"]


def _bearer_headers(raw_key: str) -> dict:
    """Build headers using Authorization: Bearer (OpenAI SDK style)."""
    return {"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"}


def _x_api_key_headers(raw_key: str) -> dict:
    """Build headers using X-API-Key (existing client style)."""
    return {"X-API-Key": raw_key, "Content-Type": "application/json"}


def _enable_proxy_config(monkeypatch):
    """Set up OpenRouter config for proxy tests."""
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestProxyHealth:
    def test_health_without_key(self, client, monkeypatch):
        """Health endpoint returns degraded when no API key is configured."""
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        resp = client.get("/api/v1/proxy/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert any("OPENROUTER_API_KEY" in p for p in body["problems"])

    @respx.mock
    def test_health_with_reachable_upstream(self, client, monkeypatch):
        """Health returns ok when upstream is reachable."""
        _enable_proxy_config(monkeypatch)
        respx.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        resp = client.get("/api/v1/proxy/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["upstream_reachable"] is True


# ---------------------------------------------------------------------------
# Models passthrough
# ---------------------------------------------------------------------------


class TestProxyModels:
    @respx.mock
    def test_models_passthrough(self, client, monkeypatch):
        _enable_proxy_config(monkeypatch)
        mock_data = {"data": [{"id": "gpt-4o", "name": "GPT-4o"}]}
        respx.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json=mock_data)
        )
        resp = client.get("/api/v1/models")
        assert resp.status_code == 200
        assert resp.json() == mock_data

    def test_models_no_key(self, client, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        resp = client.get("/api/v1/models")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Chat completions — auth
# ---------------------------------------------------------------------------


class TestProxyChatAuth:
    def test_no_auth(self, client):
        resp = client.post("/api/v1/chat/completions", json={"model": "gpt-4o"})
        assert resp.status_code == 401

    def test_bad_key(self, client):
        resp = client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o"},
            headers=_bearer_headers("lsd_bad_key"),
        )
        assert resp.status_code == 401

    def test_missing_proxy_scope(self, auth_client):
        """Key with logs:write but not proxy:use gets 403."""
        raw_key = _create_api_key(auth_client, ["logs:write"])
        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 403

    @respx.mock
    def test_bearer_header_accepted(self, auth_client, monkeypatch):
        """Bearer auth header is accepted for proxy routes."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Hello!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )
        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 200

    @respx.mock
    def test_x_api_key_header_accepted(self, auth_client, monkeypatch):
        """X-API-Key header is also accepted."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-123",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Hello!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )
        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_x_api_key_headers(raw_key),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Chat completions — non-stream
# ---------------------------------------------------------------------------


class TestProxyChatNonStream:
    @respx.mock
    def test_successful_proxy(self, auth_client, monkeypatch):
        """Non-streaming chat completion proxies correctly."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        upstream_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello, world!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 0.0001,
            },
        }
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=upstream_response)
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "Hello, world!"
        assert body["usage"]["total_tokens"] == 15

    @respx.mock
    def test_upstream_4xx_surfaced(self, auth_client, monkeypatch):
        """Upstream 4xx errors are surfaced to the client."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(429, json={"error": "rate limited"})
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 429

    @respx.mock
    def test_upstream_5xx_surfaced(self, auth_client, monkeypatch):
        """Upstream 5xx errors are surfaced."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(502, json={"error": "bad gateway"})
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 502

    @respx.mock
    def test_body_size_limit(self, auth_client, monkeypatch):
        """Oversized bodies are rejected."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        # Create a body that exceeds 1MB
        large_message = "x" * (1024 * 1024 + 1)
        resp = auth_client.post(
            "/api/v1/chat/completions",
            data=json.dumps(
                {"model": "gpt-4o", "messages": [{"role": "user", "content": large_message}]}
            ),
            headers={**_bearer_headers(raw_key), "Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    @respx.mock
    def test_proxy_persists_reasoning(self, auth_client, monkeypatch):
        """Non-streaming call with reasoning should return reasoning in response.

        The proxy passes through the full upstream response including reasoning
        fields and completion_tokens_details. Persistence is verified via unit
        test (TestMapToLogEntry.test_maps_reasoning_in_response).
        """
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        upstream_response = {
            "id": "chatcmpl-reasoning-1",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "The answer is 4.",
                        "reasoning": "Let me think: 2+2=4. That's correct.",
                        "reasoning_details": [
                            {"type": "reasoning.text", "text": "2+2=4"},
                            {"type": "reasoning.encrypted", "text": "enc=="},
                        ],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 12,
                "total_tokens": 22,
                "completion_tokens_details": {"reasoning_tokens": 8},
            },
        }
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=upstream_response)
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "What is 2+2?"}],
            },
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 200
        body = resp.json()
        msg = body["choices"][0]["message"]
        assert msg["reasoning"] == "Let me think: 2+2=4. That's correct."
        assert len(msg["reasoning_details"]) == 2
        assert msg["reasoning_details"][1]["type"] == "reasoning.encrypted"
        details = body["usage"].get("completion_tokens_details") or {}
        assert details.get("reasoning_tokens") == 8


# ---------------------------------------------------------------------------
# Per-conversation plugin override — continuing turn
# ---------------------------------------------------------------------------


class TestPerConversationOverrideContinuingTurn:
    """Per-conversation plugin overrides must apply to continuing turns
    even when the override was created after the first turn.

    Since word_count is now a request-only transform, we verify the
    transform ran by inspecting the request body sent to the upstream
    (captured via respx side_effect), not the verbatim response.
    """

    @respx.mock
    def test_override_applies_to_continuing_turn(self, auth_client, monkeypatch):
        """Global word_count=OFF, per-conversation ON → turn 2 request gets marker."""
        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Disable word_count globally.
        resp = auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200

        # Capture the request body sent to upstream
        captured_upstream: list[dict] = []

        def _capture_request(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json=upstream_turn1)

        # --- Turn 1: global OFF → no marker in request ---
        upstream_turn1 = {
            "id": "chatcmpl-turn1",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello, human!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        route = respx.post("https://openrouter.ai/api/v1/chat/completions")
        route.side_effect = _capture_request

        resp1 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Hello"},
                ],
            },
            headers=_bearer_headers(raw_key),
        )
        assert resp1.status_code == 200
        # Verify request body sent upstream does NOT have word_count marker
        assert len(captured_upstream) == 1
        last_user = captured_upstream[0]["messages"][-1]
        assert "[word_count:" not in str(last_user.get("content", "")), (
            "Turn 1 with global OFF must NOT have word_count marker in request"
        )

        # --- Set per-conversation override ON ---
        conv_id2 = "or-conv-override-explicit"
        captured_upstream.clear()

        resp1b = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Hello"},
                ],
            },
            headers={
                **_bearer_headers(raw_key),
                "x-conversation-id": conv_id2,
            },
        )
        assert resp1b.status_code == 200

        # Create per-conversation override.
        resp_override = auth_client.put(
            f"/api/v1/conversations/{conv_id2}/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )
        assert resp_override.status_code == 200

        # --- Turn 2: continuing → per-conversation ON → marker in request ---
        captured_upstream.clear()
        upstream_turn2 = {
            "id": "chatcmpl-turn2",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "I am doing well!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 6,
                "total_tokens": 26,
            },
        }
        def _capture_request2(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json=upstream_turn2)

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture_request2
        )

        resp2 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hello, human!"},
                    {"role": "user", "content": "How are you?"},
                ],
            },
            headers={
                **_bearer_headers(raw_key),
                "x-conversation-id": conv_id2,
            },
        )
        assert resp2.status_code == 200
        # Verify request body sent upstream HAS word_count marker
        assert len(captured_upstream) == 1
        last_user = captured_upstream[0]["messages"][-1]
        assert "[word_count:" in str(last_user.get("content", "")), (
            "Turn 2 with per-conversation ON must have word_count marker in request"
        )
        assert "[word_count: 3]" in str(last_user.get("content", ""))

    @respx.mock
    def test_no_marker_without_override(self, auth_client, monkeypatch):
        """Global OFF + no override → no marker on any turn's request."""
        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Disable word_count globally.
        resp = auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200

        conv_id = "or-conv-no-override"
        upstream = {
            "id": "chatcmpl-ctrl",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Nope"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        captured_upstream: list[dict] = []

        def _capture(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json=upstream)

        # Turn 1 — global OFF
        route = respx.post("https://openrouter.ai/api/v1/chat/completions")
        route.side_effect = _capture

        resp1 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are a bot."},
                    {"role": "user", "content": "Hi"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp1.status_code == 200
        last_user = captured_upstream[0]["messages"][-1]
        assert "[word_count:" not in str(last_user.get("content", ""))

        # Turn 2 — continuing, still no override → still no marker
        captured_upstream.clear()
        upstream["id"] = "chatcmpl-ctrl2"
        upstream["choices"][0]["message"]["content"] = "Still no"

        resp2 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are a bot."},
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Nope"},
                    {"role": "user", "content": "Are you sure?"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp2.status_code == 200
        last_user = captured_upstream[0]["messages"][-1]
        assert "[word_count:" not in str(last_user.get("content", ""))


# ---------------------------------------------------------------------------
# Plugin content isolation — snapshots prevent marker leakage into interned
# message content even when word_count is enabled.
# ---------------------------------------------------------------------------


class TestPluginContentIsolation:
    """Plugin transforms modify request messages before forwarding.

    The interceptor runs transforms request-side only — the response
    is verbatim from upstream.  This test class verifies the transform
    worked by inspecting the request body sent to upstream via respx.
    """

    @respx.mock
    def test_word_count_transform_modifies_request(self, auth_client, monkeypatch):
        """With word_count ON, the upstream request has the marker."""
        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Enable word_count globally.
        resp = auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )
        assert resp.status_code == 200

        captured_upstream: list[dict] = []

        def _capture(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json={
                "id": "chatcmpl-iso1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "The answer is 42.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 7,
                    "total_tokens": 22,
                },
            })

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "What is the meaning of life?"}
                ],
            },
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 200
        # Verify request body sent upstream has the word_count marker
        assert len(captured_upstream) == 1
        user_content = captured_upstream[0]["messages"][0]["content"]
        assert "word_count:" in user_content, (
            "Upstream request must have word_count marker"
        )

    @respx.mock
    def test_word_count_disabled_does_not_modify(self, auth_client, monkeypatch):
        """With word_count OFF, the upstream request has no marker."""
        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging")
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        captured_upstream: list[dict] = []

        def _capture(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json={
                "id": "chatcmpl-iso2",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            })

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers=_bearer_headers(raw_key),
        )
        assert resp.status_code == 200
        user_content = captured_upstream[0]["messages"][0]["content"]
        assert "word_count:" not in user_content

    @respx.mock
    def test_disabled_then_reenabled_request_modified(self, auth_client, monkeypatch):
        """Plugin toggle changes the request sent upstream."""
        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        captured_upstream: list[dict] = []

        def _capture(request):
            try:
                captured_upstream.append(json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json={
                "id": "cmpl-1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        # Turn 1: word_count ON
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )
        conv_id = "or-history-stable"
        resp1 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp1.status_code == 200
        assert "word_count:" in str(captured_upstream[-1]["messages"][0]["content"])

        # Turn 2: word_count OFF
        captured_upstream.clear()
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )
        resp2 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello!"},
                    {"role": "user", "content": "What's up?"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp2.status_code == 200
        assert "word_count:" not in str(captured_upstream[-1]["messages"][-1]["content"])

        # Turn 3: word_count back ON
        captured_upstream.clear()
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )
        resp3 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Hi"},
                    {"role": "assistant", "content": "Hello!"},
                    {"role": "user", "content": "What's up?"},
                    {"role": "assistant", "content": "How can I help?"},
                    {"role": "user", "content": "Tell me about yourself"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp3.status_code == 200
        assert "word_count:" in str(captured_upstream[-1]["messages"][-1]["content"])


# ---------------------------------------------------------------------------
# Canonical = original (A1) — interned messages are pre-transform originals
# ---------------------------------------------------------------------------


class TestCanonicalIsOriginal:
    """With word_count enabled, interned messages are the original (no marker)
    while diffs record the modified content sent upstream."""

    @respx.mock
    def test_interned_messages_are_original(self, auth_client, monkeypatch, pg_engine):
        """Proxy call with word_count → logged messages are original, diff has marker."""
        # Override the module-level engine in logging_sink so persist_log
        # writes to the test DB (it uses a direct Session(engine) not DI).
        import app.proxy.logging_sink as _sink
        monkeypatch.setattr(_sink, "engine", pg_engine)

        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Enable word_count globally.
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )

        conv_id = "or-canonical-orig"
        upstream = {
            "id": "chatcmpl-canon",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi back!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }

        captured_upstream: list[dict] = []

        def _capture(request):
            import json as _json
            try:
                captured_upstream.append(_json.loads(request.content))
            except Exception:
                pass
            return httpx.Response(200, json=upstream)

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Hello world"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp.status_code == 200

        # (a) Upstream request has the word_count marker.
        assert len(captured_upstream) == 1
        user_content = captured_upstream[0]["messages"][0]["content"]
        assert "[word_count:" in user_content, "Upstream must have word_count marker"

        # (b) Logged entry request messages are the original (no marker).
        conv_resp = auth_client.get(
            f"/api/v1/conversations/{conv_id}",
        )
        assert conv_resp.status_code == 200
        conv_data = conv_resp.json()
        assert len(conv_data["entries"]) == 1
        entry = conv_data["entries"][0]
        req_msg = entry["request"]["messages"][0]
        assert "[word_count:" not in str(req_msg.get("content", "")), (
            "Logged request must NOT contain word_count marker"
        )
        assert "Hello world" in str(req_msg.get("content", ""))

        # (c) Message diff exists with marker in final_content.
        diffs = entry.get("request_diffs", [])
        assert len(diffs) == 1, f"Expected 1 diff, got {len(diffs)}"
        diff = diffs[0]
        assert diff["change_kind"] == "modified"
        assert "[word_count:" in str(diff.get("final_content", {}))
        assert "word_count" in diff["modified_by"]


class TestParentLinkageStableAcrossToggle:
    """Parent linkage survives plugin toggles because canonical = original."""

    @respx.mock
    def test_parent_links_stable(self, auth_client, monkeypatch, pg_engine):
        """Two turns, word_count ON then OFF → turn 2 parent is turn 1."""
        import app.proxy.logging_sink as _sink
        monkeypatch.setattr(_sink, "engine", pg_engine)

        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Enable word_count globally.
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )

        conv_id = "or-parent-stable"

        def _make_response(msg_id, content):
            return httpx.Response(200, json={
                "id": msg_id,
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            })

        # Turn 1: word_count ON
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_make_response("turn1", "Hello from turn 1")
        )
        resp1 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp1.status_code == 200

        # Disable word_count globally.
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )

        # Turn 2: word_count OFF
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_make_response("turn2", "Hello from turn 2")
        )
        resp2 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hello from turn 1"},
                    {"role": "user", "content": "How are you?"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp2.status_code == 200

        # Fetch entries via conversation endpoint — verify parent linkage.
        conv_resp = auth_client.get(f"/api/v1/conversations/{conv_id}")
        assert conv_resp.status_code == 200
        entries = conv_resp.json()["entries"]
        assert len(entries) == 2

        # Turn 2 should have parent_entry_id set (via DB or API — check transcript).
        transcript_resp = auth_client.get(
            f"/api/v1/conversations/{conv_id}/transcript"
        )
        assert transcript_resp.status_code == 200
        tdata = transcript_resp.json()
        # With parent linkage working, the transcript should NOT be branched.
        assert tdata["is_branched"] is False, (
            "Transcript should NOT be branched — parent linkage must be stable across toggle"
        )
        # Both turns should be in the trunk (not in branches).
        assert len(tdata["trunk"]) >= 3  # messages from both turns


class TestTranscriptDiffAttribution:
    """Transcript API returns modified_content and proper diff attribution."""

    @respx.mock
    def test_transcript_has_modified_content(self, auth_client, monkeypatch, pg_engine):
        """Proxy call with word_count → transcript message has modified_content."""
        import app.proxy.logging_sink as _sink
        monkeypatch.setattr(_sink, "engine", pg_engine)

        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )

        conv_id = "or-transcript-diff"
        upstream = {
            "id": "chatcmpl-tdiff",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=upstream)
        )

        auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello world"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )

        transcript_resp = auth_client.get(
            f"/api/v1/conversations/{conv_id}/transcript"
        )
        assert transcript_resp.status_code == 200
        tdata = transcript_resp.json()

        # Find the user message in the trunk.
        user_msgs = [m for m in tdata["trunk"] if m["role"] == "user"]
        assert len(user_msgs) == 1
        user_msg = user_msgs[0]

        # Should have modified_by set.
        assert user_msg["modified_by"] == ["word_count"], (
            f"Expected ['word_count'], got {user_msg['modified_by']}"
        )

        # Should have original_content (the original).
        assert user_msg["original_content"] is not None
        assert "Hello world" in str(user_msg["original_content"])

        # Should have modified_content (what was sent upstream with the marker).
        assert user_msg["modified_content"] is not None, (
            "modified_content must be populated"
        )
        assert "[word_count:" in str(user_msg["modified_content"]), (
            "modified_content must contain the word_count marker"
        )

        # The canonical content should NOT have the marker.
        assert "[word_count:" not in str(user_msg["content"]), (
            "Canonical content must NOT contain word_count marker"
        )


class TestDisableStopsNewModsHistoryPersists:
    """Disabling a plugin stops new modifications but preserves old diffs."""

    @respx.mock
    def test_disable_preserves_history(self, auth_client, monkeypatch, pg_engine):
        """Turn 1 with word_count ON, turn 2 OFF → turn 1 diff persists, turn 2 clean."""
        import app.proxy.logging_sink as _sink
        monkeypatch.setattr(_sink, "engine", pg_engine)

        _enable_proxy_config(monkeypatch)
        monkeypatch.setattr(settings, "proxy_plugins", "logging,word_count")
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Enable word_count.
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": True},
            headers={"x-csrf-token": csrf},
        )

        conv_id = "or-disable-persists"

        def _make_response(msg_id, content):
            return httpx.Response(200, json={
                "id": msg_id,
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            })

        # Turn 1: word_count ON
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_make_response("t1", "Turn 1 reply")
        )
        resp1 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "First message"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp1.status_code == 200

        # Disable word_count.
        auth_client.put(
            "/api/v1/plugins/word_count",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )

        # Turn 2: word_count OFF
        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            return_value=_make_response("t2", "Turn 2 reply")
        )
        resp2 = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": "First message"},
                    {"role": "assistant", "content": "Turn 1 reply"},
                    {"role": "user", "content": "Second message"},
                ],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp2.status_code == 200

        # Verify via transcript.
        transcript_resp = auth_client.get(
            f"/api/v1/conversations/{conv_id}/transcript"
        )
        assert transcript_resp.status_code == 200
        tdata = transcript_resp.json()

        # Not branched.
        assert tdata["is_branched"] is False

        # Find first user message — should have modified_by and diff.
        user_msgs = [m for m in tdata["trunk"] if m["role"] == "user"]
        assert len(user_msgs) == 2, f"Expected 2 user messages, got {len(user_msgs)}"

        first_user = user_msgs[0]
        assert first_user["modified_by"] == ["word_count"], (
            f"First user msg should be modified by word_count, got {first_user['modified_by']}"
        )
        assert first_user["modified_content"] is not None

        second_user = user_msgs[1]
        assert second_user["modified_by"] == [], (
            f"Second user msg should NOT be modified, got {second_user['modified_by']}"
        )
        assert second_user["modified_content"] is None


# ---------------------------------------------------------------------------
# Session tracking — session_id injection
# ---------------------------------------------------------------------------


class TestSessionTracking:
    """session_tracking plugin injects the conversation_id as session_id into
    the request body sent upstream (OpenRouter) for session grouping."""

    @respx.mock
    def test_session_id_injected_by_default(self, auth_client, monkeypatch):
        """Default (session_tracking enabled) → forwarded body has session_id."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])

        conv_id = "or-session-default"
        captured_upstream: list[dict] = []

        def _capture(request):
            import contextlib
            import json as _json
            with contextlib.suppress(Exception):
                captured_upstream.append(_json.loads(request.content))
            return httpx.Response(200, json={
                "id": "chatcmpl-sess1",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            })

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp.status_code == 200
        assert len(captured_upstream) == 1
        forwarded = captured_upstream[0]
        assert "session_id" in forwarded, (
            "Forwarded body must include session_id when session_tracking is enabled"
        )
        assert forwarded["session_id"] == conv_id, (
            f"session_id mismatch: expected {conv_id}, got {forwarded['session_id']}"
        )

    @respx.mock
    def test_session_id_absent_when_disabled(self, auth_client, monkeypatch):
        """Disabling session_tracking globally → no session_id in forwarded body."""
        _enable_proxy_config(monkeypatch)
        raw_key = _create_api_key(auth_client, ["proxy:use"])
        csrf = auth_client.get("/api/v1/auth/csrf").json()["csrf_token"]

        # Disable session_tracking globally.
        resp_toggle = auth_client.put(
            "/api/v1/plugins/session_tracking",
            json={"enabled": False},
            headers={"x-csrf-token": csrf},
        )
        assert resp_toggle.status_code == 200

        conv_id = "or-session-disabled"
        captured_upstream: list[dict] = []

        def _capture(request):
            import contextlib
            import json as _json
            with contextlib.suppress(Exception):
                captured_upstream.append(_json.loads(request.content))
            return httpx.Response(200, json={
                "id": "chatcmpl-sess2",
                "object": "chat.completion",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Nope"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })

        respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
            side_effect=_capture
        )

        resp = auth_client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={**_bearer_headers(raw_key), "x-conversation-id": conv_id},
        )
        assert resp.status_code == 200
        assert len(captured_upstream) == 1
        forwarded = captured_upstream[0]
        assert "session_id" not in forwarded, (
            "Forwarded body must NOT include session_id when session_tracking is disabled"
        )
