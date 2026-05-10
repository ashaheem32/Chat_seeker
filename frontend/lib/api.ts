/**
 * Typed API client for the ChatLens backend.
 *
 * Endpoint shape (backend mounts these under /api/v1):
 *   POST /upload                  multipart upload, returns parsed meta
 *   GET  /upload/{id}             status poll
 *   GET  /upload/{id}/ucj         full UCJ payload (preview / download)
 *   WS   /upload/ws/{id}          server-pushed progress events
 *
 * The browser uses `/api/backend/*` (proxied by Next's rewrite to FastAPI).
 * Server-side rendering hits the backend directly via BACKEND_URL.
 */

import axios, { AxiosError, type AxiosInstance } from "axios";

import type {
  BigramsResponse,
  ConflictAnalysisResponse,
  ConflictThemesResponse,
  DashboardData,
  DashboardStats,
  DetailedParticipantStats,
  EmojiFrequencyResponse,
  EmotionByParticipant,
  EmotionDistribution,
  EmotionalPeaks,
  Granularity,
  HealthScoreReport,
  LateNightStats,
  LoveLanguageReport,
  MessageContextResponse,
  MoodCalendar,
  OverviewStats,
  SearchFilters,
  SearchResponse,
  SentimentTimeline,
  StreamEvent,
  SuggestionsResponse,
  UCJFile,
  UniqueWordsResponse,
  UploadProgressEvent,
  UploadResponse,
  UploadStatus,
  WordFrequency,
  WordTrend,
} from "./types";

const API_PREFIX = "/api/v1";

/** Browser-visible base URL (uses the Next rewrite proxy if no override). */
const baseURL =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined" ? "/api/backend" : "http://backend:8000");

export const apiClient: AxiosInstance = axios.create({
  baseURL,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

// Surface FastAPI's `detail` field as the friendly axios message.
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    const detail = error.response?.data?.detail;
    if (detail) error.message = detail;
    return Promise.reject(error);
  },
);

// ---- Uploads ---------------------------------------------------------------

/**
 * Upload a chat export file. Pass `platform` to skip auto-detection (useful
 * if a previous upload returned a low-confidence guess and the user picked
 * the platform manually).
 */
export async function uploadChat(
  file: File,
  options: { platform?: string; signal?: AbortSignal } = {},
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);

  const params = options.platform ? { platform: options.platform } : undefined;

  const { data } = await apiClient.post<UploadResponse>(
    `${API_PREFIX}/upload`,
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      // Big chats can take a while to parse + persist; let parsing complete.
      timeout: 300_000,
      signal: options.signal,
      params,
    },
  );
  return data;
}

/**
 * Upload pasted text as if it were a file. Mirrors the JSX converter's
 * "paste mode" - we wrap the string in a Blob so the same backend endpoint
 * handles both flows.
 */
export async function uploadPastedText(
  text: string,
  filename = "pasted_chat.txt",
  options: { platform?: string; signal?: AbortSignal } = {},
): Promise<UploadResponse> {
  // Detect the most likely extension so the backend's filename hints fire.
  const trimmed = text.trim();
  const looksJson = trimmed.startsWith("{") || trimmed.startsWith("[");
  const inferred = looksJson ? "pasted_chat.json" : filename;

  const blob = new Blob([text], {
    type: looksJson ? "application/json" : "text/plain",
  });
  const file = new File([blob], inferred, { type: blob.type });
  return uploadChat(file, options);
}

export async function getUploadStatus(uploadId: string): Promise<UploadStatus> {
  const { data } = await apiClient.get<UploadStatus>(
    `${API_PREFIX}/upload/${uploadId}`,
  );
  return data;
}

/**
 * Fetch the full UCJ payload for an upload.
 *
 * @param limit  Cap the messages returned (oldest first). Use a small value
 *               like 50 for the converter's preview pane.
 */
