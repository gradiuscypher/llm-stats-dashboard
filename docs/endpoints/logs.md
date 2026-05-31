# Log Ingestion & Retrieval

**Related**: [Canonical Schema](../schemas.md) · [API Keys](api-keys.md) · [AI Client Guide](../ai-client-guide.md) · [Index](../index.md)

---

## `POST /api/v1/logs` — Ingest one LLM call

**Auth**: API key with `logs:write` scope (`X-API-Key` header)

Log a single LLM call (request + response) in canonical format.
For the complete schema reference see [schemas.md](../schemas.md).

**Request body**: see [Canonical Schema](../schemas.md)

**Response** `201 Created` — [Log summary object](#log-summary-object)

**Error responses**
- `401` — missing or invalid API key
- `403` — API key lacks `logs:write` scope
- `413` — request body exceeds 1MB limit
- `422` — schema validation error (field-level details in response)

**Example (curl)**
```bash
curl -X POST http://localhost:8000/api/v1/logs \
  -H "X-API-Key: lsd_aB3xYz12_yourSecretHere" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "model": "gpt-4o",
    "conversation_id": "session-abc-123",
    "request": {
      "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is 2+2?"}
      ]
    },
    "response": {
      "message": {"role": "assistant", "content": "4"},
      "finish_reason": "stop"
    },
    "usage": {"prompt_tokens": 22, "completion_tokens": 3, "total_tokens": 25},
    "status": "ok"
  }'
```

**Example (Python with httpx)**
```python
import httpx

client = httpx.Client(
    base_url="http://localhost:8000/api/v1",
    headers={"X-API-Key": "lsd_aB3xYz12_yourSecretHere"},
)

client.post("/logs", json={
    "provider": "openai",
    "model": "gpt-4o",
    "conversation_id": "session-abc-123",
    "request": {
        "messages": [
            {"role": "user", "content": "Hello!"}
        ]
    },
    "response": {
        "message": {"role": "assistant", "content": "Hi there!"},
        "finish_reason": "stop",
    },
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    "status": "ok",
}).raise_for_status()
```

**Example (JavaScript/TypeScript)**
```ts
await fetch("/api/v1/logs", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "lsd_aB3xYz12_yourSecretHere",
  },
  body: JSON.stringify({
    provider: "openai",
    model: "gpt-4o",
    conversation_id: "session-abc-123",
    request: { messages: [{ role: "user", content: "Hello!" }] },
    response: {
      message: { role: "assistant", content: "Hi!" },
      finish_reason: "stop",
    },
    usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    status: "ok",
  }),
});
```

---

## `GET /api/v1/logs` — List log entries

**Auth**: session cookie OR API key with `logs:read`

**Query parameters**

| Param | Type | Description |
|-------|------|-------------|
| `conversation_id` | string | Filter by conversation ID |
| `model` | string | Filter by model (exact match) |
| `provider` | string | Filter by provider (exact match) |
| `since` | ISO-8601 | Filter entries after this datetime |
| `until` | ISO-8601 | Filter entries before this datetime |
| `limit` | int (1–500) | Max results. Default: 50 |
| `offset` | int | Pagination offset. Default: 0 |

**Response** `200 OK` — array of [Log summary objects](#log-summary-object)

---

## `GET /api/v1/logs/{id}` — Get full log detail

**Auth**: session cookie OR API key with `logs:read`

Returns the full log entry including raw `request` and `response` bodies.

**Response** `200 OK` — [Log detail object](#log-detail-object)  
**Error**: `404` if not found or not owned by current user

---

## `GET /api/v1/conversations/{conversation_id}` — Get conversation

**Auth**: session cookie OR API key with `logs:read`

Returns all log entries for a conversation in chronological order, plus
aggregate token and cost totals. Useful for reconstructing a full LLM session.

**Response** `200 OK`
```json
{
  "conversation_id": "session-abc-123",
  "entries": [ ...LogDetailObject ],
  "total_tokens": 1250,
  "total_cost": 0.00312
}
```

---

## `GET /api/v1/stats/summary` — Aggregated stats

**Auth**: session cookie OR API key with `logs:read`

**Query parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `days` | 30 | Lookback window (1–365) |

**Response** `200 OK`
```json
{
  "total_calls": 142,
  "total_tokens": 88320,
  "total_cost": 1.2045,
  "by_day": [
    { "date": "2025-06-01", "calls": 12, "total_tokens": 7200, "cost": 0.09 }
  ],
  "by_model": [
    { "model": "gpt-4o", "calls": 98, "total_tokens": 60000, "cost": 0.85 }
  ]
}
```

---

## Log summary object

Returned by list and ingest endpoints (no request/response bodies).

```json
{
  "id": "uuid",
  "user_id": "uuid",
  "conversation_id": "session-abc-123",
  "provider": "openai",
  "model": "gpt-4o",
  "prompt_tokens": 22,
  "completion_tokens": 3,
  "total_tokens": 25,
  "cost_total": 0.000185,
  "cost_currency": "USD",
  "cost_source": "computed",
  "latency_ms": 423,
  "status": "ok",
  "client_timestamp": "2025-06-01T12:00:00Z",
  "created_at": "2025-06-01T12:00:00.123Z"
}
```

## Log detail object

Extends summary with full request/response bodies:

```json
{
  "...all summary fields...",
  "request": { "messages": [...], "params": {} },
  "response": { "message": {...}, "finish_reason": "stop" },
  "tool_calls": [],
  "error": null,
  "metadata_extra": {}
}
```
