import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  logsApi,
  pluginsApi,
  TranscriptMessage,
  CallDivider,
  TranscriptBranch,
  ConversationPluginState,
} from "@/lib/api";
import { selectReasoningRender } from "@/lib/reasoning";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { ModificationBadge, ModifiedByLabel } from "@/components/ModificationBadge";
import { MessageDiff } from "@/components/MessageDiff";
import { useShowDiff } from "@/lib/useShowDiff";

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
        {divider.reasoning_tokens > 0 && (
          <>
            <span className="text-[var(--color-border)]">·</span>
            <span className="text-[var(--color-text-faint)]">
              {divider.reasoning_tokens.toLocaleString()} think
            </span>
          </>
        )}
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
        <ModificationBadge
          count={divider.modification_count ?? 0}
          pluginNames={divider.modifications?.map((m) => m.plugin_name)}
          size="sm"
        />
      </Link>
      <div className="flex-1 h-px bg-[var(--color-border)]" />
    </div>
  );
}

// ─── Reasoning Block ──────────────────────────────────────────────────────────

function ReasoningBlock({
  reasoning,
  reasoning_details,
}: {
  reasoning?: string | null;
  reasoning_details?: unknown[] | null;
}) {
  const [expanded, setExpanded] = useState(false);

  const render = selectReasoningRender(reasoning, reasoning_details);
  if (!render) return null;

  return (
    <div className="mt-2 border border-[var(--color-border)] bg-[var(--color-bg-alt)]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-1.5 flex items-center gap-1.5 text-[10px]
                   font-bold uppercase tracking-widest
                   text-[var(--color-text-muted)] hover:text-[var(--color-text)]
                   bg-transparent border-none cursor-pointer transition-colors"
      >
        <span className="text-[11px]">{expanded ? "▾" : "▸"}</span>
        Thinking
        <span className="font-normal lowercase tracking-normal opacity-50">
          ({render.charCount.toLocaleString()} chars)
        </span>
      </button>
      {expanded && (
        <div
          className="px-3 pb-2 text-xs whitespace-pre-wrap leading-relaxed
                     text-[var(--color-text-muted)] opacity-80"
        >
          {render.mode === "details" ? (
            render.blocks.map((block, i) => {
              const isEncryptedOrRedacted =
                block.type?.includes("encrypted") || block.type?.includes("redacted");
              if (isEncryptedOrRedacted) {
                return (
                  <p key={i} className="italic opacity-50">
                    [{block.type} reasoning block]
                  </p>
                );
              }
              if (block.text) {
                return <p key={i}>{block.text}</p>;
              }
              return null;
            })
          ) : (
            <p>{render.text}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Single Message Bubble ────────────────────────────────────────────────────

function MessageBubble({ msg, showDiff }: { msg: TranscriptMessage; showDiff: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const text = contentText(msg.content);
  const modText = msg.modified_content != null ? contentText(msg.modified_content) : null;
  const isLong = text.length > 600;
  const displayText = isLong && !expanded ? text.slice(0, 600) + "…" : text;
  const styleClass = ROLE_STYLE[msg.role] ?? ROLE_STYLE.user;
  const label = ROLE_LABEL[msg.role] ?? msg.role;
  const showReasoning = msg.role === "assistant";
  const hasModifications = msg.modified_by && msg.modified_by.length > 0;
  const hasDiff = showDiff && modText != null && modText !== text;

  return (
    <div
      className={`px-4 py-3 border-b border-[var(--color-border)] ${styleClass} ${
        hasModifications ? "border-l-2 border-l-[var(--color-accent)]/40" : ""
      }`}
    >
      <p className="text-[10px] font-bold uppercase tracking-widest mb-1.5 opacity-60">
        {label}
        {hasModifications && <ModifiedByLabel pluginNames={msg.modified_by} />}
      </p>
      {showReasoning && (
        <ReasoningBlock reasoning={msg.reasoning} reasoning_details={msg.reasoning_details} />
      )}
      {hasDiff && modText ? (
        <MessageDiff original={displayText} final={modText} modifiedBy={msg.modified_by} />
      ) : (
        <p className="text-sm whitespace-pre-wrap leading-relaxed break-words">{displayText}</p>
      )}
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
  showDiff,
}: {
  messages: TranscriptMessage[];
  dividers: CallDivider[];
  showDiff: boolean;
}) {
  // Build a lookup of message_id → index of the divider that precedes it.
  // A divider fires before the first message introduced by that call.
  const dividerBeforeEntry: Map<string, CallDivider> = new Map();
  for (const div of dividers) {
    // The first message in this call that was introduced by it.
    const firstMsg = messages.find((m) => m.introduced_by_entry_id === div.entry_id);
    if (firstMsg) dividerBeforeEntry.set(firstMsg.message_id, div);
  }

  return (
    <div className="border border-[var(--color-border)] overflow-hidden">
      {messages.map((msg) => (
        <div key={msg.message_id}>
          {dividerBeforeEntry.has(msg.message_id) && (
            <DividerBar divider={dividerBeforeEntry.get(msg.message_id)!} />
          )}
          <MessageBubble msg={msg} showDiff={showDiff} />
        </div>
      ))}
    </div>
  );
}

// ─── Branch Panel ─────────────────────────────────────────────────────────────

function BranchPanel({
  branches,
  dividerMap,
  showDiff,
}: {
  branches: TranscriptBranch[];
  dividerMap: Map<string, CallDivider>;
  showDiff: boolean;
}) {
  const [activeBranch, setActiveBranch] = useState(0);
  if (branches.length === 0) return null;

  const branch = branches[activeBranch];
  const firstDivider = branch.dividers[0] ? dividerMap.get(branch.dividers[0].entry_id) : undefined;

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
                ${
                  activeBranch === i
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

      <MessageThread messages={branch.messages} dividers={branch.dividers} showDiff={showDiff} />
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

function PluginToggle({
  plugin,
  conversationId,
}: {
  plugin: ConversationPluginState;
  conversationId: string;
}) {
  const queryClient = useQueryClient();
  const toggleMutation = useMutation({
    mutationFn: ({ enabled }: { enabled: boolean }) => {
      if (enabled) {
        return pluginsApi.setConversationOverride(
          conversationId,
          plugin.name,
          true
        ) as unknown as Promise<void>;
      }
      return pluginsApi.deleteConversationOverride(conversationId, plugin.name);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversationPlugins", conversationId] });
    },
  });

  const isOverride = plugin.override_enabled !== null;
  const isOn = plugin.effective;

  return (
    <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2 last:border-b-0">
      <div className="flex-1">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text)]">
          {plugin.name}
        </span>
        {plugin.locked && (
          <span className="ml-1 text-[9px] text-[var(--color-accent)] border border-[var(--color-accent)] px-1">
            LOCKED
          </span>
        )}
        {isOverride && !plugin.locked && (
          <span className="ml-1 text-[9px] text-[var(--color-text-faint)] border border-[var(--color-border)] px-1">
            OVERRIDE
          </span>
        )}
        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{plugin.description}</p>
      </div>
      <label className="flex items-center gap-2 cursor-pointer ml-3">
        {plugin.locked ? (
          <span className="w-8 h-4 rounded-full bg-[var(--color-accent)] relative cursor-not-allowed">
            <span className="absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-white block" />
          </span>
        ) : (
          <>
            <input
              type="checkbox"
              className="sr-only"
              checked={isOn}
              onChange={(e) => toggleMutation.mutate({ enabled: e.target.checked })}
            />
            <span
              className={`w-8 h-4 rounded-full relative transition-colors ${
                isOn ? "bg-[var(--color-accent)]" : "bg-[var(--color-border)]"
              }`}
            >
              <span
                className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform ${
                  isOn ? "left-[calc(100%-0.875rem)]" : "left-0.5"
                }`}
              />
            </span>
          </>
        )}
        <span className="text-xs text-[var(--color-text-muted)]">{isOn ? "On" : "Off"}</span>
      </label>
    </div>
  );
}

export function ConversationPage({ conversationId }: ConversationPageProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["transcript", conversationId],
    queryFn: () => logsApi.transcript(conversationId),
  });

  const [showDiff, setShowDiff] = useShowDiff();

  const dividerMap = new Map<string, CallDivider>(
    (data?.dividers ?? []).map((d) => [d.entry_id, d])
  );

  const { data: convPlugins = [], isLoading: convPluginsLoading } = useQuery({
    queryKey: ["conversationPlugins", conversationId],
    queryFn: () => pluginsApi.listConversationPlugins(conversationId),
  });

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
          <div className="flex gap-4 mb-6 text-xs text-[var(--color-text-muted)] items-center">
            <span>{data?.dividers.length ?? 0} calls</span>
            <span>{(data?.total_tokens ?? 0).toLocaleString()} tokens</span>
            {data?.total_cost != null && <span>${data.total_cost.toFixed(6)}</span>}
            {data?.is_branched && (
              <span className="text-[var(--color-accent)]">
                {data.branches.length} branch{data.branches.length !== 1 ? "es" : ""}
              </span>
            )}
            {/* Diff toggle */}
            <button
              onClick={() => setShowDiff(!showDiff)}
              className={`ml-auto px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider
                border cursor-pointer transition-colors bg-transparent ${
                  showDiff
                    ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                    : "border-[var(--color-border)] text-[var(--color-text-faint)]"
                }`}
            >
              {showDiff ? "Diffs ON" : "Diffs OFF"}
            </button>
          </div>

          {/* Per-conversation plugin overrides */}
          {convPlugins.length > 0 && (
            <div className="mb-4 border border-[var(--color-border)] bg-[var(--color-surface)]">
              <div className="px-3 py-2 border-b border-[var(--color-border)]">
                <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                  Plugin Overrides
                </span>
                <span className="ml-2 text-[10px] text-[var(--color-text-faint)]">
                  Overrides apply to future calls in this conversation
                </span>
              </div>
              {convPluginsLoading ? (
                <p className="px-3 py-2 text-xs text-[var(--color-text-faint)]">Loading…</p>
              ) : (
                convPlugins.map((p) => (
                  <PluginToggle key={p.name} plugin={p} conversationId={conversationId} />
                ))
              )}
            </div>
          )}

          {/* Main trunk thread */}
          {(data?.trunk ?? []).length > 0 ? (
            <MessageThread messages={data!.trunk} dividers={data!.dividers} showDiff={showDiff} />
          ) : (
            <p className="text-sm text-[var(--color-text-muted)]">
              No messages in this conversation.
            </p>
          )}

          {/* Branch panels (only shown when conversation has divergences) */}
          {data?.is_branched && (
            <BranchPanel branches={data.branches} dividerMap={dividerMap} showDiff={showDiff} />
          )}
        </>
      )}
    </Layout>
  );
}
