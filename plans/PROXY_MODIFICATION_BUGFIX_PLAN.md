# Proxy In-line Modification Bugfix — Plan

> Fixes two reported bugs with the in-line proxy modifications (WordCount plugin),
> investigated against conversation `44422dfd-667b-4f72-9e37-4d9c038b1877`.
> Companion to `plans/PLUGIN_TOGGLE_AND_WORDCOUNT_PLAN.md` and
> `plans/PROXY_PLAN.md`. Status: **planning** (no code written yet).

This document is self-contained: it includes the investigation, the confirmed
root causes (with evidence), the locked design decisions, and an exact,
file-by-file implementation plan with tests and verification. Another agent
should be able to implement it without re-discovering the codebase.

---

## 0. Reported bugs

1. **"When I disable word-count, it still modifies my messages even after it's
   been disabled."**
2. **"When I disable word-count, all messages that were previously modified
   appear to not have been modified any longer. The diff view is also missing."**

---

## 1. Investigation & evidence

### 1.1 The data (conversation `44422dfd-…`, user `41a24561-…`)

8 log entries. WordCount markers (`[word_count: N]`) are interned into the
**upstream** message content for the early entries and absent for later ones:

| Time | Entry | word_count effect | `message_diffs` rows |
|------|-------|-------------------|----------------------|
| 05:26:57 | `ad900efc` | none (clean) | 0 |
| 05:27:34 | `396dc7ee` | marker present (msg `8cdaa9ed`) | 1 |
| 05:28:17 | `9f09f9e3` | marker present (msg `112f80ed`) | 1 |
| 05:28:20 | `8feb0a0b` | marker present | 1 |
| 05:29:17 | `60595f3b` | **no marker** (clean) | 0 |
| 05:29:24 | `09cbd414` | no marker | 0 |
| 05:29:31 | `385e78ca` | no marker | 0 |
| 05:31:10 | `ce37429b` | no marker | 0 |

State at investigation time:
- `plugin_config`: `word_count = enabled:false`, `updated_at = 01:55` (well before
  the conversation).
- `plugin_config_conversation`: **no rows** for this conversation.

### 1.2 Resolver log (decisive)

`grep "Pipeline for user" logs/lsd.log` for this conversation:

```
05:26:52  conv=44422dfd…: []
05:27:29  conv=44422dfd…: ['word_count']
05:28:12  conv=44422dfd…: ['word_count']
05:28:17  conv=44422dfd…: ['word_count']
05:28:21  conv=44422dfd…: ['word_count']
05:29:06  conv=44422dfd…: []
05:29:17  conv=44422dfd…: []
05:29:24  conv=44422dfd…: []
05:31:04  conv=44422dfd…: []
```

WordCount ran **only** while enabled (05:27–05:28) and stopped the moment it was
disabled (05:29). Since the global config was disabled since 01:55, the only way
`word_count` was in the pipeline is a **per-conversation override** that was
enabled (~05:27) and then removed (~05:29). The `DELETE
/conversations/{id}/plugins/{name}` endpoint **hard-deletes** the override row,
which is why no trace remains.

### 1.3 Conclusions

- **Bug #1 is NOT a resolver defect.** Modifications genuinely stopped when the
  plugin was disabled. The "still modified" perception is produced by the
  transcript continuing to display the earlier (enabled-era) modified turns —
  i.e. it is really a symptom of **bug #2**. The actionable part of bug #1 is
  **auditability**: hard-deleting the override leaves no record of when it was
  on, making the behavior confusing/unexplainable after the fact.
- **Bug #2 is a real code defect** with a single underlying cause plus a
  surfacing bug (details below).

### 1.4 Root cause: canonical history is interned POST-transform

The proxy interns the **post-transform (modified)** messages as the canonical
conversation history:

- `backend/app/routers/proxy.py::_run_interceptor` writes the transformed
  messages back into `ctx.request_body["messages"]`.
- `backend/app/services/openrouter_map.py::map_to_log_entry` /
  `map_error_to_log_entry` build `request.messages` from `ctx.request_body`
  (the mutated body) — see the comment "post-interceptor — final messages are
  canonical."
