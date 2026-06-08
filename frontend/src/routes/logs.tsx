import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi } from "@/lib/api";
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

const PAGE_SIZES = [10, 20, 50, 100] as const;

// ─── Page ─────────────────────────────────────────────────────────────────────

export function LogsPage() {
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState<number>(20);

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ["logs", { model, provider, conversationId, offset, pageSize }],
    queryFn: () =>
      logsApi.list({
        model: model || undefined,
        provider: provider || undefined,
        conversation_id: conversationId || undefined,
        limit: pageSize,
        offset,
      }),
  });

  const start = logs.length > 0 ? offset + 1 : 0;
  const end = offset + logs.length;
  const hasMore = logs.length >= pageSize;

  return (
    <Layout>
      <PageHeader title="Logs" subtitle="All LLM calls" />

      {/* Filters */}
      <div className="flex gap-3 mb-4 flex-wrap">
        <Field
          label="Model"
          placeholder="gpt-4o"
          value={model}
          onChange={(e) => {
            setModel(e.target.value);
            setOffset(0);
          }}
          className="w-40"
        />
        <Field
          label="Provider"
          placeholder="openai"
          value={provider}
          onChange={(e) => {
            setProvider(e.target.value);
            setOffset(0);
          }}
          className="w-32"
        />
        <Field
          label="Conversation ID"
          placeholder="session-abc"
          value={conversationId}
          onChange={(e) => {
            setConversationId(e.target.value);
            setOffset(0);
          }}
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
                  <th>API Key</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                  <th>Latency</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="hover:bg-[var(--color-bg-alt)]">
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
                      <span className="ml-1 text-[var(--color-text-faint)] text-xs">
                        {log.provider}
                      </span>
                    </td>
                    <td>
                      {log.conversation_id ? (
                        <Link
                          to="/conversations/$conversationId"
                          params={{ conversationId: log.conversation_id }}
                          className="text-xs no-underline hover:underline"
                        >
                          {log.conversation_id}
                        </Link>
                      ) : (
                        <span className="text-[var(--color-text-faint)] text-xs">—</span>
                      )}
                    </td>
                    <td>
                      <span className="text-xs">{log.api_key_name ?? "Legacy"}</span>
                    </td>
                    <td className="tabular-nums">{log.total_tokens.toLocaleString()}</td>
                    <td className="tabular-nums">{fmtCost(log.cost_total)}</td>
                    <td className="tabular-nums">
                      {log.latency_ms != null ? `${log.latency_ms}ms` : "—"}
                    </td>
                    <td>
                      <StatusBadge status={log.status} />
                    </td>
                  </tr>
                ))}
                {logs.length === 0 && (
                  <tr>
                    <td colSpan={8} className="text-center text-[var(--color-text-faint)] py-6">
                      No logs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex gap-3 mt-3 justify-end items-center">
            <span className="text-xs text-[var(--color-text-muted)]">
              {logs.length > 0 ? `${start}–${end}` : "0"}
            </span>

            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setOffset(0);
              }}
              className="text-xs bg-[var(--color-bg-alt)] border border-[var(--color-border)] rounded px-1.5 py-0.5"
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n} / page
                </option>
              ))}
            </select>

            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                Prev
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={!hasMore}
                onClick={() => setOffset(offset + pageSize)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </Layout>
  );
}