export async function getUCJ(
  uploadId: string,
  limit?: number,
): Promise<UCJFile> {
  const { data } = await apiClient.get<UCJFile>(
    `${API_PREFIX}/upload/${uploadId}/ucj`,
    { params: limit ? { limit } : undefined },
  );
  return data;
}

/**
 * Browser-friendly download URL for the UCJ JSON file.
 *
 * Returning a URL (rather than a blob) lets the caller use a plain anchor
 * tag with `download` - browsers handle the streaming for us and big files
 * don't have to fit in memory.
 */
export function ucjDownloadUrl(uploadId: string): string {
  // baseURL may be relative (`/api/backend`) - in that case we let the
  // browser resolve it against window.location. Absolute URLs pass through.
  return `${baseURL}${API_PREFIX}/upload/${uploadId}/ucj?download=true`;
}

// ---- WebSocket progress feed ----------------------------------------------

/**
 * Subscribe to progress events for an upload.
 *
 * Returns a tear-down function the caller invokes when unmounting. The
 * socket auto-closes when the backend pushes a terminal event ("ready" /
 * "failed"), so callers usually only need the disposer for cleanup on
 * navigate-away.
 */
export function subscribeToProgress(
  uploadId: string,
  handler: (event: UploadProgressEvent) => void,
  options: { onError?: (err: Event) => void; onClose?: () => void } = {},
): () => void {
  const url = wsUrlFor(`${API_PREFIX}/upload/ws/${uploadId}`);
  const ws = new WebSocket(url);

  ws.onmessage = (e) => {
    try {
      handler(JSON.parse(e.data) as UploadProgressEvent);
    } catch {
      /* drop malformed frames - server should never send them */
    }
  };
  if (options.onError) ws.onerror = options.onError;
  if (options.onClose) ws.onclose = options.onClose;

  return () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  };
}

/**
 * Build a ws:// or wss:// URL from a relative path.
 *
 * Mirrors the http(s) protocol of the page so a deployment served over
 * https doesn't try to open an insecure ws:// (which browsers block).
 */
