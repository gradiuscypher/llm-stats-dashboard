import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "lsd-show-diff";

function getSnapshot(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === null) return false;
    return stored === "true";
  } catch {
    return false;
  }
}

function subscribe(callback: () => void): () => void {
  const handler = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) callback();
  };
  window.addEventListener("storage", handler);
  return () => window.removeEventListener("storage", handler);
}

function setValue(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(value));
    // storage events don't fire in the same tab, so dispatch manually
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
  } catch {
    // localStorage unavailable — ignore
  }
}

/**
 * Client-side preference for showing request diffs (original vs final content)
 * in conversation transcripts and log detail pages.
 */
export function useShowDiff(): [boolean, (v: boolean) => void] {
  const showDiff = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setShowDiff = useCallback((value: boolean) => setValue(value), []);
  return [showDiff, setShowDiff];
}
