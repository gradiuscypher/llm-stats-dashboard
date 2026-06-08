/** Small badge displayed when a log entry or message has plugin modifications. */

interface ModificationBadgeProps {
  count: number;
  /** Optional list of plugin names for tooltip */
  pluginNames?: string[];
  size?: "sm" | "md";
}

export function ModificationBadge({ count, pluginNames, size = "sm" }: ModificationBadgeProps) {
  if (count <= 0) return null;

  const tooltip =
    pluginNames && pluginNames.length > 0
      ? `Modified by: ${pluginNames.join(", ")}`
      : `${count} modification${count !== 1 ? "s" : ""}`;

  const sizeClass = size === "md" ? "text-xs px-1.5 py-0.5" : "text-[10px] px-1 py-0.5";

  return (
    <span
      title={tooltip}
      className={`${sizeClass} font-bold uppercase tracking-wider border
        text-[var(--color-text)] border-[var(--color-accent)] bg-[var(--color-accent)]/10
        whitespace-nowrap select-none`}
    >
      ✎ {count}
    </span>
  );
}

/** Small inline label for messages modified by plugins. */
export function ModifiedByLabel({ pluginNames }: { pluginNames: string[] }) {
  if (pluginNames.length === 0) return null;

  return (
    <span
      title={`Modified by: ${pluginNames.join(", ")}`}
      className="ml-1 text-[9px] italic text-[var(--color-text-faint)]
                 border border-[var(--color-border)] px-1 select-none"
    >
      modified by {pluginNames.join(", ")}
    </span>
  );
}
