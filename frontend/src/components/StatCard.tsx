interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
}

export function StatCard({ label, value, sub }: StatCardProps) {
  return (
    <div className="border border-[var(--color-border)] p-4 bg-[var(--color-surface)]">
      <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
        {label}
      </p>
      <p className="text-2xl font-bold tabular-nums">{value}</p>
      {sub && <p className="text-xs text-[var(--color-text-faint)] mt-1">{sub}</p>}
    </div>
  );
}
