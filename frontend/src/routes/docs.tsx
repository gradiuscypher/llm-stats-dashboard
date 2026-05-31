import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
      <PageHeader
        title="Documentation"
        subtitle="AI-first Markdown docs — publicly accessible, no login required"
      />

      {/* Raw API note for coding agents */}
      <div className="mb-4 text-xs text-[var(--color-text-muted)] border border-[var(--color-border)] px-3 py-2 bg-[var(--color-bg-alt)]">
        <span className="font-bold text-[var(--color-text)]">Coding agent?</span>{" "}
        Fetch docs directly:{" "}
        <code className="bg-[var(--color-code-bg)] px-1">GET /api/v1/docs-md</code>{" "}for the index,{" "}
        <code className="bg-[var(--color-code-bg)] px-1">GET /api/v1/docs-md/&#123;path&#125;</code>{" "}for raw Markdown.{" "}
        Start with{" "}
        <code className="bg-[var(--color-code-bg)] px-1">GET /api/v1/docs-md/ai-client-guide.md</code>.
      </div>

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
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content ?? ""}</ReactMarkdown>
            </div>
          )}
        </article>
      </div>
    </Layout>
  );
}
