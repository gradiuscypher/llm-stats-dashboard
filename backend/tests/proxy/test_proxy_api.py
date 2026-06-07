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
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })
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
            return_value=httpx.Response(200, json={
                "id": "chatcmpl-123",
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })
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
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello, world!"},
                "finish_reason": "stop",
            }],
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
            data=json.dumps({"model": "gpt-4o", "messages": [{"role": "user", "content": large_message}]}),
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
            "choices": [{
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
            }],
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
