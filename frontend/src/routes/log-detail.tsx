import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi, ModificationPublic, MessageDiffPublic } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { ModificationBadge } from "@/components/ModificationBadge";
import { MessageDiff } from "@/components/MessageDiff";
import { useShowDiff } from "@/lib/useShowDiff";
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

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) => {
        if (typeof p === "object" && p !== null && "text" in p) return (p as { text: string }).text;
        return JSON.stringify(p);
      })
      .join("\n");
  }
  return JSON.stringify(content, null, 2);
}

function MessageBlock({
  role,
  content,
  reasoning,
  reasoningDetails,
}: {
  role: string;
  content: unknown;
  reasoning?: string;
  reasoningDetails?: unknown[];
}) {
  const hasReasoning = reasoning && reasoning.trim().length > 0;
  const hasDetails = Array.isArray(reasoningDetails) && reasoningDetails.length > 0;

  return (
    <div
      className={`border-b border-[var(--color-border)] px-4 py-3 ${roleBg[role as MessageRole] ?? ""}`}
    >
      <div className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
        {role}
      </div>
      {(hasReasoning || hasDetails) && (
        <details className="mb-2">
          <summary className="text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-faint)] cursor-pointer hover:text-[var(--color-text-muted)]">
            Thinking
            {hasReasoning && ` (${reasoning!.length} chars)`}
          </summary>
          <div className="mt-1 pl-2 border-l-2 border-[var(--color-border)] text-xs whitespace-pre-wrap leading-relaxed text-[var(--color-text-muted)] opacity-80">
            {hasReasoning && <p>{reasoning}</p>}
            {hasDetails &&
              reasoningDetails!.map((block, i) => {
                if (typeof block === "object" && block !== null) {
                  const b = block as Record<string, unknown>;
                  const type = typeof b.type === "string" ? b.type : "";
                  const t = typeof b.text === "string" ? b.text : "";
                  if (type.includes("encrypted") || type.includes("redacted")) {
                    return (
                      <p key={i} className="italic opacity-50">
                        [{type} reasoning block]
                      </p>
                    );
                  }
                  if (t) return <p key={i}>{t}</p>;
                }
                return null;
              })}
          </div>
        </details>
      )}
      <pre className="whitespace-pre-wrap text-sm leading-relaxed bg-transparent border-none p-0">
        {contentText(content)}
      </pre>
    </div>
  );
}

function DiffRow({ diff }: { diff: MessageDiffPublic }) {
  if (diff.change_kind !== "modified") return null;
  const origText =
    diff.original_content && typeof diff.original_content.content === "string"
      ? diff.original_content.content
      : JSON.stringify(diff.original_content);
  const finalText =
    diff.final_content && typeof diff.final_content.content === "string"
      ? diff.final_content.content
      : JSON.stringify(diff.final_content);

  return (
    <MessageDiff
      original={origText}
      final={finalText}
      modifiedBy={diff.modified_by}
      className="mb-2"
    />
  );
}

function ModRow({ mod }: { mod: ModificationPublic }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-[var(--color-border)] mb-2 last:mb-0">
      <div
        className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-[var(--color-bg-alt)]"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-[10px]">{expanded ? "▾" : "▸"}</span>
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)]">
          {mod.plugin_name}
        </span>
        <span className="text-xs text-[var(--color-text-faint)]">{mod.target}</span>
        {mod.message_role && (
          <span className="text-xs text-[var(--color-text-faint)]">· {mod.message_role}</span>
        )}
        <span className="flex-1" />
        <span className="text-xs text-[var(--color-text-muted)]">{mod.summary}</span>
      </div>
      {expanded && mod.detail && (
        <pre className="px-3 pb-2 text-[10px] whitespace-pre-wrap text-[var(--color-text-muted)]">
          {JSON.stringify(mod.detail, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function LogDetailPage({ logId }: LogDetailPageProps) {
  const [showRaw, setShowRaw] = useState(false);
  const [showDiff, setShowDiff] = useShowDiff();
  const { data: log, isLoading } = useQuery({
    queryKey: ["log", logId],
    queryFn: () => logsApi.get(logId),
  });

  if (isLoading)
    return (
      <Layout>
        <p className="text-sm muted">Loading...</p>
      </Layout>
    );
  if (!log)
    return (
      <Layout>
        <p className="text-sm danger">Log not found.</p>
      </Layout>
    );

  const messages: Array<{
    role: string;
    content: unknown;
    reasoning?: string;
    reasoningDetails?: unknown[];
  }> = [
    ...(
      (
        log.request as {
          messages?: {
            role: string;
            content: unknown;
            reasoning?: string;
            reasoning_details?: unknown[];
          }[];
        }
      ).messages ?? []
    ).map((m) => ({
      role: m.role,
      content: m.content,
      reasoning: m.reasoning,
      reasoningDetails: m.reasoning_details,
    })),
  ];
  const rmsg = (
    log.response as {
      message?: {
        role: string;
        content: unknown;
        reasoning?: string;
        reasoning_details?: unknown[];
      };
    }
  ).message;
  if (rmsg) {
    messages.push({
      role: rmsg.role ?? "assistant",
      content: rmsg.content,
      reasoning: rmsg.reasoning,
      reasoningDetails: rmsg.reasoning_details,
    });
  }

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
          ["Reasoning tokens", log.reasoning_tokens?.toLocaleString() ?? "0"],
          ["Cost", log.cost_total != null ? `$${log.cost_total.toFixed(6)}` : "—"],
          ...(log.metadata_extra?.compression
            ? [
                [
                  "Tokens Saved by Compression",
                  <span key="comp">
                    {log.metadata_extra.compression.tokens_saved.toLocaleString()}{" "}
                    <span className="text-[var(--color-text-faint)]">
                      ({(log.metadata_extra.compression.compression_ratio * 100).toFixed(0)}%)
                    </span>
                  </span>,
                ],
                [
                  "Compression Transforms",
                  <span key="trans" className="text-xs text-[var(--color-accent)]">
                    {[...new Set(log.metadata_extra.compression.transforms_applied)].join(", ")}
                  </span>,
                ],
              ]
            : []),
        ].map(([label, value]) => (
          <div
            key={String(label)}
            className="border border-[var(--color-border)] p-3 bg-[var(--color-surface)]"
          >
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

      {/* Modifications (legacy) */}
      {log.modifications && log.modifications.length > 0 && (
        <div className="mb-4 border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="px-4 py-2 border-b border-[var(--color-border)] flex items-center gap-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Modifications (legacy)
            </h2>
            <ModificationBadge count={log.modifications.length} size="sm" />
          </div>
          <div className="p-4">
            {log.modifications.map((mod) => (
              <ModRow key={mod.id} mod={mod} />
            ))}
          </div>
        </div>
      )}

      {/* Request Diffs */}
      {log.request_diffs && log.request_diffs.length > 0 && (
        <div className="mb-4 border border-[var(--color-border)] bg-[var(--color-surface)]">
          <div className="px-4 py-2 border-b border-[var(--color-border)] flex items-center gap-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Request Diffs
            </h2>
            <ModificationBadge count={log.request_diffs.length} size="sm" />
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
          {showDiff && (
            <div className="p-4">
              {log.request_diffs.map((diff) => (
                <DiffRow key={diff.id} diff={diff} />
              ))}
            </div>
          )}
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
            <MessageBlock
              key={i}
              role={msg?.role ?? "unknown"}
              content={msg?.content}
              reasoning={msg?.reasoning}
              reasoningDetails={msg?.reasoningDetails}
            />
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
