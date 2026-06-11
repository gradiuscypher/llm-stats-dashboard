/**
 * Pure helpers for deduplicating reasoning content.
 *
 * OpenRouter returns reasoning in two redundant forms:
 *   1. reasoning — flat concatenated text string
 *   2. reasoning_details — structured blocks (e.g. "reasoning.text" blocks
 *      whose concatenated text equals the flat reasoning string)
 *
 * Rendering both causes duplication. These helpers pick one representation.
 */

export interface ReasoningDetailBlock {
  type?: string;
  text?: string;
}

export interface ReasoningRenderDetails {
  mode: "details";
  blocks: ReasoningDetailBlock[];
  charCount: number;
}

export interface ReasoningRenderFlat {
  mode: "flat";
  text: string;
  charCount: number;
}

export type ReasoningRender = ReasoningRenderDetails | ReasoningRenderFlat | null;

/**
 * Decide which reasoning representation to render, and compute a char count.
 *
 * - When reasoning_details has at least one renderable block (text with content
 *   or an encrypted/redacted placeholder), render only the details and suppress
 *   the flat reasoning string. This avoids showing the same content twice.
 * - Otherwise, fall back to the flat reasoning string when present.
 * - Returns null when there is nothing to show.
 */
export function selectReasoningRender(
  reasoning: string | null | undefined,
  reasoningDetails: unknown[] | null | undefined
): ReasoningRender {
  const hasReasoning = typeof reasoning === "string" && reasoning.trim().length > 0;

  // Collect detail blocks that have something to render.
  const detailBlocks: ReasoningDetailBlock[] = [];
  if (Array.isArray(reasoningDetails)) {
    for (const block of reasoningDetails) {
      if (typeof block !== "object" || block === null) continue;
      const b = block as Record<string, unknown>;
      const type = typeof b.type === "string" ? b.type : "";
      const text = typeof b.text === "string" ? b.text : "";

      const isEncryptedOrRedacted = type.includes("encrypted") || type.includes("redacted");
      const hasText = text.length > 0;

      if (isEncryptedOrRedacted || hasText) {
        detailBlocks.push({ type, text });
      }
    }
  }

  if (detailBlocks.length > 0) {
    // Prefer details — compute char count from joined detail text.
    const charCount = detailBlocks.reduce((sum, b) => sum + (b.text?.length ?? 0), 0);
    return { mode: "details", blocks: detailBlocks, charCount };
  }

  if (hasReasoning) {
    return { mode: "flat", text: reasoning!, charCount: reasoning!.length };
  }

  return null;
}
