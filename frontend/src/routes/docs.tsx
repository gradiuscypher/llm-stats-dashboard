import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { docsApi, DocIndex } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";

export function DocsPage() {
  const [activePath, setActivePath] = useState("index.md");

  const { data: index = [] } = useQuery({
    queryKey: ["docs-index"],
    queryFn: docsApi.list,
  });

  const { data: content, isLoading } = useQuery({
    queryKey: ["doc", activePath],
    queryFn: () => docsApi.get(activePath),
    enabled: !!activePath,
  });

  return (
    <Layout>
      <PageHeader title="Documentation" subtitle="AI-first Markdown docs" />

      <div className="flex gap-6">
        {/* Sidebar */}
        <aside className="w-48 shrink-0">
          <nav className="flex flex-col gap-0.5">
            {index.map((doc: DocIndex) => (
              <button
                key={doc.path}
                onClick={() => setActivePath(doc.path)}
                className={[
                  "text-left text-xs px-2 py-1.5 border-none cursor-pointer",
                  "font-mono uppercase tracking-wider",
                  activePath === doc.path
                    ? "bg-[var(--color-text)] text-[var(--color-surface)]"
                    : "bg-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-alt)]",
                ].join(" ")}
              >
                {doc.title}
              </button>
            ))}
          </nav>
        </aside>

        {/* Content */}
        <article className="flex-1 min-w-0">
          {isLoading ? (
            <p className="text-sm muted">Loading...</p>
          ) : (
            <div className="prose-custom">
              <ReactMarkdown>{content ?? ""}</ReactMarkdown>
            </div>
          )}
        </article>
      </div>
    </Layout>
  );
}
