/**
 * useFontSize — manage the base font-size preference.
 *
 * Stores the user's choice in localStorage under "lsd-font-size".
 * Possible values: "small" | "medium" | "large"
 *
 * Applies the size as a CSS custom property --font-size-base on <html>,
 * which global.css reads via `font-size: var(--font-size-base, 15px)`.
 */

import { useEffect, useState } from "react";

export type FontSizePreference = "small" | "medium" | "large";

const STORAGE_KEY = "lsd-font-size";

const SIZE_MAP: Record<FontSizePreference, string> = {
  small: "16px",
  medium: "20px",
  large: "24px",
};

export const FONT_SIZE_LABELS: Record<FontSizePreference, string> = {
  small: "Small (16px)",
  medium: "Medium (20px)",
  large: "Large (24px)",
};

function applyFontSize(pref: FontSizePreference): void {
  document.documentElement.style.setProperty("--font-size-base", SIZE_MAP[pref]);
}

function readPreference(): FontSizePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "small" || stored === "medium" || stored === "large") {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }
  return "medium";
}

export function useFontSize() {
  const [preference, setPreference] = useState<FontSizePreference>(readPreference);

  useEffect(() => {
    applyFontSize(preference);
    try {
      localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      // ignore
    }
  }, [preference]);

  return { preference, setPreference };
}

/**
 * Inline script string — paste into index.html <head> to apply font size
 * before first paint (no flash of wrong size).
 */
export const FONT_SIZE_INIT_SCRIPT = `
(function(){
  var map = { small: '16px', medium: '20px', large: '24px' };
  try {
    var p = localStorage.getItem('${STORAGE_KEY}');
    if (p && map[p]) {
      document.documentElement.style.setProperty('--font-size-base', map[p]);
    }
  } catch(e) {}
})();
`.trim();