function wsUrlFor(path: string): string {
  if (typeof window === "undefined") {
    // Should never happen - WS is browser-only.
    return path;
  }
  // If NEXT_PUBLIC_API_URL is absolute (http://...), use that host.
  // Otherwise the Next rewrite proxy forwards /api/backend to the backend
  // service, including WS upgrades.
  const explicit = process.env.NEXT_PUBLIC_API_URL;
  if (explicit && /^https?:\/\//.test(explicit)) {
    const u = new URL(explicit);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = u.pathname.replace(/\/$/, "") + path;
    return u.toString();
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/backend${path}`;
}

// ---- Dashboard / chat reads ------------------------------------------------

export async function getChat(uploadId: string): Promise<UCJFile> {
  // Convenience alias - same payload as getUCJ but kept for readability where
  // callers think in terms of "the chat" rather than "the UCJ blob".
  return getUCJ(uploadId);
}

export async function getDashboardData(uploadId: string): Promise<DashboardData> {
  const { data } = await apiClient.get<DashboardData>(
    `${API_PREFIX}/chats/${uploadId}/dashboard`,
  );
  return data;
}

/**
 * Aggregated stats for the overview module — message counts, sentiment
 * trace, activity buckets, top topics, highlights. Backend computes this
 * once per chat (cached via AnalysisCache) so the route is fast on repeat hits.
 */
export async function getDashboardStats(uploadId: string): Promise<DashboardStats> {
  const { data } = await apiClient.get<DashboardStats>(
    `${API_PREFIX}/chats/${uploadId}/stats`,
  );
  return data;
}

// ---- File upload (alias to match the M04 module spec) --------------------

/**
 * Spec-aligned alias for `uploadChat`. Keeps the ergonomic name
 * `uploadFile(file, onProgress)` available; `onProgress` receives a
 * 0-100 number based on the multipart upload bytes-sent stream.
 */
export async function uploadFile(
  file: File,
  onProgress?: (percent: number) => void,
  options: { platform?: string; signal?: AbortSignal } = {},
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const params = options.platform ? { platform: options.platform } : undefined;

  const { data } = await apiClient.post<UploadResponse>(
    `${API_PREFIX}/upload`,
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300_000,
      signal: options.signal,
      params,
      onUploadProgress: onProgress
        ? (e) => {
            // axios emits progress events with `loaded` and (sometimes) `total`.
            // Guard against missing total (chunked transfer-encoding).
            if (!e.total) return;
            const percent = Math.min(100, Math.round((e.loaded / e.total) * 100));
            onProgress(percent);
          }
        : undefined,
    },
  );
  return data;
}

// ---- Stats ----------------------------------------------------------------

/**
 * Aggregate stats for the dashboard's "Overview" / StatsOverview module.
 *
 * Backend caches in Redis for 1h; pass `refresh=true` to bypass after a
 * re-analysis. The endpoint 409s while the chat is still parsing, so the
 * frontend should gate the call on upload status >= "ready".
 */
export async function getStatsOverview(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<OverviewStats> {
  const { data } = await apiClient.get<OverviewStats>(
    `${API_PREFIX}/stats/${uploadId}/overview`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

/** Detailed per-participant rollup (top words/emojis, hourly histogram, …). */
export async function getParticipantStats(
  uploadId: string,
  sender: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<DetailedParticipantStats> {
  const { data } = await apiClient.get<DetailedParticipantStats>(
    `${API_PREFIX}/stats/${uploadId}/participant/${encodeURIComponent(sender)}`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

// ---- Emotion timeline -----------------------------------------------------

export async function getSentimentTimeline(
  uploadId: string,
  granularity: Granularity = "day",
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<SentimentTimeline> {
  const { data } = await apiClient.get<SentimentTimeline>(
    `${API_PREFIX}/stats/${uploadId}/sentiment-timeline`,
    {
      params: {
        granularity,
        ...(options.refresh ? { refresh: true } : {}),
      },
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

export async function getEmotionDistribution(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<EmotionDistribution> {
  const { data } = await apiClient.get<EmotionDistribution>(
    `${API_PREFIX}/stats/${uploadId}/emotion-distribution`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
    },
  );
  return data;
}

export async function getEmotionByParticipant(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<EmotionByParticipant> {
  const { data } = await apiClient.get<EmotionByParticipant>(
    `${API_PREFIX}/stats/${uploadId}/emotion-by-participant`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
    },
  );
  return data;
}

export async function getEmotionalPeaks(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<EmotionalPeaks> {
  const { data } = await apiClient.get<EmotionalPeaks>(
    `${API_PREFIX}/stats/${uploadId}/emotional-peaks`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
    },
  );
  return data;
}

export async function getMoodCalendar(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<MoodCalendar> {
  const { data } = await apiClient.get<MoodCalendar>(
    `${API_PREFIX}/stats/${uploadId}/mood-calendar`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
    },
  );
  return data;
}

// ---- Words & emojis -------------------------------------------------------

export async function getWordFrequency(
  uploadId: string,
  options: {
    sender?: string;
    topN?: number;
    excludeStopwords?: boolean;
    refresh?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<WordFrequency> {
  const { data } = await apiClient.get<WordFrequency>(
    `${API_PREFIX}/stats/${uploadId}/word-frequency`,
    {
      params: {
        ...(options.sender ? { sender: options.sender } : {}),
        top_n: options.topN ?? 100,
        exclude_stopwords: options.excludeStopwords ?? true,
        ...(options.refresh ? { refresh: true } : {}),
      },
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

export async function getEmojiFrequency(
  uploadId: string,
  options: { sender?: string; topN?: number; refresh?: boolean; signal?: AbortSignal } = {},
): Promise<EmojiFrequencyResponse> {
  const { data } = await apiClient.get<EmojiFrequencyResponse>(
    `${API_PREFIX}/stats/${uploadId}/emoji-frequency`,
    {
      params: {
        ...(options.sender ? { sender: options.sender } : {}),
        top_n: options.topN ?? 30,
        ...(options.refresh ? { refresh: true } : {}),
      },
      signal: options.signal,
    },
  );
  return data;
}

export async function getBigrams(
  uploadId: string,
  options: { sender?: string; topN?: number; refresh?: boolean; signal?: AbortSignal } = {},
): Promise<BigramsResponse> {
  const { data } = await apiClient.get<BigramsResponse>(
    `${API_PREFIX}/stats/${uploadId}/bigrams`,
    {
      params: {
        ...(options.sender ? { sender: options.sender } : {}),
        top_n: options.topN ?? 20,
        ...(options.refresh ? { refresh: true } : {}),
      },
      signal: options.signal,
    },
  );
  return data;
}

export async function getUniqueWords(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<UniqueWordsResponse> {
  const { data } = await apiClient.get<UniqueWordsResponse>(
    `${API_PREFIX}/stats/${uploadId}/unique-words`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

export async function getWordTrend(
  uploadId: string,
  word: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<WordTrend> {
  const { data } = await apiClient.get<WordTrend>(
    `${API_PREFIX}/stats/${uploadId}/word-trend`,
    {
      params: { word, ...(options.refresh ? { refresh: true } : {}) },
      signal: options.signal,
    },
  );
  return data;
}

export async function getLateNightStats(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<LateNightStats> {
  const { data } = await apiClient.get<LateNightStats>(
    `${API_PREFIX}/stats/${uploadId}/late-night`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
    },
  );
  return data;
}

// ---- Conflict analysis ---------------------------------------------------

/**
 * Detected difficult-moment windows + summary + language patterns.
 * Themes are populated only when previously fetched via
 * `getConflictThemes` (which calls Claude).
 */
export async function getConflictAnalysis(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<ConflictAnalysisResponse> {
  const { data } = await apiClient.get<ConflictAnalysisResponse>(
    `${API_PREFIX}/stats/${uploadId}/conflicts`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

/**
 * Cluster the detected conflict windows into recurring themes (Claude).
 * Cached server-side for 24h. Falls back to a single bucket when no
 * Anthropic key is configured — `used_llm` is false in that case.
 */
export async function getConflictThemes(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<ConflictThemesResponse> {
  const { data } = await apiClient.get<ConflictThemesResponse>(
    `${API_PREFIX}/stats/${uploadId}/conflict-themes`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

// ---- Love languages + health score ---------------------------------------

/**
 * Per-sender love-language distributions classified via Claude. Cached
 * server-side for 24h. Falls back to a keyword heuristic when no
 * Anthropic key is configured (`used_llm: false`).
 */
export async function getLoveLanguageReport(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<LoveLanguageReport> {
  const { data } = await apiClient.get<LoveLanguageReport>(
    `${API_PREFIX}/stats/${uploadId}/love-language`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

/**
 * Composite communication-health score (0-100) with seven weighted
 * factors. Server-cached 24h. Falls back to deterministic templated
 * insights when Anthropic isn't available.
 */
export async function getHealthScoreReport(
  uploadId: string,
  options: { refresh?: boolean; signal?: AbortSignal } = {},
): Promise<HealthScoreReport> {
  const { data } = await apiClient.get<HealthScoreReport>(
    `${API_PREFIX}/stats/${uploadId}/health-score`,
    {
      params: options.refresh ? { refresh: true } : undefined,
      signal: options.signal,
      timeout: 60_000,
    },
  );
  return data;
}

// ---- Search ---------------------------------------------------------------

/**
 * Natural-language Q&A search. Backend runs the rephrase → vector search →
 * Claude pipeline; we surface the response shape directly.
 */
export async function search(
  uploadId: string,
  query: string,
  filters?: SearchFilters,
  topK?: number,
  options: { signal?: AbortSignal } = {},
): Promise<SearchResponse> {
  const { data } = await apiClient.post<SearchResponse>(
    `${API_PREFIX}/search/${uploadId}`,
    { query, filters, top_k: topK ?? 20 },
    { signal: options.signal, timeout: 60_000 },
  );
  return data;
}

/** Pre-canned query suggestions for the search panel. */
export async function getSearchSuggestions(
  uploadId: string,
): Promise<SuggestionsResponse> {
  const { data } = await apiClient.get<SuggestionsResponse>(
    `${API_PREFIX}/search/${uploadId}/suggestions`,
  );
  return data;
}

/**
 * Streaming variant of `search()`. Uses fetch + ReadableStream rather than
 * EventSource because EventSource can't POST a JSON body (filters / top_k).
 *
 * Calls `onEvent` once per parsed SSE event:
 *   - `meta`   — rephrased query + initial evidence list (rendered immediately)
 *   - `delta`  — text chunks (one per Anthropic streaming delta)
 *   - `done`   — final answer + cited_messages + confidence
 *   - `error`  — server-side failure; stream then closes
 *
 * Returns a disposer the caller invokes to cancel an in-flight stream
 * (e.g. on new query submission or unmount). The returned promise
 * resolves after the stream closes (clean or aborted).
 */
export async function streamSearch(
  uploadId: string,
  query: string,
  onEvent: (event: StreamEvent) => void,
  options: {
    filters?: SearchFilters;
    topK?: number;
    signal?: AbortSignal;
  } = {},
): Promise<void> {
  const url = `${baseURL}${API_PREFIX}/search/${uploadId}/stream`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      query,
      filters: options.filters,
      top_k: options.topK ?? 30,
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    // Try to surface FastAPI's `detail` field for parity with the axios path.
    let detail = `HTTP ${response.status}`;
    try {
      const j = (await response.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* not JSON — leave the default */
    }
    onEvent({ type: "error", message: detail });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // SSE protocol: events are separated by a blank line ("\n\n"). Each event
  // can have multiple `event:` / `data:` / `id:` lines. We parse only the
  // ones we emit (`event` + `data`).
  const flush = (raw: string) => {
    if (!raw.trim()) return;
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      // ignore id:, retry:, comments
    }
    if (dataLines.length === 0) return;
    try {
      const parsed = JSON.parse(dataLines.join("\n")) as StreamEvent;
      // Some servers send `data: {}` without an `event:` line; trust the
      // payload's `type` field if present, else fall back to the event name.
      if (!parsed.type && eventName) {
        (parsed as { type: string }).type = eventName;
      }
      onEvent(parsed);
    } catch (e) {
      // Malformed frame — ignore, keep streaming.
      // eslint-disable-next-line no-console
      console.warn("[search] dropped malformed SSE frame", e);
    }
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        flush(raw);
      }
    }
    if (buffer.trim()) flush(buffer);
  } catch (e) {
    // Abort errors are expected when the caller cancels — don't surface
    // them as backend errors.
    if ((e as { name?: string })?.name === "AbortError") return;
    onEvent({
      type: "error",
      message: e instanceof Error ? e.message : "Stream interrupted",
    });
  }
}

/** Fetch the messages immediately surrounding a target — feeds the
 *  context drawer launched from search hits. */
export async function getMessageContext(
  uploadId: string,
  messageId: string,
  options: { window?: number; signal?: AbortSignal } = {},
): Promise<MessageContextResponse> {
  const { data } = await apiClient.get<MessageContextResponse>(
    `${API_PREFIX}/search/${uploadId}/context/${messageId}`,
    {
      params: { window: options.window ?? 10 },
      signal: options.signal,
    },
  );
  return data;
}

// ---- WebSocket helper (spec-aligned alias) -------------------------------

/**
 * Convenience alias matching the M04 spec name. Internally routes to
 * `subscribeToProgress` so we don't have two parallel WebSocket clients.
 */
export function connectToProgress(
  uploadId: string,
  onMessage: (event: UploadProgressEvent) => void,
  options?: { onError?: (err: Event) => void; onClose?: () => void },
): () => void {
  return subscribeToProgress(uploadId, onMessage, options);
}
