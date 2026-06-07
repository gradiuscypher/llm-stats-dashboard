# Plan: Conversations List Page

## Goal

Add a **Conversations** page that shows logs grouped by `conversation_id` (one row
per conversation) instead of one row per individual call. It sits next to **Logs**
in the nav. Each row links to the existing transcript view at
`/conversations/$conversationId` (already implemented in `frontend/src/routes/conversation.tsx`).

Scope decisions (confirmed):
- Rich list with filters (model / provider / date range), search by `conversation_id`,
  pagination, and sortable columns.
- Backed by a **new backend aggregation endpoint** (`GET /conversations`).
- The transcript detail page and `/conversations/{id}/transcript` endpoint already
  exist — do **not** modify them. Only add the new list endpoint and the new page.
- There is **no** "reviews" feature; ignore any earlier mention of reviews.

---

## Part 1 — Backend: aggregation endpoint

### 1.1 New schema — `ConversationSummary` and `ConversationListResponse`

File: `backend/app/schemas/log_entry.py`

Add after `ConversationResponse`:

```python
class ConversationSummary(BaseModel):
    """One row in the conversations list — aggregate over all calls in a conversation."""
    conversation_id: str
    call_count: int
    total_tokens: int
    total_cost: float | None
    # Distinct models/providers seen in this conversation (sorted, deduped)
    models: list[str]
    providers: list[str]
    # Whether any call in the conversation errored
    has_error: bool
    first_activity: datetime   # earliest created_at
    last_activity: datetime    # latest created_at


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    # Total distinct conversations matching the filters (for pagination UI)
    total: int
```

Note: `total` is the count of matching conversation groups, not log rows.

### 1.2 New endpoint — `GET /conversations`

File: `backend/app/routers/logs.py`

Add a route **before** the existing `/conversations/{conversation_id}` route to avoid
path-collision ambiguity (FastAPI matches in declaration order; `/conversations` is a
distinct path so order is not strictly required, but declare it adjacent for clarity).

Behavior:
- Auth: reuse `_resolve_user(request, db)` (session cookie OR API key with `logs:read`),
  same as the other read endpoints.
