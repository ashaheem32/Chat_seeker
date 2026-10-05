"use client";

/**
 * SearchModule — Module 04 of the dashboard.
 *
 * Two modes:
 *   - "ai"    — streaming Q&A: Claude synthesizes an answer from semantic
 *               retrieval, citing specific [msg_X] tokens in the answer.
 *   - "find"  — pure semantic search results with optional filter sidebar.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────┐
 *   │ Mode toggle · Search bar · Submit            │
 *   ├──────────────────────────────────────────────┤
 *   │ Suggested queries (grid of pills)            │
 *   │ Search history (collapsible)                 │
 *   ├──────────────────────────────────────────────┤
 *   │ Results — answer card / message list         │
 *   └──────────────────────────────────────────────┘
 *
 * The streaming flow lives in `lib/api.ts::streamSearch`. We accumulate
 * deltas into a buffer, render them char-by-char, then replace the buffer
 * with the canonical `done` payload to ensure the displayed answer
 * matches what we cite from. Cancellation cancels the abort controller,
 * which closes the underlying ReadableStream.
 *
 * Context drawer: clicking "See in context" on any message opens a Radix
 * Dialog that slides in from the right and shows N=10 messages on each
 * side of the target. Built on `getMessageContext`.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Filter,
  Heart,
  History,
  MessageCircle,
  Search as SearchIcon,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  X,
  Zap,
} from "lucide-react";
import { parseISO } from "date-fns";

import { Button } from "@/components/ui/button";
import { ContextDrawer } from "@/components/dashboard/ContextDrawer";
import {
  search as semanticSearch,
  streamSearch,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import type {
  CitedMessage,
  EmotionClass,
  SearchFilters,
  SearchResult,
  StreamEvent,
  StreamEvidence,
} from "@/lib/types";
import { useSearchHistory } from "@/lib/use-search-history";
import { cn, safeFormatDate } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PLACEHOLDERS: string[] = [
  "How did he express love?",
  "What caused our fights?",
  "When did things start changing?",
  "What do we talk about most?",
  "How do we apologize to each other?",
  "What plans did we make together?",
];

const SUGGESTIONS: {
  category: string;
  icon: typeof MessageCircle;
  color: string;
  items: { label: string; query: string }[];
}[] = [
  {
    category: "Communication",
    icon: MessageCircle,
    color: "hsl(190 78% 35%)", // cyan
    items: [
      { label: "Who dominates conversations?", query: "Who dominates the conversations and who tends to listen?" },
      { label: "Response time patterns", query: "How does each person respond — quickly, slowly, in bursts?" },
    ],
  },
  {
    category: "Affection",
    icon: Heart,
    color: "hsl(340 69% 47%)", // pink
    items: [
      { label: "Most romantic messages", query: "What are the most romantic or affectionate messages?" },
      { label: "How affection shows up", query: "How does each person express affection in this conversation?" },
    ],
  },
  {
    category: "Conflict",
    icon: Zap,
    color: "hsl(var(--destructive))", // destructive
    items: [
      { label: "Common fight triggers", query: "What topics most often lead to disagreements?" },
      { label: "How we resolve arguments", query: "How are arguments resolved? Who apologizes first?" },
    ],
  },
  {
    category: "Patterns",
    icon: TrendingUp,
    color: "hsl(var(--accent))", // accent
    items: [
      { label: "When communication changed", query: "When did the communication patterns change, and how?" },
      { label: "Shared interests and topics", query: "What topics and interests do we share most?" },
    ],
  },
];

const EMOTION_COLORS: Record<EmotionClass, string> = {
  joy: "#b76e05",
  love: "#be185d",
  sadness: "#4338ca",
  anger: "#c2413d",
  fear: "#7e22ce",
  surprise: "#0e7490",
  disgust: "#4d7c0f",
};

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

type Mode = "ai" | "find";

export function SearchModule({ uploadId }: { uploadId: string }) {
  const [mode, setMode] = useState<Mode>("ai");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);

  // ---- Streaming state (mode === "ai") ---------------------------------
  const [streaming, setStreaming] = useState(false);
  const [meta, setMeta] = useState<{
    rephrased_query: string;
    evidence: StreamEvidence[];
    search_method: string;
  } | null>(null);
  const [answerBuffer, setAnswerBuffer] = useState("");
  const [done, setDone] = useState<{
    cited: CitedMessage[];
    confidence: number;
    search_method: string;
  } | null>(null);

  // ---- Find mode state -------------------------------------------------
  // We store a flat list of SearchResult rather than the NL response shape
  // because find-mode renders raw retrieval — no answer/citations.
  const [findResults, setFindResults] = useState<SearchResult[] | null>(null);
  const [findFilters, setFindFilters] = useState<SearchFilters>({});
  const [showFilters, setShowFilters] = useState(false);
  const [findSort, setFindSort] = useState<"relevance" | "date" | "emotion">("relevance");

  // ---- Shared state ----------------------------------------------------
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const placeholder = useCyclingPlaceholder();

  const history = useSearchHistory();

  // Cancel in-flight on unmount.
  useEffect(() => () => abortRef.current?.abort(), []);

  // ---- Submission ------------------------------------------------------
  const submit = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    abortRef.current?.abort();
    setError(null);
    setSubmitted(trimmed);
    history.push({ query: trimmed, mode, uploadId });

    if (mode === "ai") {
      runStreamingSearch(trimmed);
    } else {
      runFindSearch(trimmed);
    }
  };

  const runStreamingSearch = (q: string) => {
    setMeta(null);
    setAnswerBuffer("");
    setDone(null);
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    streamSearch(
      uploadId,
      q,
      (event: StreamEvent) => {
        switch (event.type) {
          case "meta":
            setMeta({
              rephrased_query: event.rephrased_query,
              evidence: event.evidence,
              search_method: event.search_method,
            });
            break;
          case "delta":
            setAnswerBuffer((prev) => prev + event.text);
            break;
          case "done":
            setAnswerBuffer(event.answer);
            setDone({
              cited: event.cited_messages,
              confidence: event.confidence,
              search_method: event.search_method,
            });
            setStreaming(false);
            break;
          case "error":
            setError(event.message);
            setStreaming(false);
            break;
        }
      },
      { signal: ctrl.signal, topK: 30 },
    ).catch(() => {
      // Already surfaced via the error event from streamSearch's wrapper,
      // but catch here so unhandled-rejection warnings don't fire.
    });
  };

  const runFindSearch = (q: string) => {
    setFindResults(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // The dedicated NL search endpoint also returns the underlying cited
    // messages; until the backend exposes a raw-retrieval endpoint we
    // surface those here as the find-mode list. The semantic.search call
    // skips Claude synthesis on the backend when filters tell it to (this
    // lands with the next backend pass) — for now we accept the small
    // double-spend on the first query and use the citation list.
    semanticSearch(uploadId, q, findFilters, 50, { signal: ctrl.signal })
      .then((r) => {
        const items: SearchResult[] = r.cited_messages.map((c) => ({
          message: {
            // We don't have access to the underlying DB UUID here — the NL
            // response only carries the UCJ-level msg_id. For the context
            // drawer we need the UUID, so we fall back to msg_id and let
            // the drawer resolve via the `evidenceById` map (which is empty
            // in find-mode, so the drawer will gracefully surface "not found"
            // until the raw-retrieval endpoint is wired up).
            id: c.message_id,
            upload_id: r.upload_id,
            msg_index: 0,
            msg_id: c.message_id,
            sender: c.sender,
            timestamp: c.timestamp,
            content: c.content,
            msg_type: "text",
            reply_to_id: null,
            word_count: 0,
            char_count: c.content.length,
            has_emoji: false,
            emojis: [],
            has_url: false,
            is_deleted: false,
            has_media: false,
          } as SearchResult["message"],
          similarity: c.similarity,
          context: null,
        }));
        setFindResults(items);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Search failed");
      });
  };

  return (
    <section className="space-y-6" aria-labelledby="search-module-heading">
      <header>
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Module 04
        </p>
        <h2
          id="search-module-heading"
          className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
        >
          Ask anything
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Type a question about your conversation. Claude reads the relevant
          messages and answers, citing specific moments.
        </p>
      </header>

      <SearchBar
        value={query}
        onChange={setQuery}
        onSubmit={() => submit(query)}
        mode={mode}
        onModeChange={setMode}
        placeholder={placeholder}
        streaming={streaming}
        onCancel={() => abortRef.current?.abort()}
      />

      {/* Suggestions + history collapse only when there's no submitted query */}
      {!submitted ? (
        <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
          <SuggestionsPanel
            onPick={(q) => {
              setQuery(q);
              submit(q);
            }}
          />
          <HistoryPanel
            history={history.history}
            uploadId={uploadId}
            onPick={(item) => {
              setMode(item.mode);
              setQuery(item.query);
              submit(item.query);
            }}
            onRemove={history.remove}
            onClear={history.clear}
          />
        </div>
      ) : null}

      {error ? (
        <ErrorPanel
          error={error}
          onRetry={submitted ? () => submit(submitted) : undefined}
        />
      ) : null}

      {/* Results */}
      {submitted && mode === "ai" ? (
        <AIAnswer
          uploadId={uploadId}
          query={submitted}
          meta={meta}
          answerText={answerBuffer}
          done={done}
          streaming={streaming}
        />
      ) : null}

      {submitted && mode === "find" ? (
        <FindResults
          results={findResults}
          filters={findFilters}
          onFiltersChange={setFindFilters}
          showFilters={showFilters}
          onToggleFilters={() => setShowFilters((v) => !v)}
          sort={findSort}
          onSortChange={setFindSort}
          uploadId={uploadId}
        />
      ) : null}

      {/* Subtle reset link — easy escape back to the suggestions surface */}
      {submitted ? (
        <div className="text-center">
          <button
            type="button"
            onClick={() => {
              abortRef.current?.abort();
              setSubmitted(null);
              setQuery("");
              setAnswerBuffer("");
              setMeta(null);
              setDone(null);
              setFindResults(null);
              setError(null);
            }}
            className="text-xs text-muted-foreground transition hover:text-foreground"
          >
            ← back to suggestions
          </button>
        </div>
      ) : null}
    </section>
  );
}

