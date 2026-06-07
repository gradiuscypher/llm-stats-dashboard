import { ReactNode } from "react";
import { Link, useRouter } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { authApi } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { ThemeToggle } from "@/components/ThemeToggle";

interface LayoutProps {
  children: ReactNode;
}

const NAV_LINKS = [
  { to: "/", label: "Dashboard" },
  { to: "/logs", label: "Logs" },
  { to: "/conversations", label: "Conversations" },
  { to: "/api-keys", label: "API Keys" },
  { to: "/docs", label: "Docs" },
  { to: "/settings", label: "Settings" },
];

export function Layout({ children }: LayoutProps) {
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: authApi.me });
  const router = useRouter();

  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      queryClient.clear();
      void router.navigate({ to: "/login" });
    },
  });

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top bar */}
      <header className="border-b border-[var(--color-border-strong)] bg-[var(--color-surface)]">
        <div className="max-w-7xl mx-auto px-4 flex items-center h-10 gap-6">
          <span className="font-bold text-xs uppercase tracking-widest text-[var(--color-accent)]">
            LSD
          </span>
          <nav className="flex items-center gap-4 flex-1">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                activeOptions={{ exact: true }}
                className="text-xs font-bold uppercase tracking-wider no-underline text-[var(--color-text-muted)] hover:text-[var(--color-text)] [&.active]:text-[var(--color-text)] [&.active]:border-b [&.active]:border-[var(--color-text)]"
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
            <ThemeToggle />
            {user ? (
              <>
                <span>{user.username}</span>
                <button
                  onClick={() => logoutMutation.mutate()}
                  className="font-bold uppercase tracking-wider hover:text-[var(--color-danger)] cursor-pointer bg-transparent border-none"
                >
                  Logout
                </button>
              </>
            ) : (
              <Link
                to="/login"
                className="font-bold uppercase tracking-wider no-underline hover:text-[var(--color-accent)]"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6">{children}</main>

      {/* Footer */}
      <footer className="border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-text-faint)]">
        LLM Stats Dashboard
      </footer>
    </div>
  );
}