- `backend/app/proxy/logging_sink.py::persist_log` interns
  `payload.request.messages` (now the modified set).

This one design choice causes both halves of bug #2:

1. **Tree collapse → modified turn hidden.**
   `backend/app/services/ingest.py::ingest_log_entry` calls
   `resolve_parent_entry_id` (`services/messages.py`), which links entries by
   **longest proper prefix** of `message_ids`. When a toggle changes a turn's
   content, the interned message id for that turn changes, so a later (clean)
   entry is no longer a prefix-extension of the earlier (modified) entry. The
   prefix match fails → `parent_entry_id` is **NULL for every entry** in this
   conversation (verified). In `routers/logs.py::get_transcript`, all entries
   then land in `children[None]`; `_longest_chain(None)` greedily picks the
   single entry with the most `message_ids` (`ce37429b`, 20 msgs) as the entire
   trunk and treats the rest as branches. The marker-bearing message
   (`8cdaa9ed`, only in `396dc7ee`) is off-trunk, so the trunk shows the clean
   variant (`50cfce4b`) — making modified turns look unmodified.

2. **Diff attribution lost → diff view missing.**
   `message_diffs.message_index` is the index in the **full original message
   array** (e.g. 3 and 5 in this conversation). But `get_transcript` matches
   diffs to messages using `idx_in_entry` — the position within `new_ids` (only
   the newly-introduced/deduped messages for that entry). These indices almost
   never align, so the lookup
   `diff_key = (entry.id, idx_in_entry, msg_role)` misses, `modified_by` stays
   empty and `original_content` stays `None`. The frontend
   (`frontend/src/routes/conversation.tsx::MessageBubble`) only renders the
   `<MessageDiff>` overlay and the "modified by" label when `modified_by` is
   non-empty and `original_content` is present — so the diff view disappears.

Evidence for the index mismatch — `message_diffs` rows:

| entry | message_index | role | change_kind | modified_by |
|-------|---------------|------|-------------|-------------|
| `396dc7ee` | 3 | user | modified | `["word_count"]` |
| `9f09f9e3` | 5 | user | modified | `["word_count"]` |
| `8feb0a0b` | 5 | user | modified | `["word_count"]` |

---

## 2. Locked design decisions

