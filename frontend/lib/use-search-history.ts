"use client";

/**
 * useSearchHistory — localStorage-backed list of recent searches.
 *
 * Scope: per-browser, NOT per-upload — the history surface lets users
 * jump between uploads' searches in one place. Each entry remembers the
 * uploadId so the UI can label the source chat.
 *
 * Capacity: capped at 10 entries (per spec). New searches push to the
 * front; duplicates of the most-recent (same query + same uploadId)
 * collapse so re-running a query doesn't generate noise.
 *
 * Persistence: synced to `localStorage` on every change. We hydrate
 * lazily on the first browser tick to avoid the SSR mismatch warning
 * Next would otherwise log when the server-rendered (empty) state
 * differs from the client's hydrated state.
 */

import { useCallback, useEffect, useState } from "react";

import type { SearchHistoryItem } from "./types";

const STORAGE_KEY = "chatlens.search.history.v1";
const MAX_ITEMS = 10;

export interface UseSearchHistory {
  history: SearchHistoryItem[];
  push: (item: Omit<SearchHistoryItem, "at">) => void;
  remove: (query: string, uploadId: string) => void;
  clear: () => void;
}

export function useSearchHistory(): UseSearchHistory {
  const [history, setHistory] = useState<SearchHistoryItem[]>([]);

  // Hydrate after mount. Wrapping in `useEffect` (not initializing
  // useState lazily) guarantees server and client render the same empty
  // list on the first paint, then update once we've read storage.
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as unknown;
      if (!Array.isArray(parsed)) return;
      const cleaned = parsed
        .filter(_isValidEntry)
        .slice(0, MAX_ITEMS);
      setHistory(cleaned);
    } catch {
      /* corrupt entry — ignore and let it overwrite on next push */
    }
  }, []);

  const persist = useCallback((next: SearchHistoryItem[]) => {
    setHistory(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Storage can throw in private mode / when full. Ignore — the
      // in-memory copy still works for this session.
    }
  }, []);

  const push = useCallback(
    (item: Omit<SearchHistoryItem, "at">) => {
      const entry: SearchHistoryItem = { ...item, at: Date.now() };
      setHistory((prev) => {
        // Drop any prior entry for (query, uploadId) so the most recent
        // wins and we don't duplicate.
        const filtered = prev.filter(
          (e) => !(e.query === entry.query && e.uploadId === entry.uploadId),
        );
        const next = [entry, ...filtered].slice(0, MAX_ITEMS);
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        } catch {
          /* see above */
        }
        return next;
      });
    },
    [],
  );

  const remove = useCallback(
    (query: string, uploadId: string) => {
      setHistory((prev) => {
        const next = prev.filter(
          (e) => !(e.query === query && e.uploadId === uploadId),
        );
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        } catch {
          /* see above */
        }
        return next;
      });
    },
    [],
  );

  const clear = useCallback(() => {
    persist([]);
  }, [persist]);

  return { history, push, remove, clear };
}

function _isValidEntry(x: unknown): x is SearchHistoryItem {
  if (!x || typeof x !== "object") return false;
  const e = x as Record<string, unknown>;
  return (
    typeof e.query === "string" &&
    typeof e.uploadId === "string" &&
    typeof e.at === "number" &&
    (e.mode === "ai" || e.mode === "find")
  );
}
