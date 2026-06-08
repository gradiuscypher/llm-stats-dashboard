# Headroom Compression Plugin — Implementation Plan

Replace the no-op `CompressionPlugin` stub with a real, Headroom-backed request
compression transform installed from **PyPI** (not source). Compression runs on the
proxy request path, before forwarding to OpenRouter, and records token-savings metrics
for the dashboard.

> Status: planned, not yet implemented.
> Related: [PROXY_PLAN.md](PROXY_PLAN.md), [PLUGIN_TOGGLE_AND_WORDCOUNT_PLAN.md](PLUGIN_TOGGLE_AND_WORDCOUNT_PLAN.md).

---

## 1. Why this fits cleanly

Headroom's Python library exposes exactly the right primitive:

```python
from headroom import compress, CompressConfig
result = compress(messages, model="gpt-4o", config=CompressConfig(...))
# result.messages          -> compressed messages (same OpenAI format)
# result.tokens_before/after/saved, result.compression_ratio, result.transforms_applied
```

`compress()` is a **pure, synchronous, fail-open** function that returns a transformed
message list. That maps 1:1 onto LSD's existing request-transform protocol:

```python
# backend/app/proxy/interceptor.py
class RequestTransform(Protocol):
    name: str
    def transform_request(self, messages: list[dict], ctx: TransformContext) -> list[dict]: ...
```

The interceptor (`RequestInterceptor.run`) already:
- snapshots messages before/after each transform,
- emits per-message `MessageDiff` rows (original -> final) that the UI renders,
- is fail-open (a raising transform is skipped, originals carried forward).

So compression edits get diff-tracking and UI rendering **for free**. No new pipeline
machinery is required — just a real implementation of the existing stub plus a small
metrics channel.

`TransformContext` already carries `model`, `user_id`, and a writable
`request_metadata: dict` — enough to drive `compress()` and to hand metrics back out.

---

## 2. Decisions (confirmed)

