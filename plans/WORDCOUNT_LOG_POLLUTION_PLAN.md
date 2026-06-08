# WORDCOUNT_LOG_POLLUTION_PLAN.md

## Problem

The `word_count` plugin appends `\n\n[word_count: N]` markers to messages by mutating
`ctx.request_body` (in `on_request`) and `ctx.response_body` (in `on_response_sync`).
These mutated bodies are then read by `map_to_log_entry` → `intern_messages`, baking
the plugin marker permanently into the canonical interned message content in the DB.

**Result:** when the plugin is later disabled and conversation continues, the newest
trunk entries rehydrate *marker-free* versions of the same turn messages (since the
messages are content-addressed and the plugin no longer appends the marker). The
transcript renders whatever variant the trunk's entry references, making prior plugin
activity appear to vanish from conversation history.

## Root Cause

Plugin mutations to `request_body` and `response_body` were leaking into the canonical
logging path. No snapshot was preserved of the original client request or original
upstream response before plugins touched them.

## Fix: Deep-copy snapshots on ProxyContext

### Changes Made

**1. `backend/app/proxy/context.py`** — Added snapshot fields:
- `original_request_body: dict | None = None` — deep copy of the request body before any plugin runs
- `original_response_body: dict | None = None` — deep copy of the upstream response before sync mutators

**2. `backend/app/routers/proxy.py`** — Snapshot at the right moments:
- `_build_context`: `copy.deepcopy(request_body)` → `original_request_body` immediately after parsing
- `_handle_non_stream`: `copy.deepcopy(ctx.response_body)` → `original_response_body` after upstream response set, before `on_response_sync`
- `_handle_stream`: same snapshot after `assembler.assemble()`, before `on_response_sync`

**3. `backend/app/services/openrouter_map.py`** — Read from snapshots:
- `map_to_log_entry`: uses `ctx.original_request_body or ctx.request_body` for request messages; uses `ctx.original_response_body or upstream_response` for response/usage extraction
- `map_error_to_log_entry`: uses `ctx.original_request_body or ctx.request_body`

**4. `backend/app/proxy/plugins/logging.py`** — No changes needed:
- Already calls `map_to_log_entry` which now reads snapshots internally; the `payload.request.messages` and `payload.response.message` passed to `intern_messages` are automatically marker-free.
- `MessageModification` rows (from `ctx.modifications`) remain the sole durable record of plugin activity.

### Tests Added

**Unit tests** (`tests/proxy/test_proxy_unit.py`):
- `test_logs_original_request_not_mutated` — mutated `request_body` + `original_request_body` snapshot → logged content is from original
- `test_logs_original_response_not_mutated` — mutated `response_body` + `original_response_body` snapshot → logged content is from original
- `test_falls_back_when_snapshots_are_none` — backward compatibility: without snapshots, uses `request_body`/`upstream_response`

**API integration tests** (`tests/proxy/test_proxy_api.py`):
- `test_word_count_sync_mutator_still_works` — client-visible response still gets the marker (sync mutator not broken)
- `test_word_count_marker_only_in_response` — word_count with system+user messages
- `test_disabled_then_reenabled_history_stable` — full toggle cycle: ON → OFF → ON, proxy still works at each step

### What still needs manual verification (dev server)

1. Enable word_count, run a few proxy turns, then disable and continue — verify the transcript
   in the UI shows the original (marker-free) messages for all turns, with modification badges
   marking where word_count was active.
2. Verify `MessageModification` rows persist correctly for entries created before the toggle was disabled.

### No schema changes required

The `message_modifications` table already stores `plugin_name`, `target`, `summary`, `detail`,
`message_index`, `message_role` — all populated by the word_count plugin's
`ctx.record_modification()` calls. No migration needed.
