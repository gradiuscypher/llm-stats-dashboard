import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach } from "vitest";
import { ThemeToggle } from "@/components/ThemeToggle";

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = v;
    },
    removeItem: (k: string) => {
      delete store[k];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(window, "localStorage", { value: localStorageMock });

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorageMock.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("renders with SYS label by default", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /system/i })).toBeInTheDocument();
    expect(screen.getByText("SYS")).toBeInTheDocument();
  });

  it("cycles system → light on first click", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByText("LGT")).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("cycles light → dark on second click", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button")); // → light
    await userEvent.click(screen.getByRole("button")); // → dark
    expect(screen.getByText("DRK")).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("cycles dark → system on third click", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button")); // → light
    await userEvent.click(screen.getByRole("button")); // → dark
    await userEvent.click(screen.getByRole("button")); // → system
    expect(screen.getByText("SYS")).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("persists preference to localStorage", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button")); // → light
    expect(localStorageMock.getItem("lsd-theme")).toBe("light");
    await userEvent.click(screen.getByRole("button")); // → dark
    expect(localStorageMock.getItem("lsd-theme")).toBe("dark");
  });
});