| Topic | Decision |
|-------|----------|
| **Install profile** | `headroom-ai[proxy,code]` from PyPI. CPU-only, **no PyTorch**. |
| **ML text compression** | **Enabled** (ONNX INT8 Kompress; runs on CPU, no GPU). |
| **Metrics** | Persist to `LogEntry.metadata_extra` JSONB (**no migration**). |
| **Default state** | **ON by default** — `PROXY_PLUGINS` default becomes `"compression,logging"`. Users can disable via toggle. |
| **Frontend** | **Include now** — surface "tokens saved" in log-detail + conversation views. |
| **CCR / retrieval** | **Out of scope.** Plain `compress()` only; no local store / retrieval tool (we can't inject tools into a transparent proxy). LSD's `message_diffs` already give dashboard-side reversibility. |

### 2.1 GPU / Incus container note

The ML text compressor (**Kompress**) ships as **ONNX INT8** and requires only
`onnxruntime` + `transformers` (tokenizer only) — **no `torch`**. It runs fine on a
GPU-less Incus container:

- Headroom provides CPU-tuned ONNX session options (`create_cpu_session_options`,
  arena/mem-pattern disabled) explicitly for "small VMs".
- Benchmarks (Apple M-series **CPU**): text compression ~32ms typical, ~576ms
  worst-case. Model is small (~30MB-class int8), downloaded once and cached.
- We deliberately **avoid** `headroom-ai[all]` and the `[ml]` extra (which pull in
  `torch` ~2GB, image/OCR, voice, memory-stack). The `[proxy,code]` extras give all
  four compressors (JSON SmartCrusher, code AST, ONNX text Kompress, CacheAligner)
  on CPU without torch.

**First-request cold start** downloads the HF model. Mitigate by warming the cache at
container build time (see §3.3) and persisting `HF_HOME`.

---

## 3. Backend changes

### 3.1 `backend/pyproject.toml`

Add the dependency (resolve with `uv` so `uv.lock` updates):

```bash
cd backend
uv add "headroom-ai[proxy,code]"
```

- Verify resolution against LSD's existing pins. Headroom's `proxy` extra declares
  floors for `fastapi>=0.100`, `uvicorn>=0.23`, `httpx[http2]>=0.24`, plus `openai`,
  `mcp`, `magika`, `zstandard`, `websockets`, `onnxruntime`, `transformers`. LSD pins
  `fastapi[standard]>=0.115`, `httpx>=0.28`, `uvicorn[standard]>=0.34` — these are
  compatible (LSD floors are higher). Re-run `make lint-backend` + `make test-backend`
  to confirm nothing regressed.
- Commit the updated `uv.lock`.

### 3.2 `backend/app/config.py`

Add compression settings (env-overridable, with the confirmed defaults):

```python
# Compression (Headroom)
compression_target_ratio: float | None = None
compression_protect_recent: int = 4
compression_compress_user_messages: bool = False
compression_compress_system_messages: bool = True
compression_min_tokens: int = 250
compression_kompress_model: str = ""   # "" -> Headroom default ONNX model; "disabled" -> ML text off
headroom_telemetry: bool = False        # if False, set HEADROOM_TELEMETRY=off at startup
```

Change the proxy plugin default:

```python
proxy_plugins: str = "compression,logging"  # was "logging"
```

Disable Headroom telemetry at startup (Headroom enables anonymous telemetry by
default). In the app factory / settings init, set the env var when
`settings.headroom_telemetry is False`:

```python
if not settings.headroom_telemetry:
    os.environ.setdefault("HEADROOM_TELEMETRY", "off")
```

Update `.env.example` with the new vars.

### 3.3 Container model pre-cache

Avoid a network fetch on the first user request:

- Add a Dockerfile build step (and/or a `make warm-headroom` target) that runs a tiny
  `compress()` call or `hf_hub_download` to populate the HF cache.
- Set `HF_HOME` (and `TRANSFORMERS_CACHE`) to a path that is baked into the image or
  mounted on a persistent volume in the Incus container.

### 3.4 `backend/app/proxy/plugins/compression.py` (rewrite)

Replace the stub with the real transform:

```python
import logging
from app.config import settings
from app.proxy.interceptor import TransformContext

logger = logging.getLogger(__name__)


class CompressionPlugin:
    name = "compression"

    def transform_request(self, messages: list[dict], ctx: TransformContext) -> list[dict]:
        if not messages:
            return messages
        try:
            from headroom import compress, CompressConfig  # lazy import

            cfg = CompressConfig(
                compress_user_messages=settings.compression_compress_user_messages,
                compress_system_messages=settings.compression_compress_system_messages,
                protect_recent=settings.compression_protect_recent,
                target_ratio=settings.compression_target_ratio,
                min_tokens_to_compress=settings.compression_min_tokens,
                kompress_model=(settings.compression_kompress_model or None),
            )
            result = compress(messages, model=ctx.model, config=cfg)

            # Hand metrics back to the proxy via the writable context dict.
            ctx.request_metadata["compression"] = {
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "tokens_saved": result.tokens_saved,
                "compression_ratio": result.compression_ratio,
                "transforms_applied": result.transforms_applied,
            }
            return result.messages
        except Exception:
            logger.exception("CompressionPlugin.transform_request failed")
            return messages  # fail-open (interceptor is also fail-open)
```

Notes:
- `CompressConfig` field is `kompress_model` where `None` -> default ONNX model and
  `"disabled"` -> skip ML text compression. Map empty string -> `None`.
- Keep `name = "compression"` so the existing registry entry and toggles work
  unchanged.

### 3.5 Metrics threading -> `LogEntry.metadata_extra`

`TransformContext.request_metadata` is already a dict and is already passed (currently
empty) by `_run_interceptor`. Wire it through:

1. **`backend/app/routers/proxy.py::_run_interceptor`** — build the `TransformContext`
   with a shared dict, run the interceptor, then copy metrics onto the proxy context:

   ```python
   tctx = TransformContext(model=ctx.model, user_id=str(ctx.user.id), request_metadata={})
   interceptor = RequestInterceptor(transforms)
   result = interceptor.run(messages, tctx)
   ctx.request_body["messages"] = result.final_messages
   ctx.request_diffs = result.diffs
   if tctx.request_metadata.get("compression"):
       ctx.state["compression"] = tctx.request_metadata["compression"]
   ```

   (Confirm `RequestInterceptor.run` passes the *same* `ctx` object to each transform
   — it does; `TransformContext` is constructed once and shared.)

2. **`backend/app/proxy/logging_sink.py::persist_log`** (and `persist_error_log`) —
   include compression metrics in the payload so they land in
   `LogEntry.metadata_extra`:

   ```python
   comp = ctx.state.get("compression")
   if comp:
       payload.metadata_extra = {**(payload.metadata_extra or {}), "compression": comp}
   ```

3. **`backend/app/services/openrouter_map.py::map_to_log_entry`** — verify it sets /
   preserves `metadata_extra` on the `LogEntryCreate`. If it currently hard-codes or
   drops it, extend it to carry through `ctx.state` metadata. `LogEntry.metadata_extra`
   already exists as a JSONB column (`backend/app/models/log_entry.py`), so **no
   Alembic migration is needed**.

4. Confirm `ingest_log_entry` persists `metadata_extra` from the payload onto the
   `LogEntry` row (extend if needed).

### 3.6 `backend/app/proxy/registry.py`

No code change required — `CompressionPlugin` is already registered under
`"compression"`. The default-on behavior comes from the `PROXY_PLUGINS` env change in
§3.2 (`_DEFAULT_ENABLED` derives from it). Ordering (`compression` before
`word_count`/`logging`) is env-driven and user-tunable.

---

## 4. Tests (`backend/tests/proxy/`)

Keep CI **hermetic** — monkeypatch `headroom.compress` so no model download / ONNX run
happens in CI.

New file `test_compression_plugin.py`:
- **Pass-through**: mocked `compress` returns shrunken messages -> plugin returns them.
- **Metrics**: plugin writes `compression` dict into `ctx.request_metadata`.
- **Fail-open**: mocked `compress` raises -> plugin returns original messages, logs.
- **End-to-end (respx-mocked OpenRouter)**: enable compression, send
  `POST /api/v1/chat/completions`, assert:
  - `LogEntry.metadata_extra["compression"]` is populated,
  - `message_diffs` rows exist for compressed messages.
- **Toggle**: disabling `compression` via `/plugins` removes it from the resolved
  pipeline (extend existing registry/pipeline tests for the new default-on ordering).

Optional (gated behind a marker, **not** in default CI): a real-Headroom test that
compresses a large JSON tool-output and asserts a meaningful `tokens_saved`.

---

## 5. Frontend changes

1. **`frontend/src/lib/api.ts`** — extend the `LogEntry` type with a typed
   `metadata_extra` (at least `compression?: { tokens_before; tokens_after;
   tokens_saved; compression_ratio; transforms_applied: string[] }`).
2. **`frontend/src/routes/log-detail.tsx`** — when
   `metadata_extra.compression` exists, show a stat:
   "Compression: N tokens saved (X%)" + the applied transforms.
3. **`frontend/src/routes/conversation.tsx`** — optional aggregate "tokens saved"
   across the conversation; reuse the existing diff/badge UI to mark compressed
   messages.

Route all HTTP through `frontend/src/lib/api.ts` (per conventions).

---

## 6. Docs

1. New `docs/compression.md` (served at `/api/v1/docs-md`, rendered at `/docs`):
   - what Headroom is + the four compressors,
   - CPU/ONNX no-torch story (Incus-friendly),
   - default-on behavior + how to disable (env, per-user, per-conversation),
   - config knobs (§3.2),
   - container model-cache note (§3.3),
   - request-only / fail-open semantics.
2. Update `docs/proxy.md` to reference the compression plugin.
3. Update `OVERVIEW.md`:
   - §3.6 plugin table: `compression.py` is no longer a stub,
   - §3.7 config vars: add the new compression settings + new `PROXY_PLUGINS` default.

---

## 7. Verification

1. `cd backend && uv add "headroom-ai[proxy,code]"`; commit `uv.lock`.
2. `make lint-backend` (ruff + ty) — clean lazy import / typing.
3. `make test-backend` — new + existing proxy tests pass (Headroom mocked).
4. `make lint-frontend` + `make test-frontend` — type + component tests pass.
5. Manual smoke: `PROXY_PLUGINS="compression,logging"`, `make dev`, send a
   `POST /api/v1/chat/completions` with a large JSON/log + prose message; confirm:
   - response correct,
   - `message_diffs` show compressed content,
   - `LogEntry.metadata_extra.compression` populated,
   - log-detail UI shows tokens saved,
   - toggling compression off via `/plugins` removes the effect.
6. `make check` before declaring done.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Dep resolution conflict (Headroom `proxy` extra floors) | `uv add` resolves; LSD pins are higher floors. Verify with `make lint`/`make test`. |
| First-request cold model download / latency | Warm HF cache at build time (§3.3); `min_tokens_to_compress` gate; ONNX CPU is fast. |
| Default-on blast radius | respx e2e test; kill-switch via `compression_kompress_model="disabled"` or removing from `PROXY_PLUGINS`; per-user/per-conversation toggles for opt-out. |
| Synchronous compression adds request latency | Request-only (streaming responses unaffected); `min_tokens` gate; ML text on CPU ~32ms typical. |
| Telemetry on by default | `HEADROOM_TELEMETRY=off` set at startup when `headroom_telemetry` is False. |
| Container memory on small VM | Headroom's CPU-tuned ONNX session options reduce retained RSS; avoid `[all]`/`[ml]`. |

---

## 9. File-change checklist

**Backend**
- [ ] `backend/pyproject.toml` + `backend/uv.lock` — add `headroom-ai[proxy,code]`
- [ ] `backend/app/config.py` — compression settings + `proxy_plugins` default + telemetry off
- [ ] `.env.example` — document new vars
- [ ] `backend/app/proxy/plugins/compression.py` — real implementation
- [ ] `backend/app/routers/proxy.py` — thread `request_metadata` -> `ctx.state`
- [ ] `backend/app/proxy/logging_sink.py` — write `compression` to `metadata_extra`
- [ ] `backend/app/services/openrouter_map.py` / `ingest.py` — preserve `metadata_extra`
- [ ] Dockerfile / `make warm-headroom` — pre-cache HF model

**Tests**
- [ ] `backend/tests/proxy/test_compression_plugin.py` — unit + respx e2e
- [ ] Update registry/pipeline tests for default-on ordering

**Frontend**
- [ ] `frontend/src/lib/api.ts` — `metadata_extra.compression` type
- [ ] `frontend/src/routes/log-detail.tsx` — tokens-saved stat
- [ ] `frontend/src/routes/conversation.tsx` — aggregate savings (optional)

**Docs**
- [ ] `docs/compression.md` (new)
- [ ] `docs/proxy.md` — reference compression
- [ ] `OVERVIEW.md` — §3.6 + §3.7 updates
