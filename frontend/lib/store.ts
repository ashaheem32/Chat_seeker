"use client";

/**
 * Global app store (Zustand).
 *
 * Scope:
 *   - Currently-viewed upload (UCJ meta, processing status)
 *   - Dashboard stats (lazy-fetched on dashboard mount)
 *   - Search results cache (latest query → response)
 *   - Sidebar collapsed state (persisted via partial hydration in the layout)
 *
 * What this store IS NOT for:
 *   - Per-component fetch state (use SWR / local state)
 *   - The upload-flow state machine (lives in useUploadFlow — single page)
 *
 * Action design:
 *   We expose granular setters rather than a single mega-action so React
 *   only re-renders subscribers that actually depend on the slice that
 *   changed. Selectors are exported below as thin wrappers around the
 *   default useStore hook for ergonomic call sites.
 */

import { create } from "zustand";

import type {
  ChatMeta,
  DashboardStats,
  OverviewStats,
  ProcessingStage,
  SearchResponse,
} from "./types";

interface CurrentUpload {
  uploadId: string;
  filename: string;
  meta: ChatMeta | null;
  status: ProcessingStage;
  /** Optional human detail surfaced from the WebSocket progress feed. */
  stageDetail?: string;
}

interface SearchCacheEntry {
  query: string;
  response: SearchResponse;
  /** Unix ms of when this response was received — useful for "stale" indicators. */
  fetchedAt: number;
}

interface AppState {
  // ---- Current upload ----
  currentUpload: CurrentUpload | null;
  setUpload: (
    upload:
      | CurrentUpload
      | ((prev: CurrentUpload | null) => CurrentUpload | null),
  ) => void;
  updateStatus: (
    status: ProcessingStage,
    stageDetail?: string,
  ) => void;
  clearUpload: () => void;

  // ---- Dashboard stats ----
  dashboardStats: Record<string, DashboardStats>;
  setStats: (uploadId: string, stats: DashboardStats) => void;

  // ---- Stats overview (StatsOverview module) ----
  overviewStats: Record<string, OverviewStats>;
  setOverviewStats: (uploadId: string, stats: OverviewStats) => void;

  // ---- Search ----
  /** Keyed by uploadId; we cache only the most recent query per upload. */
  searchResults: Record<string, SearchCacheEntry>;
  setSearchResult: (uploadId: string, query: string, response: SearchResponse) => void;
  clearSearch: (uploadId: string) => void;

  // ---- UI ----
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const useStore = create<AppState>((set) => ({
  // ---- Current upload ----
  currentUpload: null,
  setUpload: (upload) =>
    set((state) => ({
      currentUpload:
        typeof upload === "function" ? upload(state.currentUpload) : upload,
    })),
  updateStatus: (status, stageDetail) =>
    set((state) =>
      state.currentUpload
        ? {
            currentUpload: {
              ...state.currentUpload,
              status,
              ...(stageDetail !== undefined ? { stageDetail } : {}),
            },
          }
        : state,
    ),
  clearUpload: () => set({ currentUpload: null }),

  // ---- Dashboard stats ----
  dashboardStats: {},
  setStats: (uploadId, stats) =>
    set((state) => ({
      dashboardStats: { ...state.dashboardStats, [uploadId]: stats },
    })),

  overviewStats: {},
  setOverviewStats: (uploadId, stats) =>
    set((state) => ({
      overviewStats: { ...state.overviewStats, [uploadId]: stats },
    })),

  // ---- Search ----
  searchResults: {},
  setSearchResult: (uploadId, query, response) =>
    set((state) => ({
      searchResults: {
        ...state.searchResults,
        [uploadId]: { query, response, fetchedAt: Date.now() },
      },
    })),
  clearSearch: (uploadId) =>
    set((state) => {
      // Avoid allocating a new object if the entry isn't present.
      if (!state.searchResults[uploadId]) return state;
      const next = { ...state.searchResults };
      delete next[uploadId];
      return { searchResults: next };
    }),

  // ---- UI ----
  sidebarCollapsed: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
}));

// ---- Selectors ------------------------------------------------------------
// These are stable selectors that return primitive values or shared object
// refs; using them prevents unnecessary re-renders when unrelated slices change.

export const useCurrentUpload = () => useStore((s) => s.currentUpload);
export const useDashboardStats = (uploadId: string) =>
  useStore((s) => s.dashboardStats[uploadId]);
export const useOverviewStats = (uploadId: string) =>
  useStore((s) => s.overviewStats[uploadId]);
export const useSearchResult = (uploadId: string) =>
  useStore((s) => s.searchResults[uploadId]);
export const useSidebarCollapsed = () => useStore((s) => s.sidebarCollapsed);
