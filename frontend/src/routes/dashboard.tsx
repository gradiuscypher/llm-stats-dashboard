import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtCost(v: number | null) {
  if (v == null) return "—";
  return `$${v.toFixed(4)}`;
}

function fmtNum(v: number) {
  return v.toLocaleString();
}

function fmtPct(numerator: number, denominator: number, digits = 1): string {
  if (denominator === 0) return "—";
  return `${((numerator / denominator) * 100).toFixed(digits)}%`;
}

function formatBucketLabel(v: string, interval: string): string {
  switch (interval) {
    case "5m":
      // e.g. "2026-06-10T14:00:00+00:00" → "14:00"
      return v.slice(11, 16);
    case "1h":
      // e.g. "2026-06-10T14:00:00+00:00" → "14:00"
      return v.slice(11, 16);
    case "1d":
      // e.g. "2026-06-10" → "06-10"
      return v.length >= 10 ? v.slice(5) : v;
    case "1w":
      // e.g. "2026-06-08" → "06-08"
      return v.length >= 10 ? v.slice(5) : v;
    case "1mo":
      // e.g. "2026-06" → "06/26" — keep year-month brief
      return v.length === 7 ? v.slice(2).replace("-", "/") : v;
    default:
      return v.length >= 10 ? v.slice(5) : v;
  }
}

// ─── Timeframe presets ──────────────────────────────────────────────────────

interface TimeframePreset {
  label: string;
  sinceOffsetMs: number | null; // null = all time
  interval: string;
  subtitle: string;
}

const TIMEFRAMES: Record<string, TimeframePreset> = {
  "1h": {
    label: "Last hour",
    sinceOffsetMs: 60 * 60 * 1000,
    interval: "5m",
    subtitle: "Last hour",
  },
  "6h": {
    label: "Last 6 hours",
    sinceOffsetMs: 6 * 60 * 60 * 1000,
    interval: "1h",
    subtitle: "Last 6 hours",
  },
  "12h": {
    label: "Last 12 hours",
    sinceOffsetMs: 12 * 60 * 60 * 1000,
    interval: "1h",
    subtitle: "Last 12 hours",
  },
  "1d": {
    label: "Last day",
    sinceOffsetMs: 24 * 60 * 60 * 1000,
    interval: "1h",
    subtitle: "Last 24 hours",
  },
  "1w": {
    label: "Last week",
    sinceOffsetMs: 7 * 24 * 60 * 60 * 1000,
    interval: "1d",
    subtitle: "Last 7 days",
  },
  "1m": {
    label: "Last month",
    sinceOffsetMs: 30 * 24 * 60 * 60 * 1000,
    interval: "1d",
    subtitle: "Last 30 days",
  },
  all: {
    label: "All time",
    sinceOffsetMs: null,
    interval: "1mo",
    subtitle: "All time",
  },
};

// ─── Component ───────────────────────────────────────────────────────────────