// ===========================================================================
// Search bar
// ===========================================================================

function SearchBar({
  value,
  onChange,
  onSubmit,
  mode,
  onModeChange,
  placeholder,
  streaming,
  onCancel,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  mode: Mode;
  onModeChange: (m: Mode) => void;
  placeholder: string;
  streaming: boolean;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  // Auto-focus on mount so the user can start typing immediately. The
  // dashboard renders multiple modules; we only steal focus once.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <div className="surface-card overflow-hidden p-1.5">
      <div className="flex flex-col gap-1.5 lg:flex-row lg:items-center">
        {/* Mode toggle */}
        <ModeToggle value={mode} onChange={onModeChange} />

        {/* Input wrapper */}
        <div
          className={cn(
            "relative flex-1 rounded-xl bg-card-elevated/50 transition",
            "focus-within:bg-card-elevated focus-within:shadow-glow",
          )}
        >
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            placeholder={placeholder}
            className={cn(
              "w-full bg-transparent py-3.5 pl-10 pr-4 text-sm",
              "placeholder:text-muted-foreground",
              "focus:outline-none",
            )}
          />
        </div>

        {/* Submit / cancel */}
        {streaming ? (
          <Button
            variant="outline"
            onClick={onCancel}
            className="lg:min-w-[120px]"
          >
            <X className="mr-1.5 h-4 w-4" />
            Cancel
          </Button>
        ) : (
          <Button
            onClick={onSubmit}
            disabled={value.trim().length === 0}
            className="bg-primary text-primary-foreground hover:bg-primary/90 lg:min-w-[120px]"
          >
            {mode === "ai" ? (
              <>
                <Sparkles className="mr-1.5 h-4 w-4" />
                Ask
              </>
            ) : (
              <>
                <SearchIcon className="mr-1.5 h-4 w-4" />
                Find
              </>
            )}
          </Button>
        )}
      </div>
    </div>
  );
}

