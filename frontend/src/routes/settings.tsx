import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { useTheme, ThemePreference } from "@/lib/useTheme";
import { useFontSize, FontSizePreference, FONT_SIZE_LABELS } from "@/lib/useFontSize";
import { useShowDiff } from "@/lib/useShowDiff";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { pluginsApi } from "@/lib/api";

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
  const [showDiff, setShowDiff] = useShowDiff();
  const queryClient = useQueryClient();

  const { data: plugins = [], isLoading: pluginsLoading } = useQuery({
    queryKey: ["plugins"],
    queryFn: pluginsApi.list,
  });

  const toggleMutation = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      pluginsApi.setGlobal(name, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["plugins"] });
    },
  });

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

      {/* Request Diffs */}
      <Section
        title="Show Request Diffs"
        description="When enabled, transcripts and log details show original vs transformed content for messages modified by proxy plugins."
      >
        <OptionButton active={showDiff} onClick={() => setShowDiff(!showDiff)}>
          {showDiff ? "ON" : "OFF"}
        </OptionButton>
      </Section>

      {/* Proxy Plugins */}
      <Section
        title="Proxy Plugins"
        description="Enable or disable proxy plugins. Changes apply to all future conversations unless overridden per-conversation."
      >
        {pluginsLoading ? (
          <p className="text-xs text-[var(--color-text-faint)]">Loading…</p>
        ) : (
          <div className="flex flex-col gap-3">
            {plugins.map((plugin) => (
              <div
                key={plugin.name}
                className="flex items-center justify-between border border-[var(--color-border)] p-3"
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text)]">
                      {plugin.name}
                    </span>
                    {plugin.default_enabled && (
                      <span className="text-[9px] text-[var(--color-text-faint)] border border-[var(--color-border)] px-1">
                        DEFAULT
                      </span>
                    )}
                    {plugin.locked && (
                      <span className="text-[9px] text-[var(--color-accent)] border border-[var(--color-accent)] px-1">
                        LOCKED
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                    {plugin.description}
                  </p>
                </div>
                <label className="flex items-center gap-2 cursor-pointer ml-3">
                  {plugin.locked ? (
                    <span className="w-9 h-5 rounded-full bg-[var(--color-accent)] relative cursor-not-allowed">
                      <span className="absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white block" />
                    </span>
                  ) : (
                    <>
                      <input
                        type="checkbox"
                        className="sr-only"
                        checked={
                          plugin.user_enabled !== null
                            ? plugin.user_enabled
                            : plugin.default_enabled
                        }
                        onChange={(e) =>
                          toggleMutation.mutate({
                            name: plugin.name,
                            enabled: e.target.checked,
                          })
                        }
                      />
                      <span
                        className={`w-9 h-5 rounded-full relative transition-colors ${
                          (
                            plugin.user_enabled !== null
                              ? plugin.user_enabled
                              : plugin.default_enabled
                          )
                            ? "bg-[var(--color-accent)]"
                            : "bg-[var(--color-border)]"
                        }`}
                      >
                        <span
                          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                            (
                              plugin.user_enabled !== null
                                ? plugin.user_enabled
                                : plugin.default_enabled
                            )
                              ? "left-[calc(100%-1.125rem)]"
                              : "left-0.5"
                          }`}
                        />
                      </span>
                    </>
                  )}
                  <span className="text-xs text-[var(--color-text-muted)]">
                    {(plugin.user_enabled !== null ? plugin.user_enabled : plugin.default_enabled)
                      ? "On"
                      : "Off"}
                  </span>
                </label>
              </div>
            ))}
          </div>
        )}
      </Section>
    </Layout>
  );
}
