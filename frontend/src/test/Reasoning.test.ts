import { describe, it, expect } from "vitest";
import { selectReasoningRender } from "@/lib/reasoning";

describe("selectReasoningRender", () => {
  // ── Null / empty ──────────────────────────────────────────────────────────

  it("returns null when both reasoning and details are null", () => {
    expect(selectReasoningRender(null, null)).toBeNull();
  });

  it("returns null when both are empty", () => {
    expect(selectReasoningRender("", [])).toBeNull();
  });

  it("returns null when reasoning is whitespace and details is empty", () => {
    expect(selectReasoningRender("   ", [])).toBeNull();
  });

  // ── Flat reasoning only ────────────────────────────────────────────────────

  it("returns flat mode when only reasoning is present", () => {
    const result = selectReasoningRender("hello world", null);
    expect(result).toEqual({ mode: "flat", text: "hello world", charCount: 11 });
  });

  it("returns flat mode when details is empty array", () => {
    const result = selectReasoningRender("hello", []);
    expect(result).toEqual({ mode: "flat", text: "hello", charCount: 5 });
  });

  it("returns flat mode when details has no renderable blocks", () => {
    const result = selectReasoningRender("hello", [{ type: "other", text: "" }, {}, null]);
    expect(result).toEqual({ mode: "flat", text: "hello", charCount: 5 });
  });

  // ── Details preferred — both present (dedup) ──────────────────────────────

  it("returns details mode when both reasoning and details present", () => {
    const result = selectReasoningRender("Let me think: 2+2=4", [
      { index: 0, type: "reasoning.text", text: "Let me think:" },
      { index: 1, type: "reasoning.text", text: "2+2=4" },
    ]);
    expect(result?.mode).toBe("details");
    expect(result).toMatchObject({
      mode: "details",
      charCount: 18, // "Let me think:" (13) + "2+2=4" (5)
    });
    expect(result!.mode === "details" && result!.blocks).toHaveLength(2);
  });

  // ── Encrypted / redacted blocks ───────────────────────────────────────────

  it("returns details mode for encrypted blocks only", () => {
    const result = selectReasoningRender(null, [
      { type: "reasoning.encrypted", text: "base64blob==" },
    ]);
    expect(result?.mode).toBe("details");
    if (result?.mode === "details") {
      expect(result.blocks).toHaveLength(1);
      expect(result.blocks[0].type).toBe("reasoning.encrypted");
      expect(result.blocks[0].text).toBe("base64blob==");
    }
  });

  it("returns details mode for redacted blocks only", () => {
    const result = selectReasoningRender(null, [
      { type: "reasoning.redacted", text: "[redacted]" },
    ]);
    expect(result?.mode).toBe("details");
    if (result?.mode === "details") {
      expect(result.blocks).toHaveLength(1);
      expect(result.blocks[0].type).toBe("reasoning.redacted");
    }
  });

  it("returns details for mixed text + encrypted blocks", () => {
    const result = selectReasoningRender("Visible reasoning text", [
      { type: "reasoning.text", text: "Visible reasoning text" },
      { type: "reasoning.encrypted", text: "enc==" },
    ]);
    expect(result?.mode).toBe("details");
    if (result?.mode === "details") {
      expect(result.blocks).toHaveLength(2);
      // charCount from detail block text (encrypted block text contributes too
      // — that's fine, the count is approximate and for display only)
    }
  });

  // ── Char count ────────────────────────────────────────────────────────────

  it("computes charCount from joined detail text when in details mode", () => {
    const result = selectReasoningRender("unused flat", [
      { type: "reasoning.text", text: "abc" },
      { type: "reasoning.text", text: "def" },
    ]);
    expect(result?.mode).toBe("details");
    if (result?.mode === "details") {
      expect(result.charCount).toBe(6);
    }
  });
});
