import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi } from "@/lib/api";
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
      <PageHeader
        title="Conversation"
        subtitle={conversationId}
      />

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading...</p>
      ) : (
        <>
          <div className="flex gap-4 mb-6 text-xs text-[var(--color-text-muted)]">
            <span>{data?.entries.length ?? 0} calls</span>
            <span>{(data?.total_tokens ?? 0).toLocaleString()} tokens</span>
            {data?.total_cost != null && <span>${data.total_cost.toFixed(6)}</span>}
          </div>

          <div className="flex flex-col gap-3">
            {(data?.entries ?? []).map((entry, idx) => (
              <div
                key={entry.id}
                className="border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-[var(--color-text-muted)]">
                      #{idx + 1}
                    </span>
                    <span className="text-xs">{entry.model}</span>
                    <StatusBadge status={entry.status} />
                  </div>
                  <Link
                    to="/logs/$logId"
                    params={{ logId: entry.id }}
                    className="text-xs text-[var(--color-accent)]"
                  >
                    Detail →
                  </Link>
                </div>

                {/* Last user message preview */}
                {(() => {
                  const msgs = (entry.request as { messages?: { role: string; content: string }[] }).messages ?? [];
                  const lastUser = [...msgs].reverse().find((m) => m.role === "user");
                  const content = typeof lastUser?.content === "string"
                    ? lastUser.content
                    : JSON.stringify(lastUser?.content);
                  return lastUser ? (
                    <div className="mb-2">
                      <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
                        User
                      </p>
                      <p className="text-sm text-[var(--color-text)] line-clamp-3">
                        {content?.slice(0, 300)}{(content?.length ?? 0) > 300 ? "…" : ""}
                      </p>
                    </div>
                  ) : null;
                })()}

                {/* Assistant response preview */}
                {(() => {
                  const msg = (entry.response as { message?: { role: string; content: string } }).message;
                  const content = typeof msg?.content === "string"
                    ? msg.content
                    : JSON.stringify(msg?.content);
                  return msg ? (
                    <div>
                      <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
                        Assistant
                      </p>
                      <p className="text-sm text-[var(--color-text-muted)] line-clamp-2">
                        {content?.slice(0, 300)}{(content?.length ?? 0) > 300 ? "…" : ""}
                      </p>
                    </div>
                  ) : null;
                })()}

                <div className="flex gap-3 mt-3 text-xs text-[var(--color-text-faint)]">
                  <span>{entry.total_tokens.toLocaleString()} tokens</span>
                  {entry.latency_ms != null && <span>{entry.latency_ms}ms</span>}
                  {entry.cost_total != null && <span>${entry.cost_total.toFixed(6)}</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </Layout>
  );
}