function ModeToggle({
  value,
  onChange,
}: {
  value: Mode;
  onChange: (m: Mode) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Search mode"
      className="inline-flex shrink-0 items-center rounded-xl bg-card-elevated/50 p-1 text-xs"
    >
      <ModeButton
        active={value === "ai"}
        onClick={() => onChange("ai")}
        icon={<Sparkles className="h-3 w-3" />}
        label="AI Answer"
      />
      <ModeButton
        active={value === "find"}
        onClick={() => onChange("find")}
        icon={<SearchIcon className="h-3 w-3" />}
        label="Find Messages"
      />
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 transition",
        active
          ? "bg-primary text-primary-foreground shadow-glow"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// ===========================================================================
// Suggestions
// ===========================================================================

function SuggestionsPanel({ onPick }: { onPick: (q: string) => void }) {
  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-3">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Try asking
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Suggested questions
        </h3>
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        {SUGGESTIONS.map((cat) => (
          <CategoryGroup key={cat.category} cat={cat} onPick={onPick} />
        ))}
      </div>
    </article>
  );
}

function CategoryGroup({
  cat,
  onPick,
}: {
  cat: (typeof SUGGESTIONS)[number];
  onPick: (q: string) => void;
}) {
  const Icon = cat.icon;
  return (
    <div className="rounded-xl border border-border bg-card-elevated/40 p-3">
      <div className="mb-2 flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5" style={{ color: cat.color }} />
        <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          {cat.category}
        </span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {cat.items.map((item) => (
          <li key={item.query}>
            <button
              type="button"
              onClick={() => onPick(item.query)}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-lg",
                "border border-border/40 bg-card/40 px-3 py-2 text-left text-xs text-foreground",
                "transition hover:border-primary/40 hover:bg-card",
              )}
            >
              <span>{item.label}</span>
              <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ===========================================================================
// History
// ===========================================================================

function HistoryPanel({
  history,
  uploadId,
  onPick,
  onRemove,
  onClear,
}: {
  history: ReturnType<typeof useSearchHistory>["history"];
  uploadId: string;
  onPick: (item: ReturnType<typeof useSearchHistory>["history"][number]) => void;
  onRemove: (q: string, uploadId: string) => void;
  onClear: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // Show entries from the current upload first; cross-upload entries follow
  // so the user can quickly switch contexts.
  const sorted = useMemo(() => {
    const own = history.filter((h) => h.uploadId === uploadId);
    const others = history.filter((h) => h.uploadId !== uploadId);
    return [...own, ...others];
  }, [history, uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <History className="h-3.5 w-3.5 text-muted-foreground" />
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Recent searches
          </p>
        </div>
        {history.length > 0 ? (
          <button
            type="button"
            onClick={onClear}
            className="text-[10px] text-muted-foreground transition hover:text-destructive"
          >
            clear
          </button>
        ) : null}
      </header>

      {history.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Your last 10 searches will appear here.
        </p>
      ) : (
        <ul className="mt-3 space-y-1.5">
          {sorted.slice(0, expanded ? 10 : 4).map((item) => (
            <li key={`${item.query}|${item.uploadId}`} className="group flex items-center gap-2">
              <button
                type="button"
                onClick={() => onPick(item)}
                className={cn(
                  "min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-xs",
                  "text-foreground/90 transition hover:bg-card-elevated/60 hover:text-foreground",
                )}
                title={item.query}
              >
                <span className="text-muted-foreground">
                  {item.mode === "ai" ? "Asked" : "Found"} ·
                </span>{" "}
                {item.query}
                {item.uploadId !== uploadId ? (
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    (other chat)
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                onClick={() => onRemove(item.query, item.uploadId)}
                className="opacity-0 transition group-hover:opacity-100"
                aria-label="Remove from history"
              >
                <X className="h-3 w-3 text-muted-foreground hover:text-destructive" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {history.length > 4 ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground transition hover:text-foreground"
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? "show less" : `show ${Math.min(history.length, 10) - 4} more`}
        </button>
      ) : null}
    </article>
  );
}

// ===========================================================================
// AI answer
// ===========================================================================

function AIAnswer({
  uploadId,
  query,
  meta,
  answerText,
  done,
  streaming,
}: {
  uploadId: string;
  query: string;
  meta: {
    rephrased_query: string;
    evidence: StreamEvidence[];
    search_method: string;
  } | null;
  answerText: string;
  done: { cited: CitedMessage[]; confidence: number; search_method: string } | null;
  streaming: boolean;
}) {
  const evidence = meta?.evidence ?? [];
  // While streaming, the cited list isn't authoritative yet — render every
  // evidence card and let the citation pills jump-scroll to them.
  const cited = done?.cited;
  // Build a map from msg_id → evidence for citation pills + drawer launch.
  const evidenceById = useMemo(
    () => Object.fromEntries(evidence.map((e) => [e.msg_id, e])),
    [evidence],
  );
  const [drawer, setDrawer] = useState<string | null>(null);

  const confidence = done?.confidence ?? 0;
  const searchMethod = done?.search_method ?? meta?.search_method ?? "nl_qa";

  return (
    <div className="space-y-5 animate-fade-up">
      <article className="surface-card p-5">
        <header className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
              Question
            </p>
            <h3 className="mt-0.5 font-display text-lg font-semibold leading-snug">
              {query}
            </h3>
            {meta?.rephrased_query &&
            meta.rephrased_query.toLowerCase() !== query.toLowerCase() ? (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Searched for: <em>{meta.rephrased_query}</em>
              </p>
            ) : null}
          </div>
          <div className="flex flex-col items-end gap-1">
            {done ? (
              <ConfidenceBadge value={confidence} method={searchMethod} />
            ) : null}
            {meta ? (
              <span className="text-[10px] text-muted-foreground">
                {evidence.length} message{evidence.length === 1 ? "" : "s"}{" "}
                analyzed
              </span>
            ) : null}
          </div>
        </header>

        <AnswerProse
          text={answerText}
          streaming={streaming && !done}
          evidenceById={evidenceById}
          onCitationClick={(id) => setDrawer(id)}
        />
      </article>

      <EvidenceList
        evidence={evidence}
        cited={cited}
        loading={streaming && evidence.length === 0}
        onSeeContext={(msgId) => setDrawer(msgId)}
      />

      <ContextDrawer
        open={drawer !== null}
        onOpenChange={(o) => !o && setDrawer(null)}
        uploadId={uploadId}
        targetMsgId={drawer}
        evidenceById={evidenceById}
      />
    </div>
  );
}

function ConfidenceBadge({ value, method }: { value: number; method: string }) {
  const pct = Math.round(value * 100);
  const tone =
    pct >= 70
      ? "border-success/40 bg-success/10 text-success"
      : pct >= 40
      ? "border-primary/40 bg-primary/10 text-primary"
      : "border-warning/40 bg-warning/10 text-warning";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] uppercase tracking-wider",
        tone,
      )}
      title={`Search method: ${method}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {pct}% confidence
    </span>
  );
}

// ----- Answer prose with citation pills -----

function AnswerProse({
  text,
  streaming,
  evidenceById,
  onCitationClick,
}: {
  text: string;
  streaming: boolean;
  evidenceById: Record<string, StreamEvidence>;
  onCitationClick: (msgId: string) => void;
}) {
  // Render text with inline `[msg_X]` pills and minimal **bold** markdown.
  // We split on the citation regex; each split chunk is either prose or
  // a citation. Inside prose chunks we further parse **bold**.
  const tokens = useMemo(() => parseAnswer(text), [text]);

  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4 text-sm leading-relaxed text-foreground/95">
      <p className="whitespace-pre-wrap">
        {tokens.map((t, i) => {
          if (t.type === "cite") {
            const ev = evidenceById[t.id];
            return (
              <button
                key={i}
                type="button"
                onClick={() => onCitationClick(t.id)}
                className={cn(
                  "mx-0.5 inline-flex items-center gap-1 rounded-md border px-1.5 py-0 align-baseline text-[11px]",
                  "border-primary/40 bg-primary/10 text-primary transition hover:border-primary/70 hover:bg-primary/20",
                )}
                title={
                  ev ? `${ev.sender}: ${ev.content.slice(0, 80)}` : undefined
                }
              >
                {t.id}
              </button>
            );
          }
          if (t.type === "bold")
            return (
              <strong key={i} className="font-semibold text-foreground">
                {t.text}
              </strong>
            );
          return <span key={i}>{t.text}</span>;
        })}
        {streaming ? <TypingCursor /> : null}
      </p>
    </div>
  );
}

function TypingCursor() {
  return (
    <span
      className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-[2px] animate-pulse-soft bg-primary"
      aria-hidden
    />
  );
}

type Token =
  | { type: "text"; text: string }
  | { type: "bold"; text: string }
  | { type: "cite"; id: string };

function parseAnswer(text: string): Token[] {
  // First split on citations.
  const out: Token[] = [];
  const re = /\[(msg_[\w-]+)\]/g;
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) {
      pushProse(out, text.slice(lastIdx, m.index));
    }
    out.push({ type: "cite", id: m[1]! });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < text.length) pushProse(out, text.slice(lastIdx));
  return out;
}

function pushProse(out: Token[], chunk: string) {
  // Split on **bold** runs. Anything else passes through as plain text.
  const re = /\*\*([^*]+)\*\*/g;
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(chunk)) !== null) {
    if (m.index > lastIdx) {
      out.push({ type: "text", text: chunk.slice(lastIdx, m.index) });
    }
    out.push({ type: "bold", text: m[1]! });
    lastIdx = m.index + m[0].length;
  }
  if (lastIdx < chunk.length) {
    out.push({ type: "text", text: chunk.slice(lastIdx) });
  }
}

// ----- Evidence list -----

function EvidenceList({
  evidence,
  cited,
  loading,
  onSeeContext,
}: {
  evidence: StreamEvidence[];
  cited: CitedMessage[] | undefined;
  loading: boolean;
  onSeeContext: (msgId: string) => void;
}) {
  if (loading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="surface-card flex flex-col gap-2 p-4"
          >
            <span className="shimmer h-3 w-24 rounded" />
            <span className="shimmer h-3 w-full rounded" />
            <span className="shimmer h-3 w-2/3 rounded" />
          </div>
        ))}
      </div>
    );
  }
  if (evidence.length === 0) return null;

  // If we have a cited list, sort cited-first then everything else.
  const citedIds = new Set((cited ?? []).map((c) => c.message_id));
  const ordered = [
    ...evidence.filter((e) => citedIds.has(e.msg_id)),
    ...evidence.filter((e) => !citedIds.has(e.msg_id)),
  ];

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Evidence
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Supporting messages
          </h3>
        </div>
        {cited && cited.length > 0 ? (
          <span className="text-[11px] text-muted-foreground">
            {cited.length} cited · {evidence.length} relevant
          </span>
        ) : null}
      </header>
      <ul className="grid gap-3 sm:grid-cols-2">
        {ordered.map((e) => (
          <EvidenceCard
            key={e.msg_id}
            evidence={e}
            cited={citedIds.has(e.msg_id)}
            onSeeContext={() => onSeeContext(e.msg_id)}
          />
        ))}
      </ul>
    </article>
  );
}

function EvidenceCard({
  evidence: e,
  cited,
  onSeeContext,
}: {
  evidence: StreamEvidence;
  cited: boolean;
  onSeeContext: () => void;
}) {
  const initials = getInitials(e.sender);
  const hue = stringToHue(e.sender);
  return (
    <li
      className={cn(
        "rounded-xl border bg-card-elevated/40 p-4 transition",
        cited
          ? "border-primary/40 shadow-[0_0_24px_-12px_hsl(var(--primary)/0.6)]"
          : "border-border hover:border-primary/30",
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold"
            style={{
              background: `linear-gradient(135deg, hsl(${hue} 60% 45%), hsl(${(hue + 30) % 360} 70% 55%))`,
            }}
            aria-hidden
          >
            {initials}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">
              {e.sender}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {safeFormatDate(e.timestamp, "MMM d, yyyy h:mm a")}
            </p>
          </div>
        </div>
        <SimilarityBadge value={e.similarity} />
      </header>

      <p className="mt-2.5 line-clamp-4 whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
        {e.content}
      </p>

      <footer className="mt-3 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {e.emotion_label ? (
            <span
              className="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] capitalize"
              style={{
                borderColor: EMOTION_COLORS[e.emotion_label],
                color: EMOTION_COLORS[e.emotion_label],
                background: `${EMOTION_COLORS[e.emotion_label]}1a`,
              }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: EMOTION_COLORS[e.emotion_label] }}
              />
              {e.emotion_label}
            </span>
          ) : null}
          {cited ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-primary">
              cited
            </span>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onSeeContext} className="h-7 text-[11px]">
          See in context
          <ArrowRight className="ml-1 h-3 w-3" />
        </Button>
      </footer>
    </li>
  );
}

function SimilarityBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-card/60 px-1.5 py-0.5 text-[10px] tabular-nums"
      title="Cosine similarity to your query"
    >
      <span
        className="h-1.5 w-1.5 rounded-full bg-primary"
        style={{ opacity: 0.4 + value * 0.6 }}
      />
      {pct}%
    </span>
  );
}

// ===========================================================================
// Find-Messages mode
// ===========================================================================

function FindResults({
  results,
  filters,
  onFiltersChange,
  showFilters,
  onToggleFilters,
  sort,
  onSortChange,
  uploadId,
}: {
  results: SearchResult[] | null;
  filters: SearchFilters;
  onFiltersChange: (f: SearchFilters) => void;
  showFilters: boolean;
  onToggleFilters: () => void;
  sort: "relevance" | "date" | "emotion";
  onSortChange: (s: "relevance" | "date" | "emotion") => void;
  uploadId: string;
}) {
  const [drawer, setDrawer] = useState<string | null>(null);
  const sortedResults = useMemo(() => {
    if (!results) return null;
    const arr = [...results];
    if (sort === "date") {
      arr.sort(
        (a, b) =>
          parseISO(b.message.timestamp).getTime() -
          parseISO(a.message.timestamp).getTime(),
      );
    }
    // "relevance" is the natural order; "emotion" is a stub until the
    // backend exposes per-result emotion_score in raw retrieval.
    return arr;
  }, [results, sort]);

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr] animate-fade-up">
      {showFilters ? (
        <FilterSidebar
          filters={filters}
          onChange={onFiltersChange}
          onClose={onToggleFilters}
        />
      ) : null}

      <div className={cn("space-y-3", !showFilters && "lg:col-span-2")}>
        <div className="flex items-center justify-between gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onToggleFilters}
            className="h-8"
          >
            <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" />
            {showFilters ? "Hide filters" : "Filters"}
          </Button>
          <SortToggle value={sort} onChange={onSortChange} />
        </div>

        {!sortedResults ? (
          <FindSkeleton />
        ) : sortedResults.length === 0 ? (
          <EmptyMessage text="No matches yet. Try a broader query." />
        ) : (
          <ul className="space-y-3">
            {sortedResults.map((r) => (
              <li key={r.message.msg_id}>
                <FindResultCard
                  result={r}
                  onSeeContext={() => setDrawer(r.message.msg_id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <ContextDrawer
        open={drawer !== null}
        onOpenChange={(o) => !o && setDrawer(null)}
        uploadId={uploadId}
        targetMsgId={drawer}
        evidenceById={{}}
      />
    </div>
  );
}

function FilterSidebar({
  filters,
  onChange,
  onClose,
}: {
  filters: SearchFilters;
  onChange: (f: SearchFilters) => void;
  onClose: () => void;
}) {
  const set = <K extends keyof SearchFilters>(k: K, v: SearchFilters[K]) =>
    onChange({ ...filters, [k]: v });
  return (
    <aside className="surface-card sticky top-20 self-start p-4">
      <header className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Filter className="h-3.5 w-3.5 text-muted-foreground" />
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Filters
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Close filters"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </header>

      <div className="space-y-3 text-xs">
        <FilterField label="Sender">
          <input
            type="text"
            value={filters.sender ?? ""}
            onChange={(e) => set("sender", e.target.value || undefined)}
            placeholder="Any"
            className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-foreground placeholder:text-muted-foreground"
          />
        </FilterField>
        <FilterField label="From">
          <input
            type="date"
            value={filters.date_from ?? ""}
            onChange={(e) => set("date_from", e.target.value || undefined)}
            className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-foreground"
          />
        </FilterField>
        <FilterField label="To">
          <input
            type="date"
            value={filters.date_to ?? ""}
            onChange={(e) => set("date_to", e.target.value || undefined)}
            className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-foreground"
          />
        </FilterField>
        <FilterField label="Emotion">
          <select
            value={filters.emotion_label ?? ""}
            onChange={(e) =>
              set("emotion_label", (e.target.value || undefined) as EmotionClass | undefined)
            }
            className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-foreground"
          >
            <option value="">Any</option>
            {(["joy", "love", "sadness", "anger", "fear", "surprise", "disgust"] as EmotionClass[]).map(
              (e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ),
            )}
          </select>
        </FilterField>
        <FilterField label="Type">
          <select
            value={filters.msg_type ?? ""}
            onChange={(e) => set("msg_type", e.target.value || undefined)}
            className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-foreground"
          >
            <option value="">Any</option>
            <option value="text">Text only</option>
            <option value="image">Image</option>
            <option value="video">Video</option>
            <option value="audio">Audio</option>
          </select>
        </FilterField>
        <button
          type="button"
          onClick={() => onChange({})}
          className="mt-2 w-full rounded-md border border-border bg-card px-2 py-1.5 text-[11px] uppercase tracking-wider text-muted-foreground transition hover:text-destructive"
        >
          Reset filters
        </button>
      </div>
    </aside>
  );
}

function FilterField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

function SortToggle({
  value,
  onChange,
}: {
  value: "relevance" | "date" | "emotion";
  onChange: (s: "relevance" | "date" | "emotion") => void;
}) {
  const opts: { value: typeof value; label: string }[] = [
    { value: "relevance", label: "Relevance" },
    { value: "date", label: "Date" },
    { value: "emotion", label: "Emotion" },
  ];
  return (
    <div className="inline-flex items-center rounded-full border border-border bg-card-elevated/40 p-0.5 text-[11px]">
      {opts.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-full px-2.5 py-0.5 capitalize transition",
            value === o.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function FindResultCard({
  result,
  onSeeContext,
}: {
  result: SearchResult;
  onSeeContext: () => void;
}) {
  const m = result.message;
  return (
    <article className="surface-card p-4 transition hover:border-primary/30">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-semibold"
            style={{
              background: `linear-gradient(135deg, hsl(${stringToHue(m.sender)} 60% 45%), hsl(${(stringToHue(m.sender) + 30) % 360} 70% 55%))`,
            }}
            aria-hidden
          >
            {getInitials(m.sender)}
          </span>
          <div className="min-w-0">
            <p className="truncate font-display text-sm font-semibold">
              {m.sender}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {safeFormatDate(m.timestamp, "MMM d, yyyy h:mm a")}
            </p>
          </div>
        </div>
        <SimilarityBadge value={result.similarity} />
      </header>
      <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
        {m.content}
      </p>
      <footer className="mt-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {m.emotion_label ? (
            <span
              className="inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] capitalize"
              style={{
                borderColor: EMOTION_COLORS[m.emotion_label],
                color: EMOTION_COLORS[m.emotion_label],
                background: `${EMOTION_COLORS[m.emotion_label]}1a`,
              }}
            >
              {m.emotion_label}
            </span>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onSeeContext} className="h-7 text-[11px]">
          See in context <ArrowRight className="ml-1 h-3 w-3" />
        </Button>
      </footer>
    </article>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function useCyclingPlaceholder(): string {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setIdx((i) => (i + 1) % PLACEHOLDERS.length), 3500);
    return () => clearInterval(id);
  }, []);
  return PLACEHOLDERS[idx]!;
}

function ErrorPanel({
  error,
  onRetry,
}: {
  error: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <X className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">Search failed</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

function FindSkeleton() {
  return (
    <ul className="space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <li key={i} className="surface-card p-4">
          <span className="shimmer mb-2 block h-3 w-32 rounded" />
          <span className="shimmer block h-3 w-full rounded" />
          <span className="shimmer mt-1 block h-3 w-2/3 rounded" />
        </li>
      ))}
    </ul>
  );
}

function EmptyMessage({ text }: { text: string }) {
  return (
    <div className="flex h-32 items-center justify-center rounded-lg border border-dashed border-border/60 bg-card-elevated/20 px-4 text-center">
      <p className="text-xs text-muted-foreground">{text}</p>
    </div>
  );
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0]!;
  if (parts.length === 1) return first.slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1]!;
  return (first[0]! + last[0]!).toUpperCase();
}

function stringToHue(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}
