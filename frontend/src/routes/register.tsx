import { useState } from "react";
import { useRouter } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { usersApi, ApiError } from "@/lib/api";
import { Button } from "@/components/Button";
import { Field } from "@/components/Field";

export function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({ username: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);

  const registerMutation = useMutation({
    mutationFn: () => usersApi.register(form),
    onSuccess: () => void router.navigate({ to: "/login" }),
    onError: (err) => setError(err instanceof ApiError ? err.detail : "Registration failed"),
  });

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg)]">
      <div className="w-full max-w-sm border border-[var(--color-border-strong)] p-8 bg-[var(--color-surface)]">
        <h1 className="text-base font-bold uppercase tracking-widest mb-1">Create Account</h1>
        <p className="text-xs text-[var(--color-text-muted)] mb-6">LLM Stats Dashboard</p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            registerMutation.mutate();
          }}
          className="flex flex-col gap-4"
        >
          <Field
            label="Username"
            type="text"
            autoComplete="username"
            value={form.username}
            onChange={set("username")}
            required
          />
          <Field
            label="Email (optional)"
            type="email"
            autoComplete="email"
            value={form.email}
            onChange={set("email")}
          />
          <Field
            label="Password"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set("password")}
            required
          />

          {error && (
            <p className="text-xs text-[var(--color-danger)] border border-[var(--color-danger)] px-2 py-1.5">
              {error}
            </p>
          )}

          <Button type="submit" loading={registerMutation.isPending} className="mt-2 w-full">
            Create account
          </Button>
        </form>

        <div className="mt-4 pt-4 border-t border-[var(--color-border)] text-xs text-[var(--color-text-muted)]">
          Already have an account?{" "}
          <a href="/login" className="text-[var(--color-accent)]">
            Sign in
          </a>
        </div>
      </div>
    </div>
  );
}