- Only aggregate the current user's entries (`LogEntry.user_id == user.id`).
- Exclude rows where `conversation_id IS NULL` (those can't be grouped).
- Filters (all optional, mirror `list_logs`):
  - `model: str | None` — include the conversation if **any** call used this model.
  - `provider: str | None` — include if any call used this provider.
  - `conversation_id: str | None` — substring/`ILIKE` match for search box
    (use `LogEntry.conversation_id.ilike(f"%{conversation_id}%")`).
  - `since: datetime | None`, `until: datetime | None` — filter on `created_at`.
- Sorting via `sort` + `order` query params:
  - `sort` in `{"last_activity", "first_activity", "total_tokens", "total_cost", "call_count"}`,
    default `"last_activity"`.
  - `order` in `{"asc", "desc"}`, default `"desc"`.
- Pagination: `limit` (1..500, default 50), `offset` (>=0, default 0). Paginate over
  **conversation groups**, not log rows.

Recommended implementation — single aggregated SQL query using SQLModel/SQLAlchemy:

```python
from sqlalchemy import func

@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    db: Session = Depends(get_session),
    conversation_id: str | None = Query(default=None),
    model: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    sort: str = Query(default="last_activity"),
    order: str = Query(default="desc"),
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: int = Query(default=0, ge=0),
) -> ConversationListResponse:
    user = await _resolve_user(request, db)

    # --- base filter (applied to per-call rows BEFORE grouping) ---
    base = select(LogEntry).where(
        LogEntry.user_id == user.id,
        LogEntry.conversation_id.is_not(None),
    )
    if since:
        base = base.where(LogEntry.created_at >= since)
    if until:
        base = base.where(LogEntry.created_at <= until)
    if conversation_id:
        base = base.where(LogEntry.conversation_id.ilike(f"%{conversation_id}%"))

    # model/provider are "any call in conversation" semantics. The cleanest
    # approach: first find the set of conversation_ids that contain a matching
    # call, then aggregate over the full set of calls in those conversations.
    matching_conv_ids_subq = None
    if model or provider:
        m = select(LogEntry.conversation_id).where(
            LogEntry.user_id == user.id,
            LogEntry.conversation_id.is_not(None),
        )
        if model:
            m = m.where(LogEntry.model == model)
        if provider:
            m = m.where(LogEntry.provider == provider)
        matching_conv_ids_subq = m.distinct().subquery()
        base = base.where(
            LogEntry.conversation_id.in_(select(matching_conv_ids_subq.c.conversation_id))
        )

    # --- aggregate ---
    agg = (
        select(
            LogEntry.conversation_id.label("conversation_id"),
            func.count().label("call_count"),
            func.coalesce(func.sum(LogEntry.total_tokens), 0).label("total_tokens"),
            func.sum(LogEntry.cost_total).label("total_cost"),  # NULL if all costs NULL
            func.min(LogEntry.created_at).label("first_activity"),
            func.max(LogEntry.created_at).label("last_activity"),
            func.bool_or(LogEntry.status == "error").label("has_error"),
            func.array_agg(LogEntry.model.distinct()).label("models"),
            func.array_agg(LogEntry.provider.distinct()).label("providers"),
        )
        .where(*base.whereclause)  # reuse same filters; see note below
        .group_by(LogEntry.conversation_id)
    )
```

Implementation notes / pitfalls:
- Postgres-specific functions used: `func.bool_or(...)`, `func.array_agg(... .distinct())`.
  These are fine — the project already targets Postgres (JSONB, ARRAY columns).
- `func.array_agg(LogEntry.model.distinct())` returns a list with possible `None`s only
  if model could be NULL (it can't — `model` is non-null). Sort/dedupe in Python after
  fetch to be safe: `sorted(set(row.models))`.
- Cleaner than `*base.whereclause`: build the WHERE conditions as a `list[...]` of
  SQLAlchemy expressions once, then apply the same list to both the base/count query
  and the aggregate query via `.where(*conditions)`. Refactor accordingly — do not rely
  on `base.whereclause` unpacking.
- Total count of groups for pagination:
  ```python
  count_subq = agg.subquery()
  total = db.exec(select(func.count()).select_from(count_subq)).one()
  ```
  (Run this BEFORE applying limit/offset/order to `agg`.)
- Sorting: map `sort` → the labeled column; apply `.asc()`/`.desc()`; then
  `.offset(offset).limit(limit)`. Validate `sort`/`order` against allow-lists and
  fall back to defaults on unknown values (do not 400 — be lenient).
- Build `ConversationSummary` rows from the result, converting `total_cost` with
  `round(x, 8)` when not None, and `models`/`providers` via `sorted(set(...))`.

### 1.3 Tests

File: `backend/tests/api/test_logs.py`

Mirror existing patterns (`auth_client`, `write_key`, `_VALID_LOG`). Add:
- `test_list_conversations_groups_calls`: ingest 2+ calls with the same
  `conversation_id` and one call with a different `conversation_id`; assert
  `GET /api/v1/conversations` returns 2 groups with correct `call_count`,
  `total_tokens`, summed `total_cost`, and `models`/`providers` deduped.
- `test_list_conversations_filter_model`: filter by `?model=` and assert only
  conversations containing that model are returned (and their full call counts,
  i.e. "any call matches" semantics).
- `test_list_conversations_search`: `?conversation_id=` substring match.
- `test_list_conversations_excludes_null_conversation`: a log with no
  `conversation_id` does not appear.
- `test_list_conversations_pagination`: `limit`/`offset` over groups + `total` is
  the full group count.
- `test_list_conversations_sort`: `?sort=total_tokens&order=asc` orders correctly.

Run: `cd backend && uv run pytest tests/api/test_logs.py -q` (or use the Makefile
target if one exists — check `Makefile`).

---

## Part 2 — Frontend: API client

File: `frontend/src/lib/api.ts`

### 2.1 Types

Add near the other log types:

```ts
export interface ConversationSummary {
  conversation_id: string;
  call_count: number;
  total_tokens: number;
  total_cost: number | null;
  models: string[];
  providers: string[];
  has_error: boolean;
  first_activity: string;
  last_activity: string;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
}

export interface ConversationListParams {
  conversation_id?: string;
  model?: string;
  provider?: string;
  since?: string;
  until?: string;
  sort?: "last_activity" | "first_activity" | "total_tokens" | "total_cost" | "call_count";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
```

### 2.2 Client method

Add to `logsApi` (the existing `conversation`/`transcript` singular-fetch methods stay):

```ts
listConversations: (params?: ConversationListParams) => {
  const q = new URLSearchParams();
  if (params?.conversation_id) q.set("conversation_id", params.conversation_id);
  if (params?.model) q.set("model", params.model);
  if (params?.provider) q.set("provider", params.provider);
  if (params?.since) q.set("since", params.since);
  if (params?.until) q.set("until", params.until);
  if (params?.sort) q.set("sort", params.sort);
  if (params?.order) q.set("order", params.order);
  if (params?.limit) q.set("limit", String(params.limit));
  if (params?.offset) q.set("offset", String(params.offset));
  return apiFetch<ConversationListResponse>(`/conversations?${q.toString()}`);
},
```

---

## Part 3 — Frontend: Conversations list page

### 3.1 New route file

File: `frontend/src/routes/conversations.tsx` (NEW — note plural, distinct from the
existing singular `conversation.tsx` which is the transcript detail page).

Model it closely on `frontend/src/routes/logs.tsx` (same imports: `Layout`,
`PageHeader`, `StatusBadge`, `Field`, `Button`, same `fmtCost`/`fmtDate` helpers, same
pagination structure with `PAGE_SIZES`). Component export: `ConversationsPage`.

State:
- `model`, `provider`, `conversationId` (search box), `offset`, `pageSize`.
- `sort` (default `"last_activity"`), `order` (default `"desc"`).

Query:
```ts
const { data, isLoading } = useQuery({
  queryKey: ["conversations", { model, provider, conversationId, sort, order, offset, pageSize }],
  queryFn: () => logsApi.listConversations({
    model: model || undefined,
    provider: provider || undefined,
    conversation_id: conversationId || undefined,
    sort, order, limit: pageSize, offset,
  }),
});
const rows = data?.conversations ?? [];
const total = data?.total ?? 0;
```

Filters row: reuse the three `Field` inputs from `logs.tsx` (Model, Provider,
Conversation ID search). Reset `offset` to 0 on any filter change.

Table columns:
| Column | Source | Notes |
|--------|--------|-------|
| Conversation | `conversation_id` | `Link` to `/conversations/$conversationId` (transcript) |
| Calls | `call_count` | tabular-nums |
| Models | `models` | join with `, `; if long, show first + `+N` |
| Providers | `providers` | faint text |
| Tokens | `total_tokens` | `.toLocaleString()` |
| Cost | `total_cost` | `fmtCost` |
| Last activity | `last_activity` | `fmtDate` |
| Status | `has_error` | `<StatusBadge status={has_error ? "error" : "ok"} />` |

Make column headers for Calls / Tokens / Cost / Last activity clickable to toggle
`sort` + `order` (clicking the active sort column flips `order`). Show a small ▲/▼
indicator on the active column. Keep it lightweight — no new component needed.

Pagination: copy the block from `logs.tsx` but use `total` for the range display
(`start`–`end` of `total`) and disable Next when `offset + pageSize >= total`.

Empty state: "No conversations found." with `colSpan` matching column count.

### 3.2 Register the route

File: `frontend/src/router.tsx`

- Import: `import { ConversationsPage } from "@/routes/conversations";`
- Add a route:
  ```ts
  const conversationsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/conversations",
    beforeLoad: requireAuth,
    component: ConversationsPage,
  });
  ```
  IMPORTANT: TanStack Router must not confuse `/conversations` (list) with the
  existing `/conversations/$conversationId` (transcript). Both can coexist — static
  `/conversations` and param `/conversations/$conversationId` are distinct routes.
  Add `conversationsRoute` to `routeTree = rootRoute.addChildren([... , conversationsRoute, ...])`.

### 3.3 Add to nav

File: `frontend/src/components/Layout.tsx`

Insert into `NAV_LINKS` right after the Logs entry:
```ts
{ to: "/logs", label: "Logs" },
{ to: "/conversations", label: "Conversations" },
```

Note: the `[&.active]` styling uses exact-active by default in TanStack; verify the
Conversations link isn't marked active when on a transcript detail page
(`/conversations/$id`). If it is, pass `activeOptions={{ exact: true }}` to that `Link`
(or to all nav links) so only the exact list path highlights.

---

## Part 4 — Verification

Backend:
- `cd backend && uv run pytest tests/api/test_logs.py -q` (check `Makefile` for the
  canonical test target first).

Frontend:
- `cd frontend && npm run lint && npm run build` (or the Makefile equivalents).
- Manual: log in, open **Conversations**, confirm grouping, filters, sorting,
  pagination, and that clicking a row opens the existing transcript view.

---

## Out of scope (do not touch)
- The existing transcript endpoint `/conversations/{id}/transcript` and
  `conversation.tsx` page.
- The existing `/conversations/{id}` `ConversationResponse` endpoint.
- The `/logs` list page and endpoint (left as the per-call view).
- Any "reviews" feature (does not exist; not part of this work).

## File change summary
- `backend/app/schemas/log_entry.py` — add `ConversationSummary`, `ConversationListResponse`.
- `backend/app/routers/logs.py` — add `GET /conversations` (`list_conversations`).
- `backend/tests/api/test_logs.py` — add list-conversations tests.
- `frontend/src/lib/api.ts` — add types + `logsApi.listConversations`.
- `frontend/src/routes/conversations.tsx` — NEW list page (`ConversationsPage`).
- `frontend/src/router.tsx` — register `/conversations` route.
- `frontend/src/components/Layout.tsx` — add nav link.
