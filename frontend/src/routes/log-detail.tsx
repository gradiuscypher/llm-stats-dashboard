import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/Button";

interface LogDetailPageProps {
  logId: string;
}

type MessageRole = "system" | "user" | "assistant" | "tool";

const roleBg: Record<MessageRole, string> = {
  system: "bg-[var(--color-bg-alt)]",
  user: "bg-[var(--color-surface)]",
  assistant: "bg-[var(--color-bg)]",
  tool: "bg-[var(--color-code-bg)]",
};

function MessageBlock({ role, content }: { role: string; content: unknown }) {
  const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
  return (
    <div
      className={`border-b border-[var(--color-border)] px-4 py-3 ${roleBg[(role as MessageRole)] ?? ""}`}
    >
      <div className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
        {role}
      </div>
      <pre className="whitespace-pre-wrap text-sm leading-relaxed bg-transparent border-none p-0">
        {text}
      </pre>
    </div>
  );
}

export function LogDetailPage({ logId }: LogDetailPageProps) {
  const [showRaw, setShowRaw] = useState(false);
  const { data: log, isLoading } = useQuery({
    queryKey: ["log", logId],
    queryFn: () => logsApi.get(logId),
  });

  if (isLoading) return <Layout><p className="text-sm muted">Loading...</p></Layout>;
  if (!log) return <Layout><p className="text-sm danger">Log not found.</p></Layout>;

  const messages: Array<{ role: string; content: unknown }> = [
    ...((log.request as { messages?: { role: string; content: unknown }[] }).messages ?? []),
    (log.response as { message?: { role: string; content: unknown } }).message,
  ].filter(Boolean);

  return (
    <Layout>
      <PageHeader
        title="Log Detail"
        subtitle={log.id}
        actions={
          <Button variant="secondary" size="sm" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "Message View" : "Raw JSON"}
          </Button>
        }
      />

      {/* Meta */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {[
          ["Model", log.model],
          ["Provider", log.provider],
          ["Status", <StatusBadge status={log.status} key="s" />],
          ["Latency", log.latency_ms != null ? `${log.latency_ms}ms` : "—"],
          ["Prompt tokens", log.prompt_tokens.toLocaleString()],
          ["Completion tokens", log.completion_tokens.toLocaleString()],
          ["Total tokens", log.total_tokens.toLocaleString()],
          ["Cost", log.cost_total != null ? `$${log.cost_total.toFixed(6)}` : "—"],
        ].map(([label, value]) => (
          <div key={String(label)} className="border border-[var(--color-border)] p-3 bg-[var(--color-surface)]">
            <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
              {label}
            </p>
            <p className="text-sm tabular-nums">{value}</p>
          </div>
        ))}
      </div>

      {log.conversation_id && (
        <div className="mb-4 text-xs text-[var(--color-text-muted)]">
          Conversation:{" "}
          <Link
            to="/conversations/$conversationId"
            params={{ conversationId: log.conversation_id }}
            className="text-[var(--color-accent)]"
          >
            {log.conversation_id}
          </Link>
        </div>
      )}

      {showRaw ? (
        <pre className="overflow-x-auto text-xs">
          {JSON.stringify({ request: log.request, response: log.response }, null, 2)}
        </pre>
      ) : (
        <div className="border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="px-4 py-2 border-b border-[var(--color-border)]">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Messages
            </h2>
          </div>
          {messages.map((msg, i) => (
            <MessageBlock key={i} role={msg?.role ?? "unknown"} content={msg?.content} />
          ))}
        </div>
      )}

      {log.tool_calls.length > 0 && (
        <div className="mt-4 border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="px-4 py-2 border-b border-[var(--color-border)]">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Tool Calls ({log.tool_calls.length})
            </h2>
          </div>
          <pre className="text-xs p-4">{JSON.stringify(log.tool_calls, null, 2)}</pre>
        </div>
      )}
    </Layout>
  );
}
