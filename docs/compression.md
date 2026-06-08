# Compression Plugin — Headroom-powered token savings

The compression plugin uses **[Headroom](https://github.com/headroom-ai/headroom)**
to compress request messages before forwarding to OpenRouter, reducing token
usage and cost.

## How it works

- Runs **on the request path only** — response and streaming are unaffected.
- Before each LLM call, messages are passed through Headroom's `compress()`.
- Compression is **fail-open**: if anything goes wrong, original messages are
  forwarded unchanged and the error is logged.
- Token savings are recorded in `LogEntry.metadata_extra.compression` and
  surfaced in the dashboard UI (log detail + conversation views).

## The four compressors

Headroom's `proxy` extra provides four CPU-only compressors:

| Compressor | What it does |
|------------|-------------|
| **JSON SmartCrusher** | Compresses large JSON payloads (tool outputs, API responses) by removing whitespace, shortening keys, and reducing precision. |
| **Code AST** | Compresses code blocks using tree-sitter AST shortening — preserves semantics, trims verbosity. |
| **ONNX Text Kompress** | ML-based text compression via ONNX INT8 model. Runs on CPU (no GPU). Small model (~30MB), fast (~32ms typical). |
| **CacheAligner** | Reorganises messages so repeated content (e.g. system prompts resent every turn) is de-duplicated. |

## CPU-only, no GPU required

Headroom's `proxy,code` extras install:
- `onnxruntime` (CPU-optimised ONNX inference)
- `transformers` (tokenizer only)
- `tree-sitter` + language pack (code AST)
- **No PyTorch** — no 2GB GPU dependency.

The ML text compressor (Kompress) runs on CPU with ONNX session options tuned for small VMs/containers. Typical latency: ~32ms, worst-case ~576ms.

## Configuration

All settings are env-overridable (prefix `COMPRESSION_`):

| Env var | Default | Description |
|---------|---------|-------------|
| `COMPRESSION_TARGET_RATIO` | (none) | Target compression ratio (0.0–1.0). `None` = auto-determined by Headroom. `0.5` = aim to reduce to 50% of original tokens. |
| `COMPRESSION_PROTECT_RECENT` | `4` | Number of most recent messages to leave untouched. Protects the conversation's immediate context. |
| `COMPRESSION_COMPRESS_USER_MESSAGES` | `false` | Whether to compress user messages. Off by default — user messages are usually short and compressors are tuned for system/assistant content. |
| `COMPRESSION_COMPRESS_SYSTEM_MESSAGES` | `true` | Whether to compress system messages. On by default — system prompts are prime candidates for compression. |
| `COMPRESSION_MIN_TOKENS` | `250` | Minimum token count to trigger compression. Messages below this threshold are passed through unchanged. |
| `COMPRESSION_KOMPRESS_MODEL` | `""` | Headroom Kompress model. Empty string = default ONNX model. Set to `"disabled"` to skip ML text compression entirely. |
| `HEADROOM_TELEMETRY` | `false` | Disables Headroom anonymous telemetry by default. Set to `true` to opt in. |

## Default behaviour

- Compression is **enabled by default** (`PROXY_PLUGINS=compression,logging`).
- The most recent 4 messages are protected from compression.
- System messages are compressed; user messages are not.
- Messages shorter than 250 tokens are skipped.
- All four compressors are active (JSON, code AST, ONNX text, cache alignment).

## Controlling compression

### Environment level

Change `PROXY_PLUGINS` to remove `compression`:
```bash
PROXY_PLUGINS=logging  # no compression
```

Or disable the ML text compressor specifically:
```bash
COMPRESSION_KOMPRESS_MODEL=disabled
```

### Per-user / per-conversation toggles

Compression can be toggled per user (via the Settings page) or per conversation
(via the Conversation page). Toggling compression off mid-conversation affects
**future** calls only — historical diffs are immutable.

### Plugin ordering

Compression runs first in the pipeline (before `logging`). The order is set by
`PROXY_PLUGINS` and can be customised:
```bash
PROXY_PLUGINS=logging,compression  # logging runs before compression (unusual)
```

## Metrics in the dashboard

When compression runs, each log entry stores:

```json
{
  "metadata_extra": {
    "compression": {
      "tokens_before": 4500,
      "tokens_after": 2100,
      "tokens_saved": 2400,
      "compression_ratio": 0.467,
      "transforms_applied": ["CacheAligner", "Kompress"]
    }
  }
}
```

The **log detail** page shows a "Tokens Saved by Compression" stat card.
The **conversation** view shows an aggregate tokens-saved total.

## Container / model cache

The ONNX Kompress model (~30MB) is downloaded from HuggingFace Hub on first use.
To avoid cold-start latency, warm the cache at container build time:

```dockerfile
# In your Dockerfile, after installing dependencies:
RUN python -c "from headroom import compress, CompressConfig; \
    compress([{'role':'user','content':'warmup'}], model='gpt-4o', \
    config=CompressConfig())"
```

Or via a Make target:
```bash
make warm-headroom
```

Set `HF_HOME` to a persistent volume for container deployments.

## Fallback behaviour

- If Headroom is not installed → `import` raises `ImportError` → caught by fail-open → original messages forwarded.
- If `compress()` raises (e.g. ONNX runtime error) → caught by fail-open → original messages forwarded, error logged.
- If compression is disabled via plugin toggle → plugin is not instantiated → no compression runs.

## Testing

CI tests mock `headroom.compress` so no model download / ONNX run happens in CI.
See `backend/tests/proxy/test_compression_plugin.py`.
