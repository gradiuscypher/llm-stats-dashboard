import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "@/components/StatusBadge";

describe("StatusBadge", () => {
  it("renders ok status", () => {
    render(<StatusBadge status="ok" />);
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("renders error status with danger style", () => {
    const { container } = render(<StatusBadge status="error" />);
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(container.firstChild).toHaveClass("text-[var(--color-danger)]");
  });
});
