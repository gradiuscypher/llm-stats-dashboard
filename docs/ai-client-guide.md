# AI Client Implementation Guide

> **Hand this document to an AI assistant to have it generate a complete
> LLM Stats Dashboard client.** This page is self-contained and covers
> everything needed to build a working client from scratch.
>
> **No authentication is required to fetch this documentation.**
> All `/api/v1/docs-md/*` endpoints are publicly accessible — you only need
> an API key once you start sending log entries.

---

## Overview

The LLM Stats Dashboard API receives one log entry per LLM call and stores
it for debugging and cost analysis. Your job as a client is:

1. Intercept each LLM API call your application makes
2. Map the request + response to the canonical schema (defined below)
3. POST the payload to `POST /api/v1/logs` with your API key

That's the entire integration. The rest of this guide covers auth, the schema,
error handling, and a complete working example.

---

## Base URL

```
http://localhost:8000
```

All API endpoints are under `/api/v1`.

---

## Step 1 — Register an account

```http
POST /api/v1/users
Content-Type: application/json

{
  "username": "mybot",
  "password": "a-secure-password-min-8-chars"
}
```

Response: `201 Created` with `{ "id": "uuid", "username": "mybot", ... }`

---

## Step 2 — Log in and get a session cookie

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "mybot",
  "password": "a-secure-password-min-8-chars"
}
```

Response sets cookies `lsd_session` (httpOnly) and `lsd_csrf` (readable).
You'll need these cookies for key management. **You do NOT need cookies for
log ingestion** — that uses an API key instead.

---

## Step 3 — Create an API key

Use the session cookies from Step 2.
Read the CSRF token from the `lsd_csrf` cookie and send it as `X-CSRF-Token`.

```http
POST /api/v1/api-keys
Content-Type: application/json
Cookie: lsd_session=<value>
X-CSRF-Token: <value from lsd_csrf cookie>

{
  "name": "my-client-v1",
  "scopes": ["logs:write"]
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "name": "my-client-v1",
  "prefix": "lsd_aB3xYz12",
  "scopes": ["logs:write"],
  "raw_key": "lsd_aB3xYz12_Kq7mNpRsT...",
  "created_at": "..."
}
```

> ⚠️ Save `raw_key` immediately. It is **never returned again**.

Store this key in an environment variable: `LSD_API_KEY=lsd_aB3xYz12_Kq7mN...`

---

## Step 4 — Send log entries

Use the API key for all log ingestion. No cookies needed.

```http
POST /api/v1/logs
Content-Type: application/json
X-API-Key: lsd_aB3xYz12_Kq7mNpRsT...

{ ...canonical log payload... }
```

Response: `201 Created` with a log summary object.

---

## Canonical log payload (complete schema)

This is what you POST to `/api/v1/logs`.

```jsonc
{
  // ── Required ──────────────────────────────────────
  "provider": "openai",          // string: provider slug
  "model": "gpt-4o",             // string: model id
  "request": {
    "messages": [
      // All messages in the context window (system + history + new user msg)
      { "role": "system",    "content": "You are a helpful assistant." },
      { "role": "user",      "content": "What is 2+2?" }
    ],
    "params": { "temperature": 0.7 }   // optional: model parameters
  },
  "response": {
    "message": { "role": "assistant", "content": "4" },
    "finish_reason": "stop"            // "stop" | "length" | "tool_calls" | ...
  },

  // ── Strongly recommended ──────────────────────────
  "conversation_id": "session-abc",  // string: groups calls into a session
                                     // Use any stable ID. Required for session views.
  "usage": {
    "prompt_tokens": 22,
    "completion_tokens": 3,
    "total_tokens": 25
  },
  "status": "ok",                    // "ok" | "error"

  // ── Optional ──────────────────────────────────────
  "client_timestamp": "2025-06-01T12:00:00.000Z",  // ISO-8601, when the call happened
  "latency_ms": 312,                 // integer: end-to-end latency in ms
  "tool_calls": [],                  // ToolCall[]: see below
  "cost": {                          // if omitted, server computes from pricing table
    "total": 0.000185,
    "currency": "USD"
  },
  "error": null,                     // string: required if status="error"
  "metadata": {                      // arbitrary passthrough; stored as-is
    "env": "production",
    "user_id": "u_alice_42"
  }
}
```

### conversation_id — important notes

- **You define it.** Use any string: a UUID, session ID, or slug.
- **It groups calls.** All calls with the same `conversation_id` form one
  conversation in the dashboard — essential for debugging multi-turn sessions.
- **Persist it.** For a multi-turn chat, generate one ID at session start and
  reuse it for every subsequent call in that session.
- **Optional but recommended.** Without it, calls are standalone and cannot
  be viewed as a conversation.

### Tool call object

```json
{
  "id": "call_abc123",
  "name": "get_weather",
  "arguments": { "location": "NYC", "unit": "celsius" },
  "result": { "temperature": 22, "description": "Sunny" }
}
```

### Error call example

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "conversation_id": "session-abc",
  "request": { "messages": [{ "role": "user", "content": "Hello" }] },
  "response": { "message": { "role": "assistant", "content": "" } },
  "status": "error",
  "error": "RateLimitError: 429 from OpenAI"
}
```

---

## Provider mapping reference

### OpenAI (openai Python SDK)