export function DashboardPage() {
  const [timeframe, setTimeframe] = useState<string>("1m");

  const queryOpts = useMemo(() => {
    const preset = TIMEFRAMES[timeframe];
    if (!preset) return { interval: "1d", sinceOffsetMs: null as number | null };
    return { interval: preset.interval, sinceOffsetMs: preset.sinceOffsetMs };
  }, [timeframe]);

  const { data: stats, isLoading } = useQuery({
    queryKey: ["stats", queryOpts],
    queryFn: () => {
      const opts: { since?: string; interval: string } = { interval: queryOpts.interval };
      if (queryOpts.sinceOffsetMs != null)
        opts.since = new Date(Date.now() - queryOpts.sinceOffsetMs).toISOString();
      return logsApi.stats(opts);
    },
  });

  const preset = TIMEFRAMES[timeframe] ?? TIMEFRAMES["1m"];
  const interval = stats?.interval ?? "1d";

  // Chart data for tokens saved / cached tokens per bucket
  const chartData = useMemo(() => {
    if (!stats) return [];
    return stats.by_day.map((d) => ({
      ...d,
      calls: d.calls,
      tokensSaved: d.tokens_saved,
      cachedTokens: d.cache_read_tokens,
      totalTokens: d.total_tokens,
    }));
  }, [stats]);

  const cacheHitPct = fmtPct(stats?.total_cache_read_tokens ?? 0, stats?.total_prompt_tokens ?? 0);

  return (
    <Layout>
      <PageHeader title="Dashboard" subtitle={preset.subtitle} />

      {/* Timeframe selector */}
      <div className="flex items-center gap-2 mb-4">
        <label className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">
          Timeframe
        </label>
        <select
          value={timeframe}
          onChange={(e) => setTimeframe(e.target.value)}
          className="text-xs bg-[var(--color-bg-alt)] border border-[var(--color-border)] rounded px-1.5 py-0.5"
        >
          {Object.entries(TIMEFRAMES).map(([key, p]) => (
            <option key={key} value={key}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading...</p>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
            <StatCard label="Total Calls" value={fmtNum(stats?.total_calls ?? 0)} />
            <StatCard label="Total Tokens" value={fmtNum(stats?.total_tokens ?? 0)} />
            <StatCard label="Cached Tokens" value={fmtNum(stats?.total_cache_read_tokens ?? 0)} />
            <StatCard
              label="Cache Hit %"
              value={cacheHitPct}
              sub="of prompt tokens read from cache"
            />
            <StatCard
              label="Tokens Saved"
              value={fmtNum(stats?.total_tokens_saved ?? 0)}
              sub="via compression"
            />
            <StatCard label="Reasoning Tokens" value={fmtNum(stats?.total_reasoning_tokens ?? 0)} />
            <StatCard label="Total Cost" value={fmtCost(stats?.total_cost ?? null)} sub="USD" />
            <StatCard label="Models" value={stats?.by_model.length ?? 0} sub="distinct" />
          </div>

          {/* Calls per bucket chart */}
          <div className="border border-[var(--color-border)] p-4 bg-[var(--color-surface)] mb-4">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-4">
              Calls per{" "}
              {interval === "5m"
                ? "5 min"
                : interval === "1h"
                  ? "hour"
                  : interval === "1w"
                    ? "week"
                    : interval === "1mo"
                      ? "month"
                      : "day"}
            </h2>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={chartData}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                  tickFormatter={(v: string) => formatBucketLabel(v, interval)}
                />
                <YAxis tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }} />
                <Tooltip
                  contentStyle={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    border: "1px solid var(--color-border-strong)",
                    borderRadius: 0,
                    backgroundColor: "var(--color-surface)",
                    color: "var(--color-text)",
                  }}
                  labelStyle={{ color: "var(--color-text)" }}
                  itemStyle={{ color: "var(--color-text)" }}
                />
                <Line
                  type="monotone"
                  dataKey="calls"
                  stroke="var(--color-text)"
                  dot={false}
                  strokeWidth={1.5}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Tokens saved & cached chart */}
          <div className="border border-[var(--color-border)] p-4 bg-[var(--color-surface)] mb-6">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-4">
              Cache reads & compression savings
            </h2>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={chartData}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                  tickFormatter={(v: string) => formatBucketLabel(v, interval)}
                />
                <YAxis tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }} />
                <Tooltip
                  contentStyle={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    border: "1px solid var(--color-border-strong)",
                    borderRadius: 0,
                    backgroundColor: "var(--color-surface)",
                    color: "var(--color-text)",
                  }}
                  labelStyle={{ color: "var(--color-text)" }}
                  itemStyle={{ color: "var(--color-text)" }}
                />
                <Legend
                  wrapperStyle={{
                    fontSize: 10,
                    fontFamily: "var(--font-mono)",
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="cachedTokens"
                  name="Cache reads"
                  stroke="var(--color-info, #2563eb)"
                  dot={false}
                  strokeWidth={1.5}
                />
                <Line
                  type="monotone"
                  dataKey="tokensSaved"
                  name="Saved (compression)"
                  stroke="var(--color-success, #d97706)"
                  dot={false}
                  strokeWidth={1.5}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* By model table */}
          <div className="border border-[var(--color-border)] bg-[var(--color-surface)]">
            <div className="px-4 py-2 border-b border-[var(--color-border)]">
              <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                By model
              </h2>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Tokens</th>
                  <th>Cached</th>
                  <th>Saved</th>
                  <th>Reasoning</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {(stats?.by_model ?? []).map((row) => (
                  <tr key={row.model} className="hover:bg-[var(--color-bg-alt)]">
                    <td>
                      <Link
                        to="/logs"
                        search={{
                          model: row.model,
                          provider: undefined,
                          conversation_id: undefined,
                        }}
                        className="no-underline hover:underline"
                      >
                        {row.model}
                      </Link>
                    </td>
                    <td className="tabular-nums">{fmtNum(row.calls)}</td>
                    <td className="tabular-nums">{fmtNum(row.total_tokens)}</td>
                    <td className="tabular-nums">{fmtNum(row.cache_read_tokens)}</td>
                    <td className="tabular-nums">{fmtNum(row.tokens_saved)}</td>
                    <td className="tabular-nums">{fmtNum(row.reasoning_tokens)}</td>
                    <td className="tabular-nums">{fmtCost(row.cost)}</td>
                  </tr>
                ))}
                {(stats?.by_model.length ?? 0) === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center text-[var(--color-text-faint)] py-4">
                      No data yet.{" "}
                      <Link to="/docs" className="text-[var(--color-accent)]">
                        See docs
                      </Link>{" "}
                      to start logging.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Layout>
  );
}
