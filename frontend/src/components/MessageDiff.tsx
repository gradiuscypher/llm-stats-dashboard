import { useMemo } from "react";

/** Display original→final content diff for a transformed message. */

interface MessageDiffProps {
  original: string;
  final: string;
  modifiedBy: string[];
  className?: string;
}

/** Simple word-level diff: mark words that differ between original and final. */
function computeWordDiff(
  original: string,
  final: string
): Array<{ text: string; kind: "same" | "added" | "removed" }> {
  const origWords = original.split(/(\s+)/);
  const finalWords = final.split(/(\s+)/);

  // Simple longest common subsequence on words.
  // Find the LCS to mark common parts, then fill in added/removed.
  const m = origWords.length;
  const n = finalWords.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (origWords[i - 1] === finalWords[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to build the diff.
  const result: Array<{ text: string; kind: "same" | "added" | "removed" }> = [];
  let i = m;
  let j = n;

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && origWords[i - 1] === finalWords[j - 1]) {
      result.unshift({ text: origWords[i - 1], kind: "same" });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ text: finalWords[j - 1], kind: "added" });
      j--;
    } else {
      result.unshift({ text: origWords[i - 1], kind: "removed" });
      i--;
    }
  }

  return result;
}

/** Fallback line-based comparison when content is long or word diff is noisy. */
function computeLineDiff(
  original: string,
  final: string
): Array<{ text: string; kind: "same" | "added" | "removed" }> {
  const origLines = original.split("\n");
  const finalLines = final.split("\n");

  if (origLines.length <= 1 && finalLines.length <= 1) {
    // Fall back to word diff
    return computeWordDiff(original, final);
  }

  const maxLen = Math.max(origLines.length, finalLines.length);
  const result: Array<{ text: string; kind: "same" | "added" | "removed" }> = [];

  for (let i = 0; i < maxLen; i++) {
    const o = origLines[i];
    const f = finalLines[i];

    if (o === f) {
      result.push({ text: o, kind: "same" });
    } else {
      if (o !== undefined) result.push({ text: o, kind: "removed" });
      if (f !== undefined) result.push({ text: f, kind: "added" });
    }
  }

  return result;
}

export function MessageDiff({ original, final, modifiedBy, className = "" }: MessageDiffProps) {
  const parts = useMemo(() => {
    if (original.includes("\n") || final.includes("\n")) {
      return computeLineDiff(original, final);
    }
    return computeWordDiff(original, final);
  }, [original, final]);

  const hasDiff = parts.some((p) => p.kind !== "same");

  if (!hasDiff) {
    return <span className={className}>{final}</span>;
  }

  return (
    <div className={`${className} rounded border border-[var(--color-accent)]/30 p-2`}>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
        Request diff <span className="italic normal-case">modified by {modifiedBy.join(", ")}</span>
      </div>

      <div className="whitespace-pre-wrap break-words font-mono text-sm">
        {parts.map((part, i) => {
          if (part.kind === "added") {
            return (
              <span key={i} className="bg-green-500/20 text-green-400 underline" title="Added">
                {part.text}
              </span>
            );
          }
          if (part.kind === "removed") {
            return (
              <span key={i} className="bg-red-500/20 text-red-400 line-through" title="Removed">
                {part.text}
              </span>
            );
          }
          return <span key={i}>{part.text}</span>;
        })}
      </div>

      {/* Legend */}
      <div className="mt-1 flex gap-3 text-[10px] text-[var(--color-text-faint)]">
        <span>
          <span className="inline-block w-3 h-3 bg-green-500/20 border border-green-500/30 mr-0.5 align-middle" />{" "}
          added
        </span>
        <span>
          <span className="inline-block w-3 h-3 bg-red-500/20 border border-red-500/30 mr-0.5 align-middle" />
          removed
        </span>
      </div>
    </div>
  );
}

/** Small badge showing a message has been modified and by whom. */
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
