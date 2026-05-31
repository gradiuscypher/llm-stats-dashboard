import { useTheme, ThemePreference } from "@/lib/useTheme";

const LABELS: Record<ThemePreference, string> = {
  system: "SYS",
  light:  "LGT",
  dark:   "DRK",
};

const TITLES: Record<ThemePreference, string> = {
  system: "Theme: following system — click for light",
  light:  "Theme: light — click for dark",
  dark:   "Theme: dark — click for system",
};

export function ThemeToggle() {
  const { preference, cycle } = useTheme();

  return (
    <button
      onClick={cycle}
      title={TITLES[preference]}
      aria-label={TITLES[preference]}
      className={[
        "font-mono font-bold text-xs uppercase tracking-wider",
        "px-1.5 py-0.5 border cursor-pointer transition-colors",
        "bg-transparent",
        preference === "dark"
          ? "border-[var(--color-accent)] text-[var(--color-accent)]"
          : "border-[var(--color-border-strong)] text-[var(--color-text-muted)]",
        "hover:border-[var(--color-text)] hover:text-[var(--color-text)]",
      ].join(" ")}
    >
      {LABELS[preference]}
    </button>
  );
}
