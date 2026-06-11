/**
 * Typed API client.
 * Automatically reads the CSRF cookie and attaches it as X-CSRF-Token
 * on mutating requests. Sends credentials (session cookie) on all requests.
 */

function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)lsd_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

type FetchOptions = RequestInit & { skipCsrf?: boolean };

async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { skipCsrf = false, ...fetchOpts } = options;
  const method = (fetchOpts.method ?? "GET").toUpperCase();
  const mutating = ["POST", "PUT", "PATCH", "DELETE"].includes(method);

  const headers = new Headers(fetchOpts.headers);
  headers.set("Content-Type", "application/json");

  if (mutating && !skipCsrf) {
    const token = getCsrfToken();
    if (token) headers.set("X-CSRF-Token", token);
  }

  const resp = await fetch(`/api/v1${path}`, {
    ...fetchOpts,
    credentials: "include",
    headers,
  });

  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new ApiError(resp.status, detail?.detail ?? resp.statusText);
  }

  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

// ─── Auth ────────────────────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    apiFetch<{ message: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  logout: () => apiFetch<{ message: string }>("/auth/logout", { method: "POST" }),

  me: () => apiFetch<UserPublic>("/auth/me"),

  csrf: () => apiFetch<{ csrf_token: string }>("/auth/csrf"),
};

// ─── Users ───────────────────────────────────────────────────────────────────

export const usersApi = {
  register: (data: { username: string; email?: string; password: string }) =>
    apiFetch<UserPublic>("/users", { method: "POST", body: JSON.stringify(data) }),

  updateMe: (data: { email?: string; password?: string }) =>
    apiFetch<UserPublic>("/users/me", { method: "PATCH", body: JSON.stringify(data) }),
};

// ─── API Keys ─────────────────────────────────────────────────────────────────

export const apiKeysApi = {
  list: () => apiFetch<ApiKeyPublic[]>("/api-keys"),

  create: (data: { name: string; scopes: string[] }) =>
    apiFetch<ApiKeyCreatedResponse>("/api-keys", { method: "POST", body: JSON.stringify(data) }),

  revoke: (id: string) => apiFetch<void>(`/api-keys/${id}`, { method: "DELETE" }),
};

// ─── Logs ─────────────────────────────────────────────────────────────────────

export const logsApi = {
  list: (params?: LogListParams) => {
    const q = new URLSearchParams();
    if (params?.conversation_id) q.set("conversation_id", params.conversation_id);
    if (params?.model) q.set("model", params.model);
    if (params?.provider) q.set("provider", params.provider);
    if (params?.since) q.set("since", params.since);
    if (params?.until) q.set("until", params.until);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    return apiFetch<LogEntryPublic[]>(`/logs?${q.toString()}`);
  },

  get: (id: string) => apiFetch<LogEntryDetail>(`/logs/${id}`),

  conversation: (conversationId: string) =>
    apiFetch<ConversationResponse>(`/conversations/${conversationId}`),

  transcript: (conversationId: string) =>
    apiFetch<TranscriptResponse>(`/conversations/${conversationId}/transcript`),

  stats: (opts?: { since?: string; until?: string; interval?: string; days?: number }) => {
    const q = new URLSearchParams();
    if (opts?.since) q.set("since", opts.since);
    if (opts?.until) q.set("until", opts.until);
    if (opts?.interval) q.set("interval", opts.interval);
    if (opts?.days && !opts?.since) q.set("days", String(opts.days));
    const qs = q.toString();
    return apiFetch<StatsResponse>(`/stats/summary${qs ? `?${qs}` : ""}`);
  },

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
};

// ─── Docs ─────────────────────────────────────────────────────────────────────

export const docsApi = {
  list: () => apiFetch<DocIndex[]>("/docs-md"),
  get: (path: string) =>
    fetch(`/api/v1/docs-md/${path}`, { credentials: "include" }).then((r) => r.text()),
};

// ─── Types ───────────────────────────────────────────────────────────────────

export interface UserPublic {
  id: string;
  username: string;
  email: string | null;
  is_active: boolean;
  created_at: string;
}

