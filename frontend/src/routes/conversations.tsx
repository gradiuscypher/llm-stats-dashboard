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

type SortColumn = "last_activity" | "first_activity" | "total_tokens" | "total_cost" | "call_count";

const SORT_COLUMNS: { col: SortColumn; label: string }[] = [
  { col: "last_activity", label: "Last activity" },
  { col: "total_tokens", label: "Tokens" },
  { col: "total_cost", label: "Cost" },
  { col: "call_count", label: "Calls" },
];

// ─── Page ─────────────────────────────────────────────────────────────────────

export function ConversationsPage() {
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState<number>(20);
  const [sort, setSort] = useState<SortColumn>("last_activity");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  const { data, isLoading } = useQuery({
    queryKey: ["conversations", { model, provider, conversationId, sort, order, offset, pageSize }],
    queryFn: () =>
      logsApi.listConversations({
        model: model || undefined,
        provider: provider || undefined,
        conversation_id: conversationId || undefined,
        sort,
        order,
        limit: pageSize,
        offset,
      }),
  });

  const rows = data?.conversations ?? [];
  const total = data?.total ?? 0;
  const start = rows.length > 0 ? offset + 1 : 0;
  const end = offset + rows.length;

  function toggleSort(col: SortColumn) {
    if (sort === col) {
      setOrder((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSort(col);
      setOrder("desc");
    }
    setOffset(0);
  }

  function sortIndicator(col: SortColumn) {
    if (sort !== col) return null;
    return order === "asc" ? "▲" : "▼";
  }

  return (
    <Layout>
      <PageHeader title="Conversations" subtitle="Grouped by conversation" />

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
                  <th>Conversation</th>
                  {SORT_COLUMNS.map(({ col, label }) => (
                    <th key={col} className="cursor-pointer select-none" onClick={() => toggleSort(col)}>
                      {label} <span className="text-[var(--color-accent)]">{sortIndicator(col)}</span>
                    </th>
                  ))}
                  <th>Models</th>
                  <th>Providers</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.conversation_id} className="hover:bg-[var(--color-bg-alt)]">
                    <td>
                      <Link
                        to="/conversations/$conversationId"
                        params={{ conversationId: row.conversation_id }}
                        className="text-xs no-underline hover:underline"
                      >
                        {row.conversation_id}
                      </Link>
                    </td>
                    <td className="tabular-nums whitespace-nowrap">
                      {fmtDate(row.last_activity)}
                    </td>
                    <td className="tabular-nums">{row.total_tokens.toLocaleString()}</td>
                    <td className="tabular-nums">{fmtCost(row.total_cost)}</td>
                    <td className="tabular-nums">{row.call_count}</td>
                    <td>
                      <span className="text-xs">
                        {row.models.length <= 2
                          ? row.models.join(", ")
                          : `${row.models[0]}, ${row.models[1]} +${row.models.length - 2}`}
                      </span>
                    </td>
                    <td>
                      <span className="text-xs text-[var(--color-text-faint)]">
                        {row.providers.join(", ")}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={row.has_error ? "error" : "ok"} />
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="text-center text-[var(--color-text-faint)] py-6">
                      No conversations found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex gap-3 mt-3 justify-end items-center">
            <span className="text-xs text-[var(--color-text-muted)]">
              {rows.length > 0 ? `${start}–${end} of ${total}` : "0"}
            </span>

            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setOffset(0); }}
              className="text-xs bg-[var(--color-bg-alt)] border border-[var(--color-border)] rounded px-1.5 py-0.5"
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>{n} / page</option>
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
                disabled={offset + pageSize >= total}
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
