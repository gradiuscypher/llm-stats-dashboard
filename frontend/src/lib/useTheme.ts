/**
 * useTheme — manage light / dark / system theme preference.
 *
 * Stores the user's explicit choice in localStorage under "lsd-theme".
 * Possible values: "light" | "dark" | "system" (default).
 *
 * "system" removes the data-theme attribute so the CSS media query takes over.
 * "light" and "dark" set data-theme on <html> to override the media query.
 *
 * The initial script in index.html (see ThemeScript component) applies the
 * saved preference synchronously before first paint to avoid flash.
 */

import { useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "lsd-theme";

function applyTheme(pref: ThemePreference): void {
  const root = document.documentElement;
  if (pref === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", pref);
  }
}

function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      return stored;
    }
  } catch {
    // localStorage unavailable (SSR guard, private browsing, etc.)
  }
  return "system";
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);

  // Apply on mount and whenever preference changes
  useEffect(() => {
    applyTheme(preference);
    try {
      localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      // ignore
    }
  }, [preference]);

  /**
   * Cycle through system → light → dark → system, or jump directly.
   */
  const cycle = () =>
    setPreference((p) => (p === "system" ? "light" : p === "light" ? "dark" : "system"));

  return { preference, setPreference, cycle };
}

/**
 * Inline script string — paste into index.html <head> to apply theme
 * before first paint (no flash of wrong theme).
 */
export const THEME_INIT_SCRIPT = `
(function(){
  try {
    var p = localStorage.getItem('${STORAGE_KEY}');
    if (p === 'light' || p === 'dark') {
      document.documentElement.setAttribute('data-theme', p);
    }
  } catch(e) {}
})();
`.trim();
