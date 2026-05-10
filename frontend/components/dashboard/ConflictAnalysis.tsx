"use client";

/**
 * ConflictAnalysis — Module 05 of the dashboard.
 *
 * Sensitive content. The product framing here is "difficult moments",
 * not "fights" — every label and copy choice on this page is meant to
 * help users understand patterns, not assign blame. We show:
 *   1. A sensitivity banner anchored at the top.
 *   2. Section 1 — Overview metrics (count, duration, recovery time)
 *      and a "who tends to escalate / resolve" pair, both shown with
 *      the X-of-N denominator so the reader sees the pattern in
 *      context, not as a stat-card accusation. Frequency-by-month
 *      bar chart on the right.
 *   3. Section 2 — Pattern clusters: Claude-named themes. Lazy-loaded
 *      via getConflictThemes so the page renders without waiting on
 *      the LLM call.
 *   4. Section 3 — Per-conflict timeline. Each card collapsed by
 *      default, expand to read the trigger (blurred until clicked),
 *      sentiment-arc mini chart, and resolution badge. "Read
 *      conversation" opens the shared ContextDrawer at the trigger.
 *   5. Section 4 — Language patterns: word-cloud-style pills (red/orange
 *      tones for conflict, blue/green for resolution) plus when-do-they-
 *      start charts (hour-of-day + day-of-week).
 *
 * Each section fetches independently and renders skeletons / errors
 * inline so a slow LLM call doesn't block the rest of the module.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clock,
  Eye,
  EyeOff,
  Heart,
  Layers,
  RefreshCcw,
  Shield,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { format, parseISO } from "date-fns";

import { Button } from "@/components/ui/button";
import { ContextDrawer } from "@/components/dashboard/ContextDrawer";
import { getConflictAnalysis, getConflictThemes } from "@/lib/api";
import type {
  ConflictAnalysisResponse,
  ConflictTheme,
  ConflictWindow,
  ResolutionType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants — local palettes for the language word clouds
// ---------------------------------------------------------------------------

// Warm reds + ambers for conflict words — visually heavy without being alarmist.
const CONFLICT_TONES = ["#ef4444", "#f97316", "#f59e0b"];
// Cool blues + soft greens for resolution words.
const RESOLUTION_TONES = ["#06b6d4", "#10b981", "#22c55e"];

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ConflictAnalysis({ uploadId }: { uploadId: string }) {
  return (
    <section className="space-y-6" aria-labelledby="conflict-analysis-heading">
      <header>
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Module 05
        </p>
        <h2
          id="conflict-analysis-heading"
          className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
        >
          Understanding Difficult Moments
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Stretches of harder communication, what they were about, how
          they ended.
        </p>
      </header>

      <SensitivityBanner />

      <ConflictBody uploadId={uploadId} />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sensitivity banner — always visible at the top of the module
// ---------------------------------------------------------------------------

function SensitivityBanner() {
  return (
    <aside
      role="note"
      className={cn(
        "rounded-2xl border bg-card-elevated/40 p-4",
        "border-warning/30",
        "shadow-[inset_3px_0_0_hsl(var(--warning))]",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning/15 text-warning"
          aria-hidden
        >
          <Shield className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0">
          <p className="font-display text-sm font-semibold text-foreground">
            A note before you read on
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            This section identifies communication patterns to help you
            understand, not to assign blame. Every relationship has
            difficult moments — what matters is how you read them
            together.
          </p>
        </div>
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Body — owns the data fetches + the open drawer state
// ---------------------------------------------------------------------------

function ConflictBody({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<ConflictAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [themes, setThemes] = useState<ConflictTheme[] | null>(null);
  const [themesLoading, setThemesLoading] = useState(false);
  const [themesError, setThemesError] = useState<string | null>(null);

  // The drawer takes the trigger message's DB UUID. We track which
  // window was opened so the drawer can hydrate context cleanly.
  const [drawerWindowId, setDrawerWindowId] = useState<string | null>(null);

  const fetchedRef = useRef<string | null>(null);

  useEffect(() => {
    if (fetchedRef.current === uploadId) return;
    fetchedRef.current = uploadId;
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    getConflictAnalysis(uploadId, { signal: ctrl.signal })
      .then((r) => {
        if (cancelled) return;
        setData(r);
        // If themes were already cached server-side, fold them into local state.
        if (r.themes) setThemes(r.themes);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load conflicts");
        setLoading(false);
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  // Lazy-load themes after the main payload arrives. Themes call costs
  // a Claude round-trip, so we don't block the page on it. If the main
  // response already had themes (from a prior themes fetch), skip.
  useEffect(() => {
    if (!data || data.windows.length === 0) return;
    if (themes !== null) return; // already populated
    let cancelled = false;
    const ctrl = new AbortController();
    setThemesLoading(true);
    setThemesError(null);

    getConflictThemes(uploadId, { signal: ctrl.signal })
      .then((r) => {
        if (cancelled) return;
        setThemes(r.themes);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setThemesError(
          e instanceof Error ? e.message : "Failed to cluster themes",
        );
      })
      .finally(() => {
        if (!cancelled) setThemesLoading(false);
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
    // We deliberately re-key on `data` (specifically: arriving for the
    // first time) so a refresh after manual invalidation re-fires the
    // theme fetch. eslint-disable-next-line react-hooks/exhaustive-deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, uploadId]);

  if (loading) return <BodySkeleton />;
  if (error) return <ErrorPanel error={error} />;
  if (!data || data.windows.length === 0) {
    return <EmptyState />;
  }

  const drawerWindow = data.windows.find((w) => w.window_id === drawerWindowId) ?? null;

  return (
    <div className="space-y-6">
      <OverviewSection data={data} />
      <ThemesSection
        themes={themes}
        loading={themesLoading}
        error={themesError}
        onRefresh={() => {
          setThemes(null);
          fetchedRef.current = null; // re-fire the lazy effect
        }}
      />
      <TimelineSection
        windows={data.windows}
        themes={themes}
        onOpenDrawer={(window_id) => setDrawerWindowId(window_id)}
      />
      <LanguagePatternsSection language={data.language} />

      <ContextDrawer
        open={drawerWindow !== null}
        onOpenChange={(o) => !o && setDrawerWindowId(null)}
        uploadId={uploadId}
        targetDbId={drawerWindow?.start_db_id ?? null}
        windowSize={12}
      />
    </div>
  );
}

// ===========================================================================
// Section 1 — Overview
// ===========================================================================

function OverviewSection({ data }: { data: ConflictAnalysisResponse }) {
  const s = data.summary;
  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 01 · Pattern overview
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          A read on the harder stretches
        </h3>
      </header>

      <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
        <div className="grid grid-cols-3 gap-3">
          <Stat
            label="Difficult moments"
            value={s.total_conflicts_detected.toLocaleString()}
            sub={s.most_common_triggers.length > 0 ? `top trigger: "${s.most_common_triggers[0]}"` : undefined}
          />
          <Stat
            label="Avg duration"
            value={formatHours(s.avg_duration_hours)}
            sub="from first dip to recovery"
          />
          <Stat
            label="Avg recovery"
            value={
              s.avg_recovery_time_hours != null
                ? formatHours(s.avg_recovery_time_hours)
                : "—"
            }
            sub={
              s.avg_recovery_time_hours != null
                ? "after the moment ends"
                : "no resolved moments yet"
            }
          />
          <RoleCard
            label="Tends to send the first hard message"
            role={s.who_escalates_more}
            tone="warning"
          />
          <RoleCard
            label="Tends to send the first soft message"
            role={s.who_resolves_more}
            tone="success"
          />
        </div>

        <FrequencyChart data={s.conflict_frequency_by_month} />
      </div>
    </article>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
      <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-display text-2xl font-semibold tabular-nums">
        {value}
      </p>
      {sub ? <p className="mt-1 text-[11px] text-muted-foreground">{sub}</p> : null}
    </div>
  );
}

function RoleCard({
  label,
  role,
  tone,
}: {
  label: string;
  role: { sender: string; count: number; total: number } | null;
  tone: "warning" | "success";
}) {
  const color =
    tone === "warning" ? "hsl(var(--warning))" : "hsl(var(--success))";
  return (
    <div
      className="col-span-3 rounded-xl border border-border bg-card-elevated/40 p-4"
      style={{ boxShadow: `inset 2px 0 0 ${color}` }}
    >
      <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </p>
      {role ? (
        <>
          <p className="mt-1 flex items-baseline gap-2">
            <span className="font-display text-lg font-semibold" style={{ color }}>
              {role.sender}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {role.count} of {role.total}
            </span>
          </p>
          <p className="mt-1 text-[11px] italic text-muted-foreground">
            This is a pattern observation, not blame — both people contribute
            to a conversation&apos;s shape.
          </p>
        </>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">
          Not enough resolved moments to read a pattern yet.
        </p>
      )}
    </div>
  );
}

function FrequencyChart({
  data,
}: {
  data: { month: string; count: number }[];
}) {
  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-xl border border-dashed border-border/60 bg-card-elevated/20">
        <p className="text-xs text-muted-foreground">
          Frequency chart needs at least one detected moment.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        Frequency over time
      </p>
      <div className="h-48 w-full">
        <ResponsiveContainer>
          <BarChart
            data={data}
            margin={{ top: 4, right: 8, bottom: 0, left: -16 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="hsl(var(--border))"
              strokeOpacity={0.4}
              vertical={false}
            />
            <XAxis
              dataKey="month"
              stroke="hsl(var(--muted-foreground))"
              tick={{ fontSize: 10 }}
              tickFormatter={(m) => format(parseISO(`${m}-01`), "MMM yy")}
              tickLine={false}
              axisLine={false}
              minTickGap={20}
            />
            <YAxis
              stroke="hsl(var(--muted-foreground))"
              tick={{ fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={28}
              allowDecimals={false}
            />
            <RechartsTooltip
              contentStyle={{
                background: "hsl(var(--card-elevated))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 12,
                fontSize: 11,
              }}
              labelFormatter={(m) =>
                format(parseISO(`${m as string}-01`), "MMMM yyyy")
              }
            />
            <Bar
              dataKey="count"
              fill="hsl(var(--warning))"
              fillOpacity={0.72}
              radius={[4, 4, 0, 0]}
              isAnimationActive
              animationDuration={700}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ===========================================================================
// Section 2 — Theme clusters
// ===========================================================================

function ThemesSection({
  themes,
  loading,
  error,
  onRefresh,
}: {
  themes: ConflictTheme[] | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4 flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Section 02 · Recurring themes
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            What these moments tend to be about
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Claude groups the trigger messages into recurring themes.
          </p>
        </div>
        {themes !== null ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            className="h-7 text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCcw className="mr-1.5 h-3 w-3" />
            Re-cluster
          </Button>
        ) : null}
      </header>

      {error ? (
        <ErrorPanel error={error} compact />
      ) : loading || themes === null ? (
        <ThemesSkeleton />
      ) : themes.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No themes surfaced yet — typically that means there were too few
          difficult moments to cluster reliably.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {themes.map((t) => (
            <ThemeCard key={t.label} theme={t} />
          ))}
        </div>
      )}
    </article>
  );
}

function ThemeCard({ theme }: { theme: ConflictTheme }) {
  const intensity = theme.avg_sentiment != null ? Math.abs(theme.avg_sentiment) : 0;
  return (
    <div
      className="group rounded-xl border border-border bg-card-elevated/40 p-4 transition hover:border-primary/30"
      style={{
        boxShadow: `inset 2px 0 0 ${tintForIntensity(intensity)}`,
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <h4 className="font-display text-sm font-semibold capitalize">
          {theme.label}
        </h4>
        <span className="rounded-full border border-border bg-card px-2 py-0.5 text-[10px] tabular-nums text-muted-foreground">
          ×{theme.frequency}
        </span>
      </div>
      {theme.description ? (
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
          {theme.description}
        </p>
      ) : null}
      {theme.example_messages.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {theme.example_messages.slice(0, 2).map((m) => (
            <li
              key={m.msg_id}
              className="rounded-md border border-border/60 bg-card/40 p-2 text-[11px]"
            >
              <p className="line-clamp-2 italic text-foreground/90">
                “{m.content_preview}”
              </p>
              <p className="mt-1 text-muted-foreground">
                {m.sender} ·{" "}
                {format(parseISO(m.timestamp), "MMM d, yyyy")}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Section 3 — Per-conflict timeline
// ===========================================================================

function TimelineSection({
  windows,
  themes,
  onOpenDrawer,
}: {
  windows: ConflictWindow[];
  themes: ConflictTheme[] | null;
  onOpenDrawer: (windowId: string) => void;
}) {
  const themeByWindow = useMemo(() => {
    const m: Record<string, string> = {};
    if (!themes) return m;
    for (const t of themes) {
      for (const wid of t.window_ids) {
        m[wid] = t.label;
      }
    }
    return m;
  }, [themes]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 03 · Timeline
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Each moment, in order
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Tap a row to expand it. Trigger messages are blurred until you
          choose to see them.
        </p>
      </header>

      <ol className="space-y-2">
        {windows.map((w) => (
          <li key={w.window_id}>
            <ConflictRow
              window={w}
              themeLabel={themeByWindow[w.window_id]}
              onOpenDrawer={() => onOpenDrawer(w.window_id)}
            />
          </li>
        ))}
      </ol>
    </article>
  );
}

function ConflictRow({
  window: w,
  themeLabel,
  onOpenDrawer,
}: {
  window: ConflictWindow;
  themeLabel?: string;
  onOpenDrawer: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const intensity = Math.abs(w.peak_negativity_score);

  return (
    <article
      className={cn(
        "rounded-xl border bg-card-elevated/40 transition",
        expanded ? "border-primary/30" : "border-border hover:border-border/80",
      )}
      style={{ boxShadow: `inset 2px 0 0 ${tintForIntensity(intensity)}` }}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
        aria-expanded={expanded}
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="font-display text-sm font-semibold">
              {format(parseISO(w.start_timestamp), "MMM d, yyyy")}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {formatMinutes(w.duration_minutes)} · {w.duration_messages} messages
            </span>
            {themeLabel ? (
              <span className="rounded-full border border-border bg-card px-1.5 py-0 text-[10px] capitalize text-muted-foreground">
                {themeLabel}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {w.trigger_sender ? <span>started by {w.trigger_sender}</span> : null}
            <ResolutionBadge type={w.resolution.type} />
          </div>
        </div>

        <SentimentArc arc={w.sentiment_arc} />

        <span className="ml-2 shrink-0 text-muted-foreground">
          {expanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </span>
      </button>

      {expanded ? (
        <div className="space-y-3 border-t border-border/60 px-4 py-3">
          {/* Trigger — blurred until revealed */}
          <div>
            <div className="mb-1 flex items-center justify-between">
              <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Trigger message
              </p>
              <button
                type="button"
                onClick={() => setRevealed((v) => !v)}
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground transition hover:text-foreground"
              >
                {revealed ? (
                  <>
                    <EyeOff className="h-3 w-3" /> hide
                  </>
                ) : (
                  <>
                    <Eye className="h-3 w-3" /> reveal
                  </>
                )}
              </button>
            </div>
            <div
              className={cn(
                "rounded-lg border border-border/60 bg-card/40 p-3 text-sm leading-relaxed",
                "transition-[filter] duration-300",
                !revealed && "select-none cursor-pointer blur-sm hover:blur-[3px]",
              )}
              onClick={() => !revealed && setRevealed(true)}
              role={!revealed ? "button" : undefined}
              tabIndex={!revealed ? 0 : -1}
              onKeyDown={(e) => {
                if (!revealed && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  setRevealed(true);
                }
              }}
              aria-label={
                !revealed ? "Reveal trigger message" : "Trigger message"
              }
            >
              {w.trigger_message ? (
                <>
                  <p className="italic text-foreground/90">
                    “{w.trigger_message.content_preview}”
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {w.trigger_message.sender} ·{" "}
                    {format(parseISO(w.trigger_message.timestamp), "h:mm a")}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground">No trigger detected.</p>
              )}
            </div>
          </div>

          {/* Sentiment arc — bigger this time */}
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Sentiment arc
            </p>
            <SentimentArc arc={w.sentiment_arc} large />
            <p className="mt-1 text-[10px] text-muted-foreground">
              Lowest point: {(w.peak_negativity_score * 100).toFixed(0)}%
            </p>
          </div>

          {/* Top words */}
          {w.top_words.length > 0 ? (
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                What was being said
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {w.top_words.map((word) => (
                  <li
                    key={word}
                    className="rounded-full border border-border bg-card-elevated/60 px-2 py-0.5 text-[11px] text-foreground/90"
                  >
                    {word}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Footer — read conversation */}
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onOpenDrawer}
              className="h-7 text-[11px]"
            >
              Read conversation
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function SentimentArc({
  arc,
  large,
}: {
  arc: (number | null)[];
  large?: boolean;
}) {
  if (!arc || arc.length === 0) return null;
  const width = large ? 480 : 140;
  const height = large ? 60 : 28;
  const minVal = -1;
  const maxVal = 1;

  // Build polyline points; missing values become path breaks via NaN +
  // moveTo segments.
  const stepX = arc.length > 1 ? width / (arc.length - 1) : width;
  const points: { x: number; y: number; missing: boolean }[] = arc.map(
    (v, i) => {
      const x = i * stepX;
      const norm = v == null ? 0.5 : (v - minVal) / (maxVal - minVal);
      const y = height - norm * height;
      return { x, y, missing: v == null };
    },
  );

  // Construct an SVG path with M / L commands, breaking the line at gaps.
  let d = "";
  let pendingMove = true;
  for (const p of points) {
    if (p.missing) {
      pendingMove = true;
      continue;
    }
    d += `${pendingMove ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)} `;
    pendingMove = false;
  }

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className="shrink-0"
      aria-hidden
    >
      {/* Zero line */}
      <line
        x1={0}
        x2={width}
        y1={height / 2}
        y2={height / 2}
        stroke="hsl(var(--border))"
        strokeDasharray="2 3"
      />
      <path d={d} fill="none" stroke="hsl(var(--warning))" strokeWidth={1.5} />
      {/* Mark dips below zero */}
      {points
        .filter((p) => !p.missing && p.y > height / 2)
        .map((p, i) => (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r={1.4}
            fill="hsl(var(--destructive))"
          />
        ))}
    </svg>
  );
}

function ResolutionBadge({ type }: { type: ResolutionType }) {
  const map: Record<ResolutionType, { label: string; color: string }> = {
    apology: { label: "Apology", color: "hsl(var(--success))" },
    topic_change: { label: "Topic shifted", color: "hsl(var(--accent))" },
    time_gap: { label: "Time gap", color: "hsl(var(--muted-foreground))" },
    unresolved: { label: "Unresolved", color: "hsl(var(--warning))" },
  };
  const { label, color } = map[type];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-1.5 py-0 text-[10px] uppercase tracking-wider"
      style={{ borderColor: color, color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

// ===========================================================================
// Section 4 — Language patterns
// ===========================================================================

function LanguagePatternsSection({
  language,
}: {
  language: ConflictAnalysisResponse["language"];
}) {
  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 04 · Language &amp; timing
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          The shape of how it sounds
        </h3>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <WordCloudPanel
          title="Words during difficult moments"
          subtitle="How conflict shows up in vocabulary"
          icon={TrendingDown}
          tones={CONFLICT_TONES}
          words={language.conflict_words}
        />
        <WordCloudPanel
          title="Words in the recovery"
          subtitle="What people say afterwards"
          icon={Heart}
          tones={RESOLUTION_TONES}
          words={language.resolution_words}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <HourPanel buckets={language.hour_distribution} />
        <DowPanel buckets={language.dow_distribution} />
      </div>
    </article>
  );
}

function WordCloudPanel({
  title,
  subtitle,
  icon: Icon,
  tones,
  words,
}: {
  title: string;
  subtitle: string;
  icon: typeof TrendingDown;
  tones: string[];
  words: { word: string; count: number }[];
}) {
  if (!words || words.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
        <div className="flex items-center gap-2">
          <Icon className="h-3.5 w-3.5 text-muted-foreground" />
          <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
            {title}
          </p>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Not enough text yet to surface vocabulary.
        </p>
      </div>
    );
  }
  const max = words[0]!.count;
  const min = words[words.length - 1]!.count;
  const range = Math.max(1, Math.log(max + 1) - Math.log(min + 1));
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5" style={{ color: tones[0] }} />
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          {title}
        </p>
      </div>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{subtitle}</p>
      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1.5 leading-tight">
        {words.slice(0, 40).map((w, i) => {
          const t = (Math.log(w.count + 1) - Math.log(min + 1)) / range;
          const size = 0.8 + Math.pow(t, 0.7) * 1.4;
          const tone = tones[i % tones.length]!;
          const tilt = i % 9 === 0 ? -3 : i % 13 === 0 ? 2 : 0;
          return (
            <span
              key={w.word}
              className="inline-block font-display font-semibold"
              style={{
                fontSize: `${size.toFixed(2)}rem`,
                color: tone,
                transform: `rotate(${tilt}deg)`,
                opacity: 0.55 + 0.45 * t,
                textShadow: `0 0 14px ${withAlpha(tone, 0.16)}`,
              }}
              title={`${w.word} · ${w.count} uses`}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function HourPanel({
  buckets,
}: {
  buckets: { hour: number; count: number }[];
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="flex items-center gap-2">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          When difficult moments tend to start
        </p>
      </div>
      <div className="mt-3 grid grid-cols-24 gap-[2px]" style={{ display: "grid", gridTemplateColumns: "repeat(24, minmax(0,1fr))" }}>
        {buckets.map((b) => {
          const ratio = b.count / max;
          return (
            <div
              key={b.hour}
              className="flex flex-col items-center gap-1"
              title={`${labelHour(b.hour)} · ${b.count}`}
            >
              <div
                className="w-full rounded-sm"
                style={{
                  height: `${Math.max(2, ratio * 56)}px`,
                  background: `hsl(var(--warning) / ${0.25 + 0.65 * ratio})`,
                }}
              />
              <span className="text-[9px] text-muted-foreground">
                {b.hour % 6 === 0 ? labelHour(b.hour) : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DowPanel({
  buckets,
}: {
  buckets: { dow: number; count: number }[];
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="flex items-center gap-2">
        <Layers className="h-3.5 w-3.5 text-muted-foreground" />
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Day-of-week distribution
        </p>
      </div>
      <div className="mt-3 grid grid-cols-7 gap-1.5">
        {buckets.map((b) => {
          const ratio = b.count / max;
          return (
            <div key={b.dow} className="flex flex-col items-center gap-1">
              <div
                className="w-full rounded-md"
                style={{
                  height: `${Math.max(4, ratio * 56)}px`,
                  background: `hsl(var(--warning) / ${0.25 + 0.65 * ratio})`,
                }}
                title={`${DAY_NAMES[b.dow]} · ${b.count}`}
              />
              <span className="text-[10px] text-muted-foreground">
                {DAY_NAMES[b.dow]}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ===========================================================================
// Skeletons + utility
// ===========================================================================

function BodySkeleton() {
  return (
    <div className="space-y-6">
      <div className="surface-card p-5">
        <span className="shimmer mb-3 block h-3 w-40 rounded" />
        <div className="grid gap-4 lg:grid-cols-[2fr_3fr]">
          <div className="grid grid-cols-3 gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <span key={i} className="shimmer h-20 rounded-xl" />
            ))}
            <span className="shimmer col-span-3 h-20 rounded-xl" />
            <span className="shimmer col-span-3 h-20 rounded-xl" />
          </div>
          <span className="shimmer h-48 rounded-xl" />
        </div>
      </div>
      <ThemesSkeleton />
      <span className="shimmer block h-32 rounded-xl" />
    </div>
  );
}

function ThemesSkeleton() {
  return (
    <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl border border-border bg-card-elevated/40 p-4"
        >
          <span className="shimmer mb-2 block h-3 w-32 rounded" />
          <span className="shimmer block h-3 w-full rounded" />
          <span className="shimmer mt-1 block h-3 w-2/3 rounded" />
        </div>
      ))}
    </div>
  );
}

function ErrorPanel({
  error,
  compact,
}: {
  error: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5",
        compact ? "p-3" : "p-4",
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          Couldn&apos;t load this section
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/60 bg-card-elevated/20 px-6 py-10 text-center">
      <Sparkles className="h-5 w-5 text-success" />
      <h3 className="font-display text-base font-semibold">No difficult moments detected</h3>
      <p className="max-w-sm text-xs text-muted-foreground">
        We didn&apos;t find any sustained negative stretches in this conversation
        — based on the criteria we use, the tone stayed mostly steady. That
        doesn&apos;t mean nothing was hard, just that no moments matched the
        pattern we look for.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatHours(h: number): string {
  if (!Number.isFinite(h) || h <= 0) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 24) return `${h.toFixed(1)}h`;
  const days = h / 24;
  return `${days.toFixed(1)}d`;
}

function formatMinutes(min: number): string {
  if (min < 60) return `${min}m`;
  const h = min / 60;
  if (h < 24) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

function labelHour(h: number): string {
  if (h === 0) return "12a";
  if (h === 12) return "12p";
  if (h < 12) return `${h}a`;
  return `${h - 12}p`;
}

/** Map |sentiment| in [0, 1] to a tint string used for inset borders. */
function tintForIntensity(intensity: number): string {
  // Higher intensity → warmer; lower → softer. Capped at 0.85 alpha so the
  // border doesn't overpower the card.
  const a = Math.min(0.85, 0.25 + 0.7 * intensity);
  return `hsl(var(--warning) / ${a})`;
}

function withAlpha(color: string, alpha: number): string {
  // Same trick as in WordAnalytics — only used for soft text shadows.
  if (color.startsWith("#") && color.length === 7) {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}
