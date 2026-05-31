import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { Field } from "@/components/Field";

describe("Field", () => {
  it("renders label and input", () => {
    render(<Field label="Username" />);
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
  });

  it("shows error message", () => {
    render(<Field label="Password" error="Too short" />);
    expect(screen.getByText("Too short")).toBeInTheDocument();
  });

  it("calls onChange", async () => {
    const onChange = vi.fn();
    render(<Field label="Email" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText(/email/i), "a");
    expect(onChange).toHaveBeenCalled();
  });
});