export interface ApiKeyPublic {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiKeyCreatedResponse extends ApiKeyPublic {
  raw_key: string;
}

export interface LogEntryPublic {
  id: string;
  user_id: string;
  conversation_id: string | null;
  api_key_id: string | null;
  api_key_name: string | null;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reasoning_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_total: number | null;
  cost_currency: string;
  cost_source: string;
  latency_ms: number | null;
  status: string;
  client_timestamp: string | null;
  created_at: string;
  modification_count: number;
  diff_count: number;
}

export interface ModificationPublic {
  id: string;
  plugin_name: string;
  target: string;
  message_index: number | null;
  message_role: string | null;
  summary: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface MessageDiffPublic {
  id: string;
  message_index: number;
  role: string | null;
  change_kind: string;
  original_content: Record<string, unknown> | null;
  final_content: Record<string, unknown> | null;
  modified_by: string[];
  created_at: string;
}

export interface LogEntryDetail extends LogEntryPublic {
  request: Record<string, unknown>;
  response: Record<string, unknown>;
  tool_calls: unknown[];
  error: string | null;
  metadata_extra: Record<string, unknown> & {
    compression?: {
      tokens_before: number;
      tokens_after: number;
      tokens_saved: number;
      compression_ratio: number;
      transforms_applied: string[];
    };
  };
  modifications: ModificationPublic[];
  request_diffs: MessageDiffPublic[];
}

export interface ConversationResponse {
  conversation_id: string;
  entries: LogEntryDetail[];
  total_tokens: number;
  total_cost: number | null;
}

export interface TranscriptMessage {
  message_id: string;
  role: string;
  content: string | unknown[];
  reasoning?: string | null;
  reasoning_details?: unknown[] | null;
  introduced_by_entry_id: string | null;
  introduced_by_call_index: number | null;
  modified_by: string[];
  original_content?: string | unknown[] | null;
  modified_content?: string | unknown[] | null;
}

export interface CallDivider {
  entry_id: string;
  call_index: number;
  model: string;
  provider: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reasoning_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_total: number | null;
  latency_ms: number | null;
  status: string;
  created_at: string;
  modification_count: number;
  modifications: ModificationPublic[];
  diff_count: number;
  diffs: MessageDiffPublic[];
}

export interface TranscriptBranch {
  branch_id: string;
  messages: TranscriptMessage[];
  dividers: CallDivider[];
}

export interface CompressionSummary {
  tokens_before: number;
  tokens_after: number;
  tokens_saved: number;
  compression_ratio: number;
  calls_with_compression: number;
}

export interface TranscriptResponse {
  conversation_id: string;
  trunk: TranscriptMessage[];
  branches: TranscriptBranch[];
  dividers: CallDivider[];
  total_tokens: number;
  total_cost: number | null;
  is_branched: boolean;
  compression: CompressionSummary | null;
}

export interface StatsResponse {
  total_calls: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_reasoning_tokens: number;
  total_cache_read_tokens: number;
  total_cache_write_tokens: number;
  total_tokens_saved: number;
  total_cost: number | null;
  interval: string;
  since: string | null;
  until: string | null;
  by_day: {
    date: string;
    calls: number;
    total_tokens: number;
    reasoning_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    tokens_saved: number;
    cost: number | null;
  }[];
  by_model: {
    model: string;
    calls: number;
    total_tokens: number;
    reasoning_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    tokens_saved: number;
    cost: number | null;
  }[];
}

export interface LogListParams {
  conversation_id?: string;
  model?: string;
  provider?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface ConversationSummary {
  conversation_id: string;
  call_count: number;
  total_tokens: number;
  total_cost: number | null;
  tokens_saved: number;
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

export interface DocIndex {
  path: string;
  title: string;
}

// ─── Plugin types ──────────────────────────────────────────────────────────

export interface PluginInfo {
  name: string;
  description: string;
  default_enabled: boolean;
  locked: boolean;
  user_enabled: boolean | null;
}

export interface ConversationPluginState {
  name: string;
  description: string;
  locked: boolean;
  global_enabled: boolean | null;
  override_enabled: boolean | null;
  effective: boolean;
}

export const pluginsApi = {
  list: () => apiFetch<PluginInfo[]>("/plugins"),

  setGlobal: (name: string, enabled: boolean) =>
    apiFetch<PluginInfo>(`/plugins/${name}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  listConversationPlugins: (conversationId: string) =>
    apiFetch<ConversationPluginState[]>(`/conversations/${conversationId}/plugins`),

  setConversationOverride: (conversationId: string, name: string, enabled: boolean) =>
    apiFetch<ConversationPluginState>(`/conversations/${conversationId}/plugins/${name}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  deleteConversationOverride: (conversationId: string, name: string) =>
    apiFetch<void>(`/conversations/${conversationId}/plugins/${name}`, {
      method: "DELETE",
    }),
};
