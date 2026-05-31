interface StatusBadgeProps {
  status: string;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const isError = status === "error";
  return (
    <span
      className={[
        "text-xs font-bold uppercase tracking-wider px-1.5 py-0.5 border",
        isError
          ? "text-[var(--color-danger)] border-[var(--color-danger)]"
          : "text-[var(--color-accent)] border-[var(--color-accent)]",
      ].join(" ")}
    >
      {status}
    </span>
  );
}
