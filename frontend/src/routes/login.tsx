import { useState } from "react";
import { useRouter } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { authApi, ApiError } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { Button } from "@/components/Button";
import { Field } from "@/components/Field";

export function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loginMutation = useMutation({
    mutationFn: () => authApi.login(username, password),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      void router.navigate({ to: "/" });
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.detail : "Login failed");
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    loginMutation.mutate();
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-bg)]">
      <div className="w-full max-w-sm border border-[var(--color-border-strong)] p-8 bg-[var(--color-surface)]">
        <h1 className="text-base font-bold uppercase tracking-widest mb-1">
          LLM Stats Dashboard
        </h1>
        <p className="text-xs text-[var(--color-text-muted)] mb-6">Sign in to continue.</p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Field
            label="Username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          {error && (
            <p className="text-xs text-[var(--color-danger)] border border-[var(--color-danger)] px-2 py-1.5">
              {error}
            </p>
          )}

          <Button type="submit" loading={loginMutation.isPending} className="mt-2 w-full">
            Sign in
          </Button>
        </form>

        <div className="mt-4 pt-4 border-t border-[var(--color-border)] text-xs text-[var(--color-text-muted)]">
          No account?{" "}
          <a href="/register" className="text-[var(--color-accent)]">
            Register
          </a>
        </div>
      </div>
    </div>
  );
}
