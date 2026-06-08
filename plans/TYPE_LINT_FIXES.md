# Type / Lint / Test Error Report & Fix Plan

Generated from a full `make check`-equivalent run. This document lists every
failing check and a precise fix for a follow-up model to apply.

## Summary of current state

| Check | Command | Result |
|-------|---------|--------|
| Backend ruff | `cd backend && uv run ruff check .` | ✅ **Pass** (only harmless removed-rule warnings for `ANN101`/`ANN102`) |
| Backend type check (`ty`) | `cd backend && uv run ty check app` | ❌ **37 errors + 20 deprecation warnings** (57 diagnostics) |
| Backend tests | `cd backend && uv run pytest` | ✅ **132 passed** (but 444 warnings; 2 tests mis-use `return`) |
| Frontend eslint | `cd frontend && pnpm lint` | ✅ **Pass** |
| Frontend prettier | `cd frontend && pnpm fmt:check` | ✅ **Pass** |
| Frontend tsc | `cd frontend && pnpm typecheck` | ✅ **Pass** |
| Frontend tests | `cd frontend && pnpm test` | ✅ **25 passed** |

**Only the backend `ty` type check fails the build.** Everything else passes.
The two pytest `return`-not-`None` warnings are not failures but should be
cleaned up. The `utcnow()` deprecations are warnings (not errors) but are
worth fixing in one sweep since they account for the bulk of the noise and
will eventually break.

`[tool.ty]` in `backend/pyproject.toml` is empty, so every rule runs at default
severity. The established pattern in this codebase for SQLModel/SQLAlchemy
false positives is an inline `# type: ignore[<rule>]` comment (see existing
usages in `api_keys.py:42`, `auth.py:42`, `main.py:130`, and the existing
`# type: ignore` lines in `messages.py`/`logs.py`/`stats.py`). Follow that
same convention rather than weakening global config.

---

## Group A — Real type bugs (fix the code, not with `# type: ignore`)

These are genuine type problems worth fixing properly.

