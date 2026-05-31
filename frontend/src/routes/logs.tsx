import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi, LogEntryPublic } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Field } from "@/components/Field";
import { Button } from "@/components/Button";

function fmtCost(v: number | null) {
  return v != null ? `$${v.toFixed(6)}` : "—";
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// ─── Grouping ────────────────────────────────────────────────────────────────

type StandaloneRow = { kind: "standalone"; log: LogEntryPublic };
type GroupRow      = { kind: "group";      conversationId: string; entries: LogEntryPublic[] };
type DisplayRow    = StandaloneRow | GroupRow;

function groupLogs(logs: LogEntryPublic[]): DisplayRow[] {
  // Preserve original order; gather runs/groups by conversation_id
  const seen = new Map<string, GroupRow>();
  const result: DisplayRow[] = [];

  for (const log of logs) {
    if (!log.conversation_id) {
      result.push({ kind: "standalone", log });
      continue;
    }

    const existing = seen.get(log.conversation_id);
    if (existing) {
      existing.entries.push(log);
    } else {
      const group: GroupRow = { kind: "group", conversationId: log.conversation_id, entries: [log] };
      seen.set(log.conversation_id, group);
      result.push(group);
    }
  }

  return result;
}

// ─── Standalone row ───────────────────────────────────────────────────────────

function StandaloneLogRow({ log }: { log: LogEntryPublic }) {
  return (
    <tr className="hover:bg-[var(--color-bg-alt)]">
      <td className="tabular-nums whitespace-nowrap">
        <Link
          to="/logs/$logId"
          params={{ logId: log.id }}
          className="no-underline hover:underline"
        >
          {fmtDate(log.created_at)}
        </Link>
      </td>
      <td>
        <span className="text-xs">{log.model}</span>
        <span className="ml-1 text-[var(--color-text-faint)] text-xs">{log.provider}</span>
      </td>
      <td><span className="faint text-xs">—</span></td>
      <td className="tabular-nums">{log.total_tokens.toLocaleString()}</td>
      <td className="tabular-nums">{fmtCost(log.cost_total)}</td>
      <td className="tabular-nums">{log.latency_ms != null ? `${log.latency_ms}ms` : "—"}</td>
      <td><StatusBadge status={log.status} /></td>
    </tr>
  );
}

// ─── Group rows ───────────────────────────────────────────────────────────────

function ConversationGroupRows({ group }: { group: GroupRow }) {
  const [open, setOpen] = useState(false);

  const first = group.entries[0];
  const totalTokens   = group.entries.reduce((s, e) => s + e.total_tokens, 0);
  const totalCost     = group.entries.reduce<number | null>((s, e) =>
    e.cost_total != null ? (s ?? 0) + e.cost_total : s, null);
  const lastTs        = group.entries[group.entries.length - 1].created_at;
  const hasError      = group.entries.some((e) => e.status === "error");

  return (
    <>
      {/* ── Group header row ── */}
      <tr
        className="hover:bg-[var(--color-bg-alt)] cursor-pointer select-none"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {/* Timestamp: first call timestamp */}
        <td className="tabular-nums whitespace-nowrap">
          <span className="inline-flex items-center gap-1.5">
            <span
              className="text-[var(--color-text-faint)] text-xs"
              style={{ display: "inline-block", transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 120ms" }}
            >
              ▶
            </span>
            {fmtDate(first.created_at)}
            {open && (
              <span className="text-[var(--color-text-faint)] text-xs">
                → {fmtDate(lastTs)}
              </span>
            )}
          </span>
        </td>

        {/* Model: show first model; if mixed, note it */}
        <td>
          <span className="text-xs">{first.model}</span>
          <span className="ml-1 text-[var(--color-text-faint)] text-xs">{first.provider}</span>
        </td>

        {/* Conversation ID */}
        <td>
          <Link
            to="/conversations/$conversationId"
            params={{ conversationId: group.conversationId }}
            className="text-xs no-underline hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {group.conversationId}
          </Link>
          <span className="ml-1.5 text-[var(--color-text-faint)] text-xs">
            ({group.entries.length})
          </span>
        </td>

        {/* Totals */}
        <td className="tabular-nums">{totalTokens.toLocaleString()}</td>
        <td className="tabular-nums">{fmtCost(totalCost)}</td>
        <td className="tabular-nums text-[var(--color-text-faint)]">—</td>
        <td>
          {hasError
            ? <StatusBadge status="error" />
            : <StatusBadge status="success" />}
        </td>
      </tr>

      {/* ── Expanded child rows ── */}
      {open && group.entries.map((log) => (
        <tr key={log.id} className="bg-[var(--color-bg-alt)] hover:bg-[var(--color-bg)]">
          {/* Indent timestamp */}
          <td className="tabular-nums whitespace-nowrap pl-8 text-[var(--color-text-muted)]">
            <Link
              to="/logs/$logId"
              params={{ logId: log.id }}
              className="no-underline hover:underline"
            >
              {fmtDate(log.created_at)}
            </Link>
          </td>
          <td>
            <span className="text-xs">{log.model}</span>
            <span className="ml-1 text-[var(--color-text-faint)] text-xs">{log.provider}</span>
          </td>
          {/* No conversation cell for children — they're already grouped */}
          <td className="text-[var(--color-text-faint)] text-xs pl-3">└</td>
          <td className="tabular-nums">{log.total_tokens.toLocaleString()}</td>
          <td className="tabular-nums">{fmtCost(log.cost_total)}</td>
          <td className="tabular-nums">{log.latency_ms != null ? `${log.latency_ms}ms` : "—"}</td>
          <td><StatusBadge status={log.status} /></td>
        </tr>
      ))}
    </>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function LogsPage() {
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ["logs", { model, provider, conversationId, offset }],
    queryFn: () =>
      logsApi.list({
        model: model || undefined,
        provider: provider || undefined,
        conversation_id: conversationId || undefined,
        limit,
        offset,
      }),
  });

  const rows = groupLogs(logs);

  return (
    <Layout>
      <PageHeader title="Logs" subtitle="All LLM calls" />

      {/* Filters */}
      <div className="flex gap-3 mb-4 flex-wrap">
        <Field
          label="Model"
          placeholder="gpt-4o"
          value={model}
          onChange={(e) => { setModel(e.target.value); setOffset(0); }}
          className="w-40"
        />
        <Field
          label="Provider"
          placeholder="openai"
          value={provider}
          onChange={(e) => { setProvider(e.target.value); setOffset(0); }}
          className="w-32"
        />
        <Field
          label="Conversation ID"
          placeholder="session-abc"
          value={conversationId}
          onChange={(e) => { setConversationId(e.target.value); setOffset(0); }}
          className="w-48"
        />
      </div>

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading...</p>
      ) : (
        <>
          <div className="border border-[var(--color-border)] bg-[var(--color-surface)] overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Model</th>
                  <th>Conversation</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Latency</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) =>
                  row.kind === "standalone" ? (
                    <StandaloneLogRow key={row.log.id} log={row.log} />
                  ) : (
                    <ConversationGroupRows key={row.conversationId} group={row} />
                  )
                )}
                {logs.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center text-[var(--color-text-faint)] py-6">
                      No logs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex gap-2 mt-3 justify-end">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Prev
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={logs.length < limit}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </Button>
          </div>
        </>
      )}
    </Layout>
  );
}
