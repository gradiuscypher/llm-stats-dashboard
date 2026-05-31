import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { logsApi } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

function fmtCost(v: number | null) {
  if (v == null) return "—";
  return `$${v.toFixed(4)}`;
}

function fmtNum(v: number) {
  return v.toLocaleString();
}

export function DashboardPage() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ["stats", 30],
    queryFn: () => logsApi.stats(30),
  });

  return (
    <Layout>
      <PageHeader
        title="Dashboard"
        subtitle="Last 30 days"
      />

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-muted)]">Loading...</p>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
            <StatCard label="Total Calls" value={fmtNum(stats?.total_calls ?? 0)} />
            <StatCard label="Total Tokens" value={fmtNum(stats?.total_tokens ?? 0)} />
            <StatCard
              label="Total Cost"
              value={fmtCost(stats?.total_cost ?? null)}
              sub="USD"
            />
            <StatCard
              label="Models"
              value={stats?.by_model.length ?? 0}
              sub="distinct"
            />
          </div>

          {/* Calls per day chart */}
          <div className="border border-[var(--color-border)] p-4 bg-[var(--color-surface)] mb-6">
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-4">
              Calls per day
            </h2>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={stats?.by_day ?? []}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                  tickFormatter={(v: string) => v.slice(5)} // MM-DD
                />
                <YAxis tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }} />
                <Tooltip
                  contentStyle={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    border: "1px solid var(--color-border-strong)",
                    borderRadius: 0,
                  }}
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
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {(stats?.by_model ?? []).map((row) => (
                  <tr key={row.model} className="hover:bg-[var(--color-bg-alt)]">
                    <td>
                      <Link
                        to="/logs"
                        search={{ model: row.model }}
                        className="no-underline hover:underline"
                      >
                        {row.model}
                      </Link>
                    </td>
                    <td className="tabular-nums">{fmtNum(row.calls)}</td>
                    <td className="tabular-nums">{fmtNum(row.total_tokens)}</td>
                    <td className="tabular-nums">{fmtCost(row.cost)}</td>
                  </tr>
                ))}
                {(stats?.by_model.length ?? 0) === 0 && (
                  <tr>
                    <td colSpan={4} className="text-center text-[var(--color-text-faint)] py-4">
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
