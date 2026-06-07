# Canonical Log Schema Reference

**Related**: [Log Endpoints](endpoints/logs.md) · [AI Client Guide](ai-client-guide.md) · [Index](index.md)

This is the full reference for the canonical log payload sent to
`POST /api/v1/logs`. All LLM clients must map their provider-native format to
this schema.

---

## Top-level fields

```jsonc
{
  // Required
  "provider": "openai",              // string — provider identifier
  "model": "gpt-4o",                 // string — model identifier
  "request": { ... },                // RequestPayload — see below
  "response": { ... },               // ResponsePayload — see below

  // Strongly recommended
  "conversation_id": "session-abc",  // string | null — groups calls into a session
  "usage": { ... },                  // UsagePayload — token counts
  "status": "ok",                    // "ok" | "error"

  // Optional
  "client_timestamp": "2025-06-01T12:00:00Z",  // ISO-8601
  "tool_calls": [ ... ],             // ToolCall[] — see below
  "cost": { ... },                   // CostPayload | null — omit to let server compute
  "latency_ms": 423,                 // integer | null
  "error": null,                     // string | null — required when status="error"
  "metadata": {}                     // any passthrough key-value pairs
}
```

### Field details

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | ✓ | Provider slug. Examples: `openai`, `anthropic`, `google`, `mistral`, `ollama` |
| `model` | string | ✓ | Model identifier as used by the provider. E.g. `gpt-4o`, `claude-3-5-sonnet-20241022` |
| `request` | RequestPayload | ✓ | The messages sent to the model |
| `response` | ResponsePayload | ✓ | The model's reply |
| `conversation_id` | string\|null | — | Client-defined identifier grouping calls into a session. Use any stable string (UUID, slug, hash). Required to use the conversation view. |
| `usage` | UsagePayload | — | Token counts. Required for cost computation if `cost` is omitted. |
| `status` | `"ok"\|"error"` | — | Defaults to `"ok"`. If `"error"`, `error` field is required. |
| `client_timestamp` | ISO-8601\|null | — | When the call occurred on the client (before network). |
| `tool_calls` | ToolCall[] | — | Tool/function calls and their results. |
| `cost` | CostPayload\|null | — | If provided, stored as-is with `cost_source: "client"`. If omitted, server computes from pricing table. |
| `latency_ms` | int\|null | — | End-to-end latency as measured by the client, in milliseconds. |
| `error` | string\|null | — | Error message. **Required** when `status` is `"error"`. |
| `metadata` | object | — | Arbitrary passthrough data. Tag your calls with `{"env": "prod", "user_id": "u_123"}` etc. Stored as-is. |

---

## RequestPayload

```jsonc
{
  "messages": [
    { "role": "system",    "content": "You are a helpful assistant." },
    { "role": "user",      "content": "What is 2+2?" },
    { "role": "assistant", "content": "4" },
    { "role": "user",      "content": "Why?" }
  ],
  "params": {
    "temperature": 0.7,
    "max_tokens": 1024
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `messages` | Message[] | ✓ | Ordered list of all messages in the context window |
| `params` | object | — | Model parameters (temperature, top_p, etc.). Provider-specific. |

### Message object

| Field | Type | Description |
|-------|------|-------------|
| `role` | `"system"\|"user"\|"assistant"\|"tool"` | Message author |
| `content` | string \| parts[] | Text string, or structured parts array for multimodal content |
| `reasoning` | string\|null | Reasoning/thinking text (from reasoning models like o1, DeepSeek-R1, etc.) |
| `reasoning_details` | object[]\|null | Structured reasoning blocks (provider-native format; may include encrypted/redacted entries) |

**Multimodal content** (structured parts):
```json
[
  { "type": "text",      "text": "Describe this image:" },
  { "type": "image_url", "image_url": { "url": "https://..." } }
]
```

---

## ResponsePayload

```jsonc
{
  "message": {
    "role": "assistant",
    "content": "The answer is 4 because..."
  },
  "finish_reason": "stop"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | Message | ✓ | The model's response message |
| `finish_reason` | string\|null | — | Why generation stopped: `stop`, `length`, `tool_calls`, `content_filter`, etc. |

---

## UsagePayload

```json
{
  "prompt_tokens": 22,
  "completion_tokens": 3,
  "total_tokens": 25,
  "reasoning_tokens": 8
}
```

All fields default to `0` if omitted. `reasoning_tokens` captures
`completion_tokens_details.reasoning_tokens` from providers that report it.

All fields default to `0` if omitted. `total_tokens` is used for cost
computation if individual counts aren't available. `reasoning_tokens`
is reported by reasoning/thinking-capable models (e.g. reasoning models
on OpenRouter).

---

## CostPayload

```json
{
  "total": 0.000185,
  "currency": "USD"
}
```

If `cost` is **omitted**, the server will attempt to compute it using the
built-in pricing table for the given `provider` + `model`.
If no pricing entry exists, `cost_total` will be `null`.

---

## ToolCall

```jsonc
{
  "id": "call_abc123",      // optional — provider-assigned call ID
  "name": "get_weather",    // function/tool name
  "arguments": {            // parsed arguments object
    "location": "San Francisco",
    "unit": "celsius"
  },
  "result": {               // optional — tool execution result
    "temperature": 18,
    "description": "Partly cloudy"
  }
}
```

Include all tool calls from the response, with their results if available.

---

## Minimal valid payload

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "request": {
    "messages": [{ "role": "user", "content": "Hello" }]
  },
  "response": {
    "message": { "role": "assistant", "content": "Hi!" }
  }
}
```

---

## Full example payload

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "conversation_id": "session-debug-20250601",
  "client_timestamp": "2025-06-01T12:00:00.000Z",
  "request": {
    "messages": [
      { "role": "system", "content": "You are a helpful assistant." },
      { "role": "user",   "content": "What tools do you have available?" }
    ],
    "params": { "temperature": 0.3, "max_tokens": 512 }
  },
  "response": {
    "message": { "role": "assistant", "content": "I have access to get_weather and search." },
    "finish_reason": "stop"
  },
  "tool_calls": [],
  "usage": {
    "prompt_tokens": 28,
    "completion_tokens": 14,
    "total_tokens": 42
  },
  "latency_ms": 312,
  "status": "ok",
  "metadata": {
    "env": "production",
    "client_version": "1.2.0",
    "user_id": "u_alice_42"
  }
}
```
