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
3. **Prefix-ancestor inheritance**: if the new request's messages extend an
   existing entry's `message_ids` (i.e. the client resends full history with
   a new turn), the new call inherits that existing entry's `conversation_id`.
   This matches how stateless chat APIs work and chains multi-turn sessions
   correctly regardless of how much wall-clock time passes between turns.
4. **Fresh UUID**: when no structural link is found, a new conversation_id
   is minted — guaranteeing unrelated sessions with coincidentally similar
   opening messages never merge.

### Recommended: use X-Conversation-Id

For guaranteed correct grouping across clients that don't resend full history
(sliding-window context, summarization, etc.), set the `X-Conversation-Id` header:

```bash
curl http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer lsd_abc_yourKey" \
  -H "X-Conversation-Id: session-abc-123" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-4o", "messages": [...]}'
```

### Derived-id behaviour and tradeoffs

When no explicit id is provided, the proxy derives one from message structure.
This works well for most cases but has known edge cases:

| Scenario | Behaviour |
|----------|-----------|
| Multi-turn with full history | Turn N+1 chains to turn N via prefix match — one conversation |
| Two separate sessions, same first message | Each gets its own UUID — **no merge** |
| Client truncates history (sliding window) | Prefix chain breaks → new conversation — over-split, not merge |
| Retry of first message within an existing conversation | New first message lacks a proper-prefix ancestor → new conversation; use `X-Conversation-Id` to keep retries grouped |
| Same session, long idle between turns | Prefix match doesn't care about wall-clock time — still chains correctly |

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

## Reasoning / thinking capture

The proxy captures **all** assistant output — including reasoning/thinking
blocks from reasoning-capable models. Both paths are covered:

- **Streaming**: `delta.reasoning` and `delta.reasoning_details` fragments
  are accumulated by the stream assembler.
- **Non-streaming**: `message.reasoning` and `message.reasoning_details` are
  passed through from the upstream response.
- **Usage**: `completion_tokens_details.reasoning_tokens` is captured as
  `reasoning_tokens` in the log entry.

Reasoning is stored on the assistant message and surfaced in the conversation
transcript and log detail views as a collapsible "Thinking" block.
Encrypted/redacted reasoning blocks are shown as labeled placeholders.

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