### A1. `app/routers/api_keys.py:42` — `invalid-return-type`
```
error[invalid-return-type]: Return type does not match returned value
 --> app/routers/api_keys.py:42:6
```
`list_keys` is annotated `-> list[ApiKey]` but returns
`db.exec(...).all()` whose inferred type is `Sequence[ApiKey]`, and the line
already carries `# type: ignore[return-value]` which `ty` does not honor here
(that's a mypy-style code).
**Fix:** change the return annotation to `Sequence[ApiKey]`
(`from collections.abc import Sequence`) and `return list(db.exec(...).all())`,
or wrap the result in `list(...)`. Then drop the stale
`# type: ignore[return-value]`. Confirm `response_model=list[ApiKeyPublic]`
still serializes correctly (it will).

### A2. `app/routers/auth.py:43` — `unresolved-attribute` on `None`
```
error[unresolved-attribute]: Attribute `csrf_secret` is not defined on `None` in union `UserSession | None`
 --> app/routers/auth.py:43:33
```
`sess = db.get(_UserSession, ...)` can be `None`; `sess.csrf_secret` is then
unsafe. The line has `# type: ignore[union-attr]` which `ty` ignores.
**Fix:** add an explicit guard:
```python
sess = db.get(_UserSession, _uuid.UUID(raw_sid)) if raw_sid else None
if sess is None:
    raise HTTPException(status_code=401, detail="No active session")
token = generate_csrf_token(sess.csrf_secret)
```
Remove the now-unnecessary `# type: ignore[arg-type]` / `[union-attr]`
comments.

### A3. `app/proxy/plugins/word_count.py` — content typing (4 errors)
```
error[invalid-argument-type]   word_count.py:38:63  -> dict.get with non-str key
error[invalid-argument-type]   word_count.py:39:30  -> __getitem__ "text" on object
error[no-matching-overload]    word_count.py:40:16  -> str.join over object
error[invalid-argument-type]   word_count.py:66:39  -> _count_words bad arg
```
Root cause: `_get_text_content(content: str | list | object)` and the loop
body treat `part` as `object`, so `part.get("text")` / `part["text"]` /
`" ".join(texts)` all fail type inference, and `target_content` (typed
`object` from `messages[i].get("content", "")`) is passed to
`_count_words(content: str | list)`.
**Fix:**
- In `_get_text_content`, narrow the element type explicitly:
  ```python
  texts: list[str] = []
  for part in content:
      if isinstance(part, dict):
          t = part.get("text")
          if isinstance(t, str):
              texts.append(t)
  return " ".join(texts)
  ```
  (This removes the `object`-typed `part["text"]` access.)
- Change the `_count_words` / `_get_text_content` call sites so the value
  passed is narrowed. `messages[target_idx].get("content", "")` is inferred as
  `object`; cast/narrow it: assign `content_val = messages[target_idx].get("content", "")`
  and guard with `if isinstance(content_val, (str, list)): ...`, or annotate
  the helper params as `str | list | object` consistently and add the
  `isinstance` narrowing inside. Make `_count_words` accept
  `content: str | list | object` and handle the fallthrough.
- Verify `tests/proxy/test_proxy_*.py` word-count assertions still pass.

### A4. `app/services/openrouter_map.py:230` — `invalid-argument-type`
```
error[invalid-argument-type]: Expected `list[ToolCall]`, found `list[dict] | list[ToolCall]`
 --> app/services/openrouter_map.py:230:9  (tool_calls=tool_calls)
```
`tool_calls` is built as either a `list[dict]` or `list[ToolCall]` depending on
a branch, then passed where `list[ToolCall]` is required.
**Fix:** normalize the variable to a single type before the call — build a
`list[ToolCall]` in both branches (construct `ToolCall(**d)` from the dict
branch), or annotate the accumulator `tool_calls: list[ToolCall] = []` and
append only `ToolCall` instances. Read lines ~200–235 to see which branch
produces dicts and convert them.

### A5. `app/routers/plugins.py:207` — `invalid-argument-type`
```
error[invalid-argument-type]: Argument is incorrect
 --> app/routers/plugins.py:207:17
```
**Fix:** read the surrounding function; likely a value passed to a SQLModel
constructor or service call whose declared type is narrower than the value.
Inspect line 207 and the called signature, then either narrow/convert the
argument or correct the annotation. Add a targeted `# type: ignore[...]` only
if it is a SQLModel column false-positive (see Group B).

### A6. `app/services/conversation_identity.py:48` — `invalid-argument-type`
```
error[invalid-argument-type]: Argument to bound method `dict.get` is incorrect
 --> app/services/conversation_identity.py:48:30
```
Same shape as A3 — a `dict.get(key)` where `key` is inferred as `Never`/wrong
type, usually because the dict's value type is over-narrowed.
**Fix:** read the dict's construction/annotation around line 48; annotate it as
`dict[str, Any]` (or the correct key type) so `.get()` accepts the key.

---

## Group B — SQLModel / SQLAlchemy column-expression false positives

`ty` does not understand that SQLModel class attributes (e.g. `LogEntry.id`,
`Message.content_hash`, `LogEntry.created_at`) are `InstrumentedAttribute`
column expressions, so it sees the *Python* field type (`UUID`, `str`,
`datetime`) and reports `.in_`, `.desc`, `.ilike`, `.is_not`, `.label`,
`order_by(...)`, `group_by(...)` as invalid. These are **not real bugs** — the
runtime + tests pass. Follow the existing in-file convention: add
`# type: ignore[unresolved-attribute]` (or `[invalid-argument-type]` /
`[no-matching-overload]` as appropriate) on each flagged line.

> Note: several of these lines *already* have `# type: ignore[attr-defined]`
> comments (mypy codes) that `ty` does not recognize. Replace/augment them with
> `ty`'s codes. Confirm the exact code `ty` emits per line from the table below.

### B1. `app/routers/logs.py` (12 diagnostics)
| Line | Code | Expression |
|------|------|-----------|
| 251 | `unresolved-attribute` | `LogEntry.created_at.desc()` |
| 258 | `unresolved-attribute` | `LogEntry.id.in_(...)` |
| 270 | `invalid-argument-type` | `func.count(...)` arg |
| 272 | `unresolved-attribute` | `<col>.in_(...)` |
| 273 | `invalid-argument-type` | `.group_by(...)` |
| 275 | `unresolved-attribute` | `row.log_entry_id` (tuple unpack) |
| 275 | `unresolved-attribute` | `row.cnt` (tuple unpack) |
| 330 | `unresolved-attribute` | `<str\|None col>.is_not(...)` |
| 337 | `unresolved-attribute` | `<col>.ilike(...)` |
| 344 | `unresolved-attribute` | `<col>.is_not(...)` |
| 351 | `unresolved-attribute` | `<col>.in_(...)` |
| 355 | `no-matching-overload` | `select(...)` |
| 356 | `unresolved-attribute` | `<col>.label(...)` |
| 422 | `invalid-argument-type` | `.order_by(...)` |
| 474 | `invalid-argument-type` | `.order_by(...)` |
| 490 | `unresolved-attribute` | `LogEntry.id.in_(...)` |

The `row.log_entry_id` / `row.cnt` cases (line 275) are tuple-row access on a
`select(col, func.count())` result. If you want to avoid `# type: ignore`,
unpack the row explicitly: `for log_entry_id, cnt in rows:`. Otherwise
`# type: ignore[unresolved-attribute]`.

### B2. `app/services/messages.py` (6 diagnostics)
| Line | Code | Expression |
|------|------|-----------|
| 70  | `invalid-context-manager` | `with raw_conn.cursor() as cur:` |
| 91  | `unresolved-attribute` | `Message.content_hash.in_(hashes)` |
| 119 | `unresolved-attribute` | `Message.id.in_(message_ids)` |
| 143 | `unresolved-attribute` | `Message.id.in_(list(all_ids))` |
| 188 | `unresolved-attribute` | `LogEntry.created_at.desc()` |
| 196 | `unresolved-attribute` | `candidates.sort(...)` |

- Lines 91/119/143/188: replace stale `# type: ignore[attr-defined]` with the
  code `ty` reports (`unresolved-attribute`).
- Line 70 (`raw_conn.cursor()` context manager): the DBAPI cursor lacks typed
  `__enter__`/`__exit__`. Add `# type: ignore[invalid-context-manager]`.
- Line 196 (`candidates.sort`): `candidates` is a `Sequence` (from
  `db.exec(...).all()`), which has no `.sort`. **Real-ish fix:** make it a list
  first — `candidates = list(db.exec(stmt).all())` — then `.sort()` is valid and
  no ignore is needed.

### B3. `app/services/stats.py:18` and `app/services/cost.py:25`
| File:Line | Code | Expression |
|-----------|------|-----------|
| `stats.py:18` | `invalid-argument-type` | `.order_by(LogEntry.created_at)` |
| `cost.py:25`  | `unresolved-attribute` | `<datetime col>.desc()` |

`stats.py:18` already has `# type: ignore[arg-type]`; switch it to the `ty`
code `# type: ignore[invalid-argument-type]`. Add `# type: ignore[unresolved-attribute]`
to `cost.py:25`.

### B4. `app/routers/health.py:20`
```
error[no-matching-overload]: No overload of bound method `Session.exec` matches arguments
 --> app/routers/health.py:20:5
```
This is the readiness probe `SELECT 1`. Likely `db.exec(select(1))` or
`db.exec(text("SELECT 1"))`.
**Fix:** if using a raw `text("SELECT 1")`, wrap with
`# type: ignore[no-matching-overload]`, or use `db.exec(select(func.count()))`
shape that `ty` accepts. Prefer the targeted ignore here since it's a trivial
health check.

---

## Group C — `datetime.utcnow()` deprecation warnings (20 occurrences)

```
warning[deprecated]: The function `utcnow` is deprecated
```
Not build-failing today, but `datetime.utcnow()` is removed in future Python.
Replace every `datetime.utcnow()` with a timezone-aware UTC `now`.

**Recommended approach:** add a small helper (e.g. in `app/utils/time.py` or
reuse an existing util module):
```python
from datetime import datetime, timezone

def utcnow() -> datetime:
    return datetime.now(timezone.utc)
```
Then replace call sites. **Caution:** several of these feed SQLModel
`default_factory=datetime.utcnow` for columns that are likely
`TIMESTAMP WITHOUT TIME ZONE`. Switching to timezone-aware datetimes can change
stored values / comparisons. Before doing this sweep:
1. Check the column definitions and existing Alembic migrations to see whether
   columns are `timezone=True`.
2. If columns are naive, either keep naive (`datetime.now(timezone.utc).replace(tzinfo=None)`)
   or migrate columns to `timezone=True` (new Alembic migration) — pick one and
   apply consistently.
3. Re-run `pytest`; the time-comparison logic in `sessions.py:64`
   (`sess.expires_at < utcnow()`) must keep working.

Locations:
- `app/models/api_key.py:26`
- `app/models/log_entry.py:82`
- `app/models/message.py:34`
- `app/models/message_modification.py:37`
- `app/models/model_price.py:18`
- `app/models/plugin_config.py:27, 29, 30, 55, 57, 58`
- `app/models/session.py:15`
- `app/models/user.py:23, 24`
- `app/routers/api_keys.py:106`
- `app/routers/users.py:85`
- `app/security/api_key_auth.py:94`
- `app/security/sessions.py:24, 64`
- `app/services/stats.py:14`

If a full timezone migration is out of scope, at minimum centralize the helper
and silence the warning consistently; do **not** scatter `# type: ignore`
across all 20 — fix the source.

---

## Group D — `app/routers/logs.py:372,386` — deprecated `Session.execute`

```
warning[deprecated]: The function `execute` is deprecated
 --> app/routers/logs.py:372 and :386
```
SQLModel deprecates `Session.execute` in favor of `Session.exec`.
**Fix:** switch `db.execute(stmt)` → `db.exec(stmt)` where the statement is a
SQLModel `select`. If these use raw SQLAlchemy `text()`/Core constructs that
require `execute`, leave them and add a targeted `# type: ignore[deprecated]`,
but prefer converting to `exec` if the surrounding query is a `select`.

---

## Group E — `app/main.py:130` — exception handler arg type

```
error[invalid-argument-type]: Argument to bound method `Starlette.add_exception_handler` is incorrect
 --> app/main.py:130:50
```
`app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)`
— slowapi's handler signature doesn't match Starlette's expected
`Callable[[Request, Exception], Response]`. The line already has
`# type: ignore[arg-type]` (mypy code) which `ty` ignores.
**Fix:** replace with `# type: ignore[invalid-argument-type]` (the `ty` code).
This is a known slowapi/Starlette typing mismatch — an ignore is appropriate.

---

## Group F — pytest hygiene (not failures, but should fix)

```
PytestReturnNotNoneWarning: Test functions should return None, but
  tests/api/test_api_keys.py::test_create_key returned <class 'dict'>
  tests/api/test_logs.py::test_ingest_success returned <class 'dict'>
```
**Fix:** these tests end with `return <something>` instead of `assert`. Change
the trailing `return data` to either nothing or `assert data is not None`.
Read each test and remove the `return`.

---

## Suggested execution order

1. **Group A** (real bugs) — A1, A2, A3, A4, A6 are clear; A5 needs a quick
   read of `plugins.py:207`.
2. **Group B** (SQLModel ignores) — apply the correct `ty` ignore codes; do the
   `list(...)` fix for `messages.py:196` and tuple-unpack for `logs.py:275`
   rather than ignores where easy.
3. **Group E** + **Group D** — one-liners.
4. **Group C** (utcnow) — do as one deliberate change after confirming the
   timezone strategy; this is the largest and riskiest sweep.
5. **Group F** — test cleanup.
6. Re-run `make check` and confirm zero `ty` diagnostics and clean pytest.

## Verification

```bash
make lint        # ruff + ty (backend); eslint + prettier + tsc (frontend)
make test        # pytest + vitest
make check       # both
```
Target end state: `ty check app` reports **0 errors** (warnings ideally 0 after
Group C), pytest shows **0** PytestReturnNotNoneWarning, all suites green.
