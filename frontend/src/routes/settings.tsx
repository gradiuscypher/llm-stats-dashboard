import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { useTheme, ThemePreference } from "@/lib/useTheme";
import { useFontSize, FontSizePreference, FONT_SIZE_LABELS } from "@/lib/useFontSize";

// ─── Reusable option button ──────────────────────────────────────────────────

function OptionButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "px-3 py-1.5 border text-xs font-bold uppercase tracking-wider cursor-pointer transition-colors bg-transparent",
        active
          ? "border-[var(--color-accent)] text-[var(--color-accent)] bg-[var(--color-surface)]"
          : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-text)] hover:text-[var(--color-text)]",
      ].join(" ")}
    >
      {children}
    </button>
  );
}

// ─── Section wrapper ─────────────────────────────────────────────────────────

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border border-[var(--color-border)] bg-[var(--color-surface)] mb-4">
      <div className="px-4 py-3 border-b border-[var(--color-border)]">
        <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
          {title}
        </h2>
        {description && (
          <p className="mt-0.5 text-xs text-[var(--color-text-faint)]">{description}</p>
        )}
      </div>
      <div className="px-4 py-4">{children}</div>
    </div>
  );
}

// ─── Settings page ───────────────────────────────────────────────────────────

const THEME_OPTIONS: { value: ThemePreference; label: string; description: string }[] = [
  { value: "system", label: "System", description: "Follow OS preference" },
  { value: "light", label: "Light", description: "Always light" },
  { value: "dark", label: "Dark", description: "Always dark" },
];

const FONT_SIZE_OPTIONS: { value: FontSizePreference }[] = [
  { value: "small" },
  { value: "medium" },
  { value: "large" },
];

export function SettingsPage() {
  const { preference: theme, setPreference: setTheme } = useTheme();
  const { preference: fontSize, setPreference: setFontSize } = useFontSize();

  return (
    <Layout>
      <PageHeader title="Settings" subtitle="Display &amp; appearance preferences" />

      {/* Theme */}
      <Section
        title="Theme"
        description="Choose between light, dark, or system-controlled appearance."
      >
        <div className="flex gap-2 flex-wrap">
          {THEME_OPTIONS.map((opt) => (
            <div key={opt.value} className="flex flex-col gap-1">
              <OptionButton active={theme === opt.value} onClick={() => setTheme(opt.value)}>
                {opt.label}
              </OptionButton>
              <span className="text-xs text-[var(--color-text-faint)] ml-0.5">
                {opt.description}
              </span>
            </div>
          ))}
        </div>
      </Section>

      {/* Font size */}
      <Section
        title="Font Size"
        description="Adjust the base font size. Changes apply immediately across the entire UI."
      >
        <div className="flex gap-2 flex-wrap items-end">
          {FONT_SIZE_OPTIONS.map((opt) => (
            <div key={opt.value} className="flex flex-col gap-1">
              <OptionButton active={fontSize === opt.value} onClick={() => setFontSize(opt.value)}>
                {opt.value}
              </OptionButton>
              <span className="text-xs text-[var(--color-text-faint)] ml-0.5">
                {FONT_SIZE_LABELS[opt.value]}
              </span>
            </div>
          ))}
        </div>

        {/* Live preview */}
        <div className="mt-4 border border-[var(--color-border)] p-3 bg-[var(--color-bg-alt)]">
          <p className="text-[var(--color-text-muted)] text-xs uppercase tracking-wider mb-1">
            Preview
          </p>
          <p>The quick brown fox jumps over the lazy dog.</p>
          <p className="text-[var(--color-text-muted)]">
            model: gpt-4o &nbsp;|&nbsp; tokens: 1,234 &nbsp;|&nbsp; cost: $0.003456
          </p>
        </div>
      </Section>
    </Layout>
  );
}