| Area | Decision |
|------|----------|
| Canonical history | **Intern the ORIGINAL (pre-transform) messages** as canonical conversation history. Transform diffs are an **overlay**. This keeps the conversation tree + identity stable across toggles (matches the existing "identity must be stable across transform toggles" intent already encoded in `ProxyContext.original_request_messages` and `conversation_identity`). |
| Override audit (bug #1) | **Log-only.** No schema/migration. Add explicit audit log lines on override/global create/update/delete. The existing `resolve_pipeline` debug line plus these makes "was it on for that call?" answerable. |
| Historical data | Existing entries in `44422dfd…` were interned with modified content and have NULL `parent_entry_id`. They will **not** be retroactively fixed (out of scope). A one-off backfill is a possible follow-up — flagged, not implemented. |

Why "intern original" over "keep modified + only fix indices": interning the
original makes `resolve_parent_entry_id` work again *for free* (prefix matching
is stable across toggles), makes diff `message_index` line up with positions in
`entry.message_ids`, and matches the product intent that the transcript shows
the user's real conversation with the transform shown as an overlay diff.

---

## 3. Implementation — Part A: intern ORIGINAL messages as canonical

### A1. `backend/app/services/openrouter_map.py`
- `map_to_log_entry(ctx, response_body, …)`: build `request.messages` from
  **`ctx.original_request_messages`** (fallback to
  `ctx.request_body.get("messages", [])` if the snapshot is `None`/empty).
  Keep `params` derived from `request_body` (all non-`messages`/`model` keys).
  Update the misleading comment ("post-interceptor — final messages are
  canonical").
- `map_error_to_log_entry(...)`: same change (use original messages).
- Do **not** touch response mapping (responses are never transformed here).

### A2. `backend/app/proxy/logging_sink.py`
- `persist_log` and `persist_error_log` already intern `payload.request.messages`.
  Once A1 lands, that is automatically the **original** set — confirm neither
  path re-reads `ctx.request_body["messages"]` for interning. No further change
  expected, but verify.

### A3. Diffs unchanged
- `backend/app/proxy/interceptor.py` and `backend/app/services/diffs.py` stay
  as-is. `ctx.request_diffs` already carries `original_content` (client) and
  `final_content` (sent upstream), keyed by full-array `message_index`. With A1,
  `message_index` now corresponds to the position of that message in
  `entry.message_ids` (both derived from the original list).

### A4. Consequence (free fix)
- With A1+A2, `resolve_parent_entry_id` prefix matching works across toggles, so
  `parent_entry_id` populates again and the transcript tree no longer collapses.
  No change needed in `ingest.py`/`messages.py`; add a regression test (Part E).

---

## 4. Implementation — Part B: fix transcript diff attribution

### B1. `backend/app/routers/logs.py` → `get_transcript`
Replace the broken index-based diff lookup with a **`message_id`-keyed** lookup.

- After fetching `all_diffs`, build `diff_by_message_id: dict[uuid.UUID, MessageDiff]`:
  - For each diff, the introducing entry is `diff.log_entry_id`; resolve the
    referenced interned message id as
    `entry.message_ids[diff.message_index]` (guard against index out of range;
    only handle `change_kind == "modified"` for the overlay — `added`/`removed`
    can be skipped for the message-level overlay since WordCount is `modified`).
  - Map `message_id → diff`.
- In **both** the trunk loop and the branch loop, replace the existing:
  ```python
  diff_key = (entry.id, idx_in_entry, msg_role)
  original_content = diff_original_map.get(diff_key)
  diff_mod_by = diff_modified_by_map.get(diff_key)
  ```
  with a direct `diff = diff_by_message_id.get(mid)` lookup; when present set
  `modified_by = diff.modified_by` and populate the original/modified content
  fields (see Part C for which fields).
- Remove the now-dead `diff_original_map` / `diff_modified_by_map`
  (keyed by the broken `(entry, idx_in_entry, role)` tuple).
- The legacy `MessageModification` path (`mod_targets_by_entry`,
  `mods_by_entry`) has 0 rows for proxy conversations; keep it only if used
  elsewhere, otherwise treat `message_diffs` as authoritative and note the
  legacy path for cleanup (do not expand it).

### B2. `get_log_detail` / `_to_detail_with_db`
- These already return the full `request_diffs` list for a single entry (no
  index mapping), so they keep working. Verify the log-detail UI consumes
  `request_diffs` directly rather than index-matching.

### B3. CallDivider diffs
- `CallDivider.diffs` / `diff_count` are populated per entry from
  `diffs_by_entry` — unaffected and still correct.

---

## 5. Implementation — Part C: frontend diff semantics

Because canonical is now the **original**, the displayed `msg.content` is what
the user actually sent; the modified text is the diff's `final_content`. The
overlay must show **original → what-was-sent-upstream**.

### C1. Backend schema — `backend/app/schemas/log_entry.py`
- Add `modified_content: Any | None = None` to `TranscriptMessage`.
- In `get_transcript`, when a diff matches a message:
  - `original_content` ← `diff.original_content.get("content")` (the original;
    equals `msg.content` for `modified` turns, kept for clarity/back-compat).
  - `modified_content` ← `diff.final_content.get("content")` (sent upstream).
  - `modified_by` ← `diff.modified_by`.

### C2. Frontend types — `frontend/src/lib/api.ts`
- Add `modified_content?: unknown` to `TranscriptMessage`. Keep
  `MessageDiffPublic` in sync if used.

### C3. Frontend render — `frontend/src/routes/conversation.tsx`
- In `MessageBubble`: compute `modText = msg.modified_content != null ?
  contentText(msg.modified_content) : null`.
- Render the diff as **original (`text`) → sent (`modText`)** via `MessageDiff`
  when `showDiff && modText != null && modText !== text`. Keep the amber
  left-border + `ModifiedByLabel` when `modified_by` is non-empty.
- Adjust `MessageDiff` props/labels so the "before" is the original and the
  "after" is what was sent upstream (clarify labels, e.g. "your message" →
  "sent to model").

---

## 6. Implementation — Part D: override auditability (bug #1, log-only)

### D1. `backend/app/routers/plugins.py`
Add explicit `logger.info(...)` lines (no schema change):
- `set_plugin_global`: log `user_id`, `plugin_name`, old→new `enabled`.
- `set_conversation_plugin_override`: log `user_id`, `conversation_id`,
  `plugin_name`, `enabled`, created-vs-updated.
- `delete_conversation_plugin_override`: log `user_id`, `conversation_id`,
  `plugin_name`, "override removed → reverting to global".

These complement the existing `resolve_pipeline` debug line so the per-call
plugin state is fully reconstructable from logs.

---

## 7. Tests — Part E

Backend (`pytest`, OpenRouter mocked via `respx`) under
`backend/tests/proxy/` and `backend/tests/api/`:

1. **Canonical = original:** proxy a call with `word_count` enabled; assert
   (a) the upstream request body received the marker, (b) the interned messages
   referenced by `entry.message_ids` are the **original** (no marker),
   (c) a `message_diff` row exists whose `final_content` contains the marker.
2. **Parent linkage stable across toggle:** two calls in one conversation —
   word_count enabled for turn 1, disabled for turn 2; assert turn 2's
   `parent_entry_id` points at turn 1 (prefix match holds because canonical is
   original).
3. **Transcript diff attribution:** construct an entry where a transform
   modifies a message whose full-array `message_index` differs from its deduped
   position; assert the transcript message returns `modified_by=["word_count"]`
   and non-null `original_content` + `modified_content`.
4. **Disable stops new mods, history persists:** after disabling, new entries
   have no diffs; previously-modified turns still surface their diff in the
   transcript.
5. Update existing transcript / log-detail tests for the new `modified_content`
   field.

Frontend (`vitest` + Testing Library):
- `MessageBubble` renders the original→sent diff and the "modified by" label
  when `modified_content` is present; renders plain text otherwise.

---

## 8. Docs — Part F
- `docs/proxy.md` / `docs/plugins.md`: clarify that the **transcript shows your
  original messages**, with an overlay diff showing what an enabled transform
  actually sent upstream; modifications are **immutable history** — disabling a
  plugin does not un-modify past calls. Retain the existing streaming caveat.
- `OVERVIEW.md`: note canonical interning = **original** messages; transform
  diffs are an overlay (update the `message_diffs` / proxy description).

---

## 9. Verification
- `make check` (lint + tests) green.
- Manual repro of the report: enable word_count for a conversation, send a turn,
  disable it, send another turn. Confirm:
  - both turns are linked in a single trunk (no collapse);
  - the first turn shows the diff overlay + "modified by word_count";
  - the second turn shows no modification;
  - disabling did not retroactively change the first turn's recorded diff.

---

## 10. Known limitations / follow-ups
- The 8 existing entries in `44422dfd…` were interned with modified content and
  have NULL `parent_entry_id`; they will not retroactively link or render diffs
  without a one-off backfill migration. New conversations will be correct.
  Decide separately whether a backfill is worth it.
- The legacy `MessageModification` table/path is effectively unused by the proxy
  flow (0 rows). Consider removing it in a later cleanup; out of scope here.

---

## 11. File-change checklist
- [ ] `backend/app/services/openrouter_map.py` — intern original messages (A1)
- [ ] `backend/app/proxy/logging_sink.py` — verify interns original (A2)
- [ ] `backend/app/routers/logs.py` — message_id-keyed diff attribution +
      `modified_content` population (B1, C1)
- [ ] `backend/app/schemas/log_entry.py` — add `TranscriptMessage.modified_content` (C1)
- [ ] `frontend/src/lib/api.ts` — add `modified_content` to `TranscriptMessage` (C2)
- [ ] `frontend/src/routes/conversation.tsx` — render original→sent diff (C3)
- [ ] `backend/app/routers/plugins.py` — audit log lines (D1)
- [ ] `backend/tests/{proxy,api}/…` — tests (E)
- [ ] `frontend` vitest — MessageBubble test (E)
- [ ] `docs/proxy.md`, `docs/plugins.md`, `OVERVIEW.md` — docs (F)
