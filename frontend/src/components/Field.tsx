import { InputHTMLAttributes, ReactNode } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  hint?: ReactNode;
}

export function Field({ label, error, hint, id, className = "", ...props }: FieldProps) {
  const fieldId = id ?? label.toLowerCase().replace(/\s+/g, "-");
  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor={fieldId}
        className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]"
      >
        {label}
      </label>
      <input
        id={fieldId}
        {...props}
        className={[
          "bg-[var(--color-surface)] border border-[var(--color-border)] px-2 py-1.5",
          "font-mono text-sm text-[var(--color-text)]",
          "focus:outline-none focus:border-[var(--color-text)]",
          "placeholder:text-[var(--color-text-faint)]",
          error ? "border-[var(--color-danger)]" : "",
          className,
        ].join(" ")}
      />
      {error && <p className="text-xs text-[var(--color-danger)]">{error}</p>}
      {hint && !error && <p className="text-xs text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}