```python
import time
import httpx
from openai import OpenAI

openai_client = OpenAI()
lsd_client = httpx.Client(
    base_url="http://localhost:8000/api/v1",
    headers={"X-API-Key": "lsd_aB3xYz12_yourKey"},
)

def chat_with_logging(messages, model="gpt-4o", conversation_id=None, **kwargs):
    t0 = time.time()
    try:
        response = openai_client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
        latency_ms = int((time.time() - t0) * 1000)
        lsd_client.post("/logs", json={
            "provider": "openai",
            "model": model,
            "conversation_id": conversation_id,
            "request": {"messages": [m if isinstance(m, dict) else m.model_dump() for m in messages]},
            "response": {
                "message": {
                    "role": response.choices[0].message.role,
                    "content": response.choices[0].message.content or "",
                },
                "finish_reason": response.choices[0].finish_reason,
            },
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            "latency_ms": latency_ms,
            "status": "ok",
        })
        return response
    except Exception as e:
        lsd_client.post("/logs", json={
            "provider": "openai",
            "model": model,
            "conversation_id": conversation_id,
            "request": {"messages": [m if isinstance(m, dict) else m.model_dump() for m in messages]},
            "response": {"message": {"role": "assistant", "content": ""}},
            "status": "error",
            "error": str(e),
        })
        raise
```

### Anthropic (anthropic Python SDK)

```python
import anthropic

def claude_with_logging(messages, model="claude-3-5-sonnet-20241022", conversation_id=None):
    t0 = time.time()
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(model=model, max_tokens=1024, messages=messages)
        latency_ms = int((time.time() - t0) * 1000)
        lsd_client.post("/logs", json={
            "provider": "anthropic",
            "model": model,
            "conversation_id": conversation_id,
            "request": {"messages": messages},
            "response": {
                "message": {
                    "role": "assistant",
                    "content": response.content[0].text if response.content else "",
                },
                "finish_reason": response.stop_reason,
            },
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            "latency_ms": latency_ms,
            "status": "ok",
        })
        return response
    except Exception as e:
        # log error similarly ...
        raise
```

---

## Error handling and retries

The API returns standard HTTP status codes:

| Status | Meaning | Retry? |
|--------|---------|--------|
| `201` | Created successfully | — |
| `401` | Invalid/missing API key | No — fix the key |
| `403` | Key lacks required scope | No — create a new key with `logs:write` |
| `413` | Payload too large (>1MB) | No — truncate messages |
| `422` | Schema validation error | No — fix the payload |
| `429` | Rate limited | Yes — back off |
| `5xx` | Server error | Yes — retry with exponential backoff |

**Recommended retry strategy** for `429` and `5xx`:
```python
import time

def post_log_with_retry(payload, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = lsd_client.post("/logs", json=payload)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503) and attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
                continue
            raise
```

**Fire-and-forget pattern** (don't let logging failures break your app):
```python
import threading

def log_async(payload):
    def _post():
        try:
            post_log_with_retry(payload)
        except Exception:
            pass  # logging should never crash the main app
    threading.Thread(target=_post, daemon=True).start()
```

---

## Complete minimal Python client

```python
"""
Minimal LSD client — copy-paste into your project.
Set LSD_API_KEY and LSD_BASE_URL environment variables.
"""

import os
import time
import threading
import httpx

LSD_BASE_URL = os.getenv("LSD_BASE_URL", "http://localhost:8000/api/v1")
LSD_API_KEY = os.getenv("LSD_API_KEY", "")

_client = httpx.Client(
    base_url=LSD_BASE_URL,
    headers={"X-API-Key": LSD_API_KEY},
    timeout=5.0,
)


def log_llm_call(
    *,
    provider: str,
    model: str,
    messages: list[dict],
    response_content: str,
    finish_reason: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int | None = None,
    conversation_id: str | None = None,
    status: str = "ok",
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Fire-and-forget log of one LLM call.
    Never raises — logging should not break your application.
    """
    payload = {
        "provider": provider,
        "model": model,
        "conversation_id": conversation_id,
        "request": {"messages": messages},
        "response": {
            "message": {"role": "assistant", "content": response_content},
            "finish_reason": finish_reason,
        },
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "latency_ms": latency_ms,
        "status": status,
        "error": error,
        "metadata": metadata or {},
    }

    def _send():
        for attempt in range(3):
            try:
                r = _client.post("/logs", json=payload)
                if r.status_code < 500:
                    return
                time.sleep(2 ** attempt)
            except Exception:
                time.sleep(2 ** attempt)

    threading.Thread(target=_send, daemon=True).start()
```

---

## Reading your logs back

```python
# List recent logs (requires logs:read scope or session auth)
resp = _client.get("/logs", params={"limit": 20})
logs = resp.json()

# Get a full conversation
resp = _client.get(f"/conversations/session-abc-123")
convo = resp.json()
for entry in convo["entries"]:
    print(entry["model"], entry["total_tokens"])

# Aggregated stats
resp = _client.get("/stats/summary", params={"days": 7})
stats = resp.json()
print(f"Last 7 days: {stats['total_calls']} calls, ${stats['total_cost']:.4f}")
```

---

## Security checklist for client implementors

- [ ] Store `LSD_API_KEY` in an environment variable, not in source code
- [ ] Use a key with only `logs:write` scope for ingest (principle of least privilege)
- [ ] Never log secrets or credentials — redact `Authorization` headers in messages
- [ ] Log errors with `status: "error"` and the error string in `error` field
- [ ] Keep message content appropriate — all content is stored server-side
- [ ] Use `conversation_id` consistently per session to enable session debugging
- [ ] Use `client_timestamp` with the actual call time for accurate timing data
