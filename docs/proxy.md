# LLM Proxy — Drop-in OpenRouter Proxy with Logging

> The dashboard now acts as a **transparent LLM proxy**.  Point any
> OpenRouter-compatible client at our base URL, swap the API key, and every
> call is automatically logged — with zero client code changes.

---

## Quick start

```bash
# 1. Create an API key with the proxy:use scope (in the dashboard UI or API)
# 2. Set your client's base_url + key:

export OPENROUTER_BASE_URL="http://localhost:8000/api/v1"
export OPENROUTER_API_KEY="lsd_abc123_yourProxyKey"
```

That's it. Your client now proxies through the dashboard. Every chat completion
is logged and visible in the conversation views.

---

## Drop-in client setup

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/api/v1",
    api_key="lsd_abc123_yourProxyKey",
)

# Use exactly as you would with OpenRouter
response = client.chat.completions.create(
    model="openai/gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### curl

```bash
curl http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer lsd_abc123_yourProxyKey" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### JavaScript (fetch)

```js
const response = await fetch("http://localhost:8000/api/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer lsd_abc123_yourProxyKey",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "openai/gpt-4o",
    messages: [{ role: "user", content: "Hello!" }],
  }),
});
```

---

## Endpoints

All endpoints are under `/api/v1` and mirror OpenRouter's surface:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat/completions` | Chat completions (stream + non-stream) |
| `POST` | `/completions` | Legacy text completions |
| `GET`  | `/models` | Proxied model list (no auth) |
| `GET`  | `/proxy/health` | Proxy readiness check (no auth) |

---

## Authentication

The proxy accepts API keys in either header:

- `Authorization: Bearer lsd_...` (OpenAI SDK style, **recommended**)
- `X-API-Key: lsd_...` (existing client style)

The key **must** have the `proxy:use` scope. Keys without this scope get
`403 Forbidden`.

Create a proxy key in the dashboard (**API Keys** page) or via the API:

```bash
curl http://localhost:8000/api/v1/api-keys \
  -H "Cookie: lsd_session=..." \
  -H "X-CSRF-Token: ..." \
  -H "Content-Type: application/json" \
  -d '{"name": "my-proxy-key", "scopes": ["proxy:use"]}'
```

---

## Conversation grouping

The proxy derives a `conversation_id` automatically so multi-turn chats are
grouped correctly in the dashboard.

### Resolution order

1. **Explicit header** (best): `X-Conversation-Id: my-session-123`
2. **OpenRouter `user` field**: from `request_body.user`
3. **Derived** (fallback): hash of the leading system + first user message,
   salted per API key

### Recommended: use X-Conversation-Id

For guaranteed correct grouping, set the `X-Conversation-Id` header:

```bash
curl http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer lsd_abc_yourKey" \
  -H "X-Conversation-Id: session-abc-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-4o", "messages": [...]}'
```

### Derived-id behaviour and tradeoffs

When no explicit id is provided, the proxy derives one from message content.
This works well for most cases but has known edge cases:

| Scenario | Behaviour |
|----------|-----------|
| Two sessions with the same system prompt + first message | Same bucket, but prefix-based parent detection keeps them as **separate trees** — visible as branches, not corrupted |
| Client rewrites the system prompt mid-conversation | New bucket = new conversation in the dashboard; old messages remain accessible |
| Single-shot calls (no history) | Each call is its own conversation — correct |
| Concurrent calls in the same growing thread | Transient mis-attribution possible; self-correcting on next call |

The derived id is **purely a logging concern** — it never affects the bytes
returned to the client.

---

## Streaming

SSE streaming is fully supported. The proxy:

- Relays chunks byte-for-byte to the client
- Automatically sets `stream_options.include_usage` so token counts are captured
- Assembles the full response for logging at stream end

No client changes needed — just set `stream: true` as usual:

```python
response = client.chat.completions.create(
    model="openai/gpt-4o",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True,
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

---

## Cost tracking

The proxy captures cost from OpenRouter's native `usage.cost` field when
available (marked as `cost_source="client"` in logs). This is more accurate
than the push-based model which had to compute cost from a pricing table.

---

## Coexistence with POST /logs

The existing `POST /api/v1/logs` endpoint is **unchanged**. Both the proxy
and the push API write to the same `log_entries` table, so proxied calls
and manually-ingested calls appear side by side in the dashboard.

---

## Plugin architecture

The proxy runs a plugin pipeline for each request. The default pipeline is
`logging` (configured via `PROXY_PLUGINS=logging`). Available plugins:

| Plugin | Status | Description |
|--------|--------|-------------|
| `logging` | **active** | Maps OpenRouter → canonical schema, persists to DB |
| `compression` | **stub** | No-op skeleton; future: trim/dedupe messages to save tokens |

The pipeline is configured globally via the `PROXY_PLUGINS` env var.
Per-user/per-key plugin config is designed-for but not yet built.

---

## Configuration

```bash
OPENROUTER_API_KEY=sk-or-...           # server-held upstream key (required)
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_REFERER=                    # optional HTTP-Referer header
OPENROUTER_APP_TITLE=LLM Stats Dashboard
PROXY_PLUGINS=logging                  # comma-separated plugin names
PROXY_UPSTREAM_TIMEOUT_S=120
PROXY_STREAM_IDLE_TIMEOUT_S=120
```

---

## Health check

```bash
curl http://localhost:8000/api/v1/proxy/health
```

Returns:
```json
{
  "status": "ok",
  "upstream": "https://openrouter.ai/api/v1",
  "upstream_reachable": true,
  "problems": []
}
```

---

## Limits

- Maximum request body size: 1 MB (same as `POST /logs`)
- Upstream timeout: 120 s (configurable via `PROXY_UPSTREAM_TIMEOUT_S`)
- Rate limiting: inherits the app-wide rate limiter

---

## Future

- Compression strategies (history trimming, message dedupe, summarization)
- Per-user / per-key plugin configuration
- Per-user OpenRouter keys
- Caching plugin (semantic/exact-match)
- Additional providers (OpenAI, Anthropic native)
- Prompt firewall / PII redaction
