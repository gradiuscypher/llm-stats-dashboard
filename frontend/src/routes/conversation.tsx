import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  logsApi,
  TranscriptMessage,
  CallDivider,
  TranscriptBranch,
} from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";

interface ConversationPageProps {
  conversationId: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function contentText(content: string | unknown[]): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) => {
        if (typeof p === "object" && p !== null && "text" in p) return (p as { text: string }).text;
        return JSON.stringify(p);
      })
      .join("\n");
  }
  return JSON.stringify(content);
}

const ROLE_LABEL: Record<string, string> = {
  system: "System",
  user: "User",
  assistant: "Assistant",
  tool: "Tool",
};

const ROLE_STYLE: Record<string, string> = {
  system: "text-[var(--color-text-faint)] bg-[var(--color-bg-alt)]",
  user: "text-[var(--color-text)] bg-[var(--color-surface)]",
  assistant: "text-[var(--color-text-muted)] bg-[var(--color-bg)]",
  tool: "text-[var(--color-accent)] bg-[var(--color-bg-alt)]",
};

// ─── Call Divider Bar ─────────────────────────────────────────────────────────

function DividerBar({ divider }: { divider: CallDivider }) {
  return (
    <div className="flex items-center gap-2 my-3 select-none">
      <div className="flex-1 h-px bg-[var(--color-border)]" />
      <Link
        to="/logs/$logId"
        params={{ logId: divider.entry_id }}
        className="flex items-center gap-2 px-2 py-0.5 text-[10px] text-[var(--color-text-faint)]
                   border border-[var(--color-border)] bg-[var(--color-bg-alt)]
                   no-underline hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]
                   transition-colors whitespace-nowrap"
      >
        <StatusBadge status={divider.status} />
        <span className="font-bold">#{divider.call_index}</span>
        <span>{divider.model}</span>
        <span className="text-[var(--color-border)]">·</span>
        <span>{divider.total_tokens.toLocaleString()} tok</span>
        {divider.latency_ms != null && (
          <>
            <span className="text-[var(--color-border)]">·</span>
            <span>{divider.latency_ms}ms</span>
          </>
        )}
        {divider.cost_total != null && (
          <>
            <span className="text-[var(--color-border)]">·</span>
            <span>${divider.cost_total.toFixed(6)}</span>
          </>
        )}
      </Link>
      <div className="flex-1 h-px bg-[var(--color-border)]" />
    </div>
  );
}

// ─── Single Message Bubble ────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: TranscriptMessage }) {
  const [expanded, setExpanded] = useState(false);
  const text = contentText(msg.content);
  const isLong = text.length > 600;
  const displayText = isLong && !expanded ? text.slice(0, 600) + "…" : text;
  const styleClass = ROLE_STYLE[msg.role] ?? ROLE_STYLE.user;
  const label = ROLE_LABEL[msg.role] ?? msg.role;

  return (
    <div className={`px-4 py-3 border-b border-[var(--color-border)] ${styleClass}`}>
      <p className="text-[10px] font-bold uppercase tracking-widest mb-1.5 opacity-60">
        {label}
      </p>
      <p className="text-sm whitespace-pre-wrap leading-relaxed break-words">{displayText}</p>
      {isLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 text-xs text-[var(--color-accent)] bg-transparent border-none
                     cursor-pointer p-0 hover:underline"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

// ─── Thread (a linear sequence of messages with interspersed dividers) ────────

function MessageThread({
  messages,
  dividers,
}: {
  messages: TranscriptMessage[];
  dividers: CallDivider[];
}) {
  // Build a lookup of message_id → index of the divider that precedes it.
  // A divider fires before the first message introduced by that call.
  const dividerBeforeEntry: Map<string, CallDivider> = new Map();
  for (const div of dividers) {
    // The first message in this call that was introduced by it.
    const firstMsg = messages.find(
      (m) => m.introduced_by_entry_id === div.entry_id
    );
    if (firstMsg) dividerBeforeEntry.set(firstMsg.message_id, div);
  }

  return (
    <div className="border border-[var(--color-border)] overflow-hidden">
      {messages.map((msg) => (
        <div key={msg.message_id}>
          {dividerBeforeEntry.has(msg.message_id) && (
            <DividerBar divider={dividerBeforeEntry.get(msg.message_id)!} />
          )}
          <MessageBubble msg={msg} />
        </div>
      ))}
    </div>
  );
}

// ─── Branch Panel ─────────────────────────────────────────────────────────────

function BranchPanel({
  branches,
  dividerMap,
}: {
  branches: TranscriptBranch[];
  dividerMap: Map<string, CallDivider>;
}) {
  const [activeBranch, setActiveBranch] = useState(0);
  if (branches.length === 0) return null;

  const branch = branches[activeBranch];
  const firstDivider = branch.dividers[0]
    ? dividerMap.get(branch.dividers[0].entry_id)
    : undefined;

  return (
    <div className="mt-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-bold text-[var(--color-text-muted)] uppercase tracking-widest">
          Branches
        </span>
        {branches.map((b, i) => {
          const div = b.dividers[0] ? dividerMap.get(b.dividers[0].entry_id) : undefined;
          return (
            <button
              key={b.branch_id}
              onClick={() => setActiveBranch(i)}
              className={`px-2 py-0.5 text-xs border transition-colors cursor-pointer
                ${activeBranch === i
                  ? "border-[var(--color-accent)] text-[var(--color-accent)] bg-transparent"
                  : "border-[var(--color-border)] text-[var(--color-text-faint)] bg-transparent hover:border-[var(--color-text-muted)]"
                }`}
            >
              {div ? `from call #${div.call_index}` : `Branch ${i + 1}`}
            </button>
          );
        })}
      </div>

      {firstDivider && (
        <p className="text-xs text-[var(--color-text-faint)] mb-2">
          Diverges at call #{firstDivider.call_index} · {firstDivider.model}
        </p>
      )}

      <MessageThread messages={branch.messages} dividers={branch.dividers} />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function ConversationPage({ conversationId }: ConversationPageProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["transcript", conversationId],
    queryFn: () => logsApi.transcript(conversationId),
  });

  const dividerMap = new Map<string, CallDivider>(
    (data?.dividers ?? []).map((d) => [d.entry_id, d])
  );

  return (
    <Layout>
      <PageHeader title="Conversation" subtitle={conversationId} />

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
      ) : isError ? (
        <p className="text-sm text-red-500">Failed to load conversation.</p>
      ) : (
        <>
          {/* Summary bar */}
          <div className="flex gap-4 mb-6 text-xs text-[var(--color-text-muted)]">
            <span>{data?.dividers.length ?? 0} calls</span>
            <span>{(data?.total_tokens ?? 0).toLocaleString()} tokens</span>
            {data?.total_cost != null && (
              <span>${data.total_cost.toFixed(6)}</span>
            )}
            {data?.is_branched && (
              <span className="text-[var(--color-accent)]">
                {data.branches.length} branch{data.branches.length !== 1 ? "es" : ""}
              </span>
            )}
          </div>

          {/* Main trunk thread */}
          {(data?.trunk ?? []).length > 0 ? (
            <MessageThread
              messages={data!.trunk}
              dividers={data!.dividers}
            />
          ) : (
            <p className="text-sm text-[var(--color-text-muted)]">
              No messages in this conversation.
            </p>
          )}

          {/* Branch panels (only shown when conversation has divergences) */}
          {data?.is_branched && (
            <BranchPanel
              branches={data.branches}
              dividerMap={dividerMap}
            />
          )}
        </>
      )}
    </Layout>
  );
}
