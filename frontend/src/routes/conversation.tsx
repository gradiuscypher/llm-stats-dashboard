import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi, LogEntryDetail } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";

interface ConversationPageProps {
  conversationId: string;
}

export function ConversationPage({ conversationId }: ConversationPageProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => logsApi.conversation(conversationId),
  });

  return (
    <Layout>
      <PageHeader title="Conversation" subtitle={conversationId} />

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading...</p>
      ) : (
        <>
          <div className="flex gap-4 mb-6 text-xs text-[var(--color-text-muted)]">
            <span>{data?.entries.length ?? 0} calls</span>
            <span>{(data?.total_tokens ?? 0).toLocaleString()} tokens</span>
            {data?.total_cost != null && <span>${data.total_cost.toFixed(6)}</span>}
          </div>

          <div className="flex flex-col gap-2">
            {(data?.entries ?? []).map((entry, idx) => (
              <LogEntryFold key={entry.id} entry={entry} idx={idx} />
            ))}
          </div>
        </>
      )}
    </Layout>
  );
}

// ─── Collapsible log entry ────────────────────────────────────────────────────

function LogEntryFold({ entry, idx }: { entry: LogEntryDetail; idx: number }) {
  const [open, setOpen] = useState(false);

  const msgs = (entry.request as { messages?: { role: string; content: string }[] }).messages ?? [];
  const lastUser = [...msgs].reverse().find((m) => m.role === "user");
  const userText = lastUser
    ? typeof lastUser.content === "string" ? lastUser.content : JSON.stringify(lastUser.content)
    : null;

  const assistantMsg = (entry.response as { message?: { role: string; content: string } }).message;
  const assistantText = assistantMsg
    ? typeof assistantMsg.content === "string" ? assistantMsg.content : JSON.stringify(assistantMsg.content)
    : null;

  return (
    <div className="border border-[var(--color-border)] bg-[var(--color-surface)]">
      {/* ── Fold header (always visible) ── */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left cursor-pointer bg-transparent border-none hover:bg-[var(--color-bg-alt)] transition-colors"
        aria-expanded={open}
      >
        {/* Chevron */}
        <span
          className="text-[var(--color-text-faint)] text-xs select-none transition-transform"
          style={{ display: "inline-block", transform: open ? "rotate(90deg)" : "rotate(0deg)" }}
        >
          ▶
        </span>

        {/* Index */}
        <span className="text-xs font-bold text-[var(--color-text-muted)] tabular-nums w-6 shrink-0">
          #{idx + 1}
        </span>

        {/* Model + status */}
        <span className="text-xs font-bold shrink-0">{entry.model}</span>
        <StatusBadge status={entry.status} />

        {/* User message snippet */}
        {userText && (
          <span className="text-xs text-[var(--color-text-muted)] truncate flex-1 min-w-0">
            {userText.slice(0, 120)}{userText.length > 120 ? "…" : ""}
          </span>
        )}

        {/* Right-side meta */}
        <span className="text-xs text-[var(--color-text-faint)] tabular-nums shrink-0 ml-auto pl-4">
          {entry.total_tokens.toLocaleString()} tok
          {entry.latency_ms != null && ` · ${entry.latency_ms}ms`}
          {entry.cost_total != null && ` · $${entry.cost_total.toFixed(6)}`}
        </span>
      </button>

      {/* ── Expanded body ── */}
      {open && (
        <div className="border-t border-[var(--color-border)]">
          {/* User message */}
          {userText && (
            <div className="px-4 py-3 border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">User</p>
              <p className="text-sm whitespace-pre-wrap leading-relaxed">{userText}</p>
            </div>
          )}

          {/* Assistant response */}
          {assistantText && (
            <div className="px-4 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
              <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">Assistant</p>
              <p className="text-sm whitespace-pre-wrap leading-relaxed text-[var(--color-text-muted)]">{assistantText}</p>
            </div>
          )}

          {/* Footer: all messages count + detail link */}
          <div className="px-4 py-2 flex items-center justify-between text-xs text-[var(--color-text-faint)]">
            <span>{msgs.length} message{msgs.length !== 1 ? "s" : ""} in request</span>
            <Link
              to="/logs/$logId"
              params={{ logId: entry.id }}
              className="text-[var(--color-accent)] no-underline hover:underline"
            >
              Full detail →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
