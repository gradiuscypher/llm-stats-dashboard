# Thinking Block Duplication Fix Plan

## Problem

On an individual conversation page (`/conversations/$id` → `frontend/src/routes/conversation.tsx`),
the assistant "Thinking" block shows the reasoning content **twice**. It is most noticeable on the
final thinking block, but the duplication happens for any assistant turn whose reasoning is plain
text.

## Root cause

OpenRouter (and our proxy assembler that mirrors it) returns reasoning in **two redundant forms**
on the same assistant message:

1. `reasoning` — a flat concatenated text string.
2. `reasoning_details` — a list of structured blocks. For plain-text reasoning these are
   `{"type": "reasoning.text", "text": "..."}` blocks whose concatenated `text` equals the
   `reasoning` string. (Encrypted/redacted reasoning instead comes as
   `reasoning.encrypted` / `reasoning.redacted` blocks.)

Evidence:
- `backend/app/proxy/assembler.py` (~L60–125) accumulates `_reasoning_parts` (→ `reasoning`) and
  `_reasoning_details` (→ `reasoning_details`) independently and emits **both** into the assembled
  message.
- `backend/tests/proxy/test_proxy_api.py` (~L266–270) and `test_proxy_unit.py` show the same content
  living in both `reasoning` and a `reasoning.text` detail block.
- `backend/app/routers/logs.py` (~L66–83) passes both `reasoning` and `reasoning_details` straight
  through to the transcript message.

The frontend `ReasoningBlock` in `frontend/src/routes/conversation.tsx` (~L90–155) renders **both**:

```tsx
{hasReasoning && <p>{reasoning}</p>}          // (1) flat reasoning
{hasDetails && reasoning_details.map(... <p>{block.text}</p> ...)}  // (2) per-block text
```

So for plain-text reasoning the user sees the text once from (1) and again from (2) → duplication.

## Fix strategy

Render reasoning **once**, preferring the richer `reasoning_details` when present and falling back
to the flat `reasoning` string otherwise. The fix is frontend-only; the backend correctly preserves
both fields (encrypted blocks only exist in `reasoning_details`, so we must not drop that field).

### Chosen approach: frontend dedup in `ReasoningBlock`

Edit `frontend/src/routes/conversation.tsx`:

- When `reasoning_details` has at least one renderable block (a `reasoning.text` block with text,
  or an encrypted/redacted block), render **only** the details and **suppress** the flat
  `reasoning` `<p>`.
- Only render the flat `reasoning` string when there are no usable `reasoning_details` blocks.
- Keep the `(<n> chars)` count in the header (it can keep using `reasoning.length` when available,
  otherwise derive from the joined detail text — minor, optional).

Concretely:

```tsx
const detailBlocks = hasDetails
  ? reasoning_details!.filter(
      (b) =>
        typeof b === "object" &&
        b !== null &&
        (((b as Record<string, unknown>).type as string)?.includes("encrypted") ||
          ((b as Record<string, unknown>).type as string)?.includes("redacted") ||
          typeof (b as Record<string, unknown>).text === "string"),
    )
  : [];
const useDetails = detailBlocks.length > 0;
```

Then in the expanded body:

```tsx
{useDetails
  ? detailBlocks.map((block, i) => { /* existing encrypted/text rendering */ })
  : hasReasoning && <p>{reasoning}</p>}
```

This guarantees content shows exactly once, while still surfacing encrypted/redacted placeholders
that only exist in `reasoning_details`.

### Edge cases to preserve

- **Encrypted/redacted only** (no flat `reasoning`): still shows `[reasoning.encrypted reasoning block]`
  placeholder via the details branch. ✓
- **Flat reasoning only** (no details): shows the flat string via the fallback. ✓
- **Mixed details** (some text + an encrypted block): all rendered from details, flat suppressed —
  no dupes, encrypted placeholder still shown. ✓
- **Char count header**: if `reasoning` is absent but details exist, optionally compute the count
  from joined detail `text`. Low priority.

## Files to change

- `frontend/src/routes/conversation.tsx` — update `ReasoningBlock` rendering logic only.

## Tests / verification

- **Frontend unit test** (vitest) for `ReasoningBlock` (or a small extracted pure helper that
  decides what to render), covering:
  1. `reasoning` + matching `reasoning.text` detail → content rendered once.
  2. `reasoning` only → rendered once.
  3. `reasoning.encrypted` detail only → placeholder shown, no crash.
  4. text detail + encrypted detail → both shown once each.
  - If `ReasoningBlock` is hard to test directly, extract a `selectReasoningRender(reasoning, details)`
    pure helper and unit-test that.
- **Manual check**: load a conversation with a reasoning model turn; confirm the final Thinking
  block expands to a single copy of the reasoning text.
- Run `make lint-frontend` and `make check`.

## Out of scope / explicitly NOT doing

- Do **not** change the backend assembler or stored data — both fields are intentionally preserved
  (encrypted reasoning only lives in `reasoning_details`, and keeping `reasoning` is useful for
  search/length). The duplication is purely a presentation concern.
