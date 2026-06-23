"use client";

/**
 * EmotionTimeline — Module 02 of the dashboard.
 *
 * Sections (top → bottom):
 *   1. Sentiment line chart with granularity toggle (Recharts).
 *      - Two lines (one per participant), monotone curve.
 *      - Reference line at y=0.
 *      - Soft green fill above neutral, soft red below — drawn as two
 *        Area shapes layered behind the lines using `gradientStops`.
 *      - Annotated peaks/valleys: dots with labels at the top-3 highs and
 *        top-3 lows (whichever exist after `getEmotionalPeaks`).
 *   2. Emotion distribution — one donut per participant, hover reveals
 *      sample messages from that emotion class.
 *   3. Mood calendar — GitHub-style heatmap. Hue maps positive→amber-pink
 *      / negative→indigo, intensity from |avg_sentiment|.
 *   4. Peaks panel — top-5 happiest periods + top-5 hardest periods, each
 *      with 2-3 message previews.
 *
 * Each section fetches independently so a failure in one doesn't block
 * the others. Skeletons mirror the eventual layout.
 */

import { useEffect, useMemo, useState } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertCircle,
  Calendar,
  ChevronDown,
  ChevronUp,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { parseISO } from "date-fns";

import {
  getEmotionalPeaks,
  getEmotionByParticipant,
  getMoodCalendar,
  getSentimentTimeline,
} from "@/lib/api";
import type {
  EmotionByParticipant,
  EmotionClass,
  EmotionalPeak,
  EmotionalPeaks,
  Granularity,
  MoodCalendar,
  MoodCalendarDay,
  ParticipantEmotionDistribution,
  SampleMessage,
  SentimentTimeline,
} from "@/lib/types";
import { cn, safeFormatDate } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Color system — emotion palette per spec
// ---------------------------------------------------------------------------

const EMOTION_COLORS: Record<EmotionClass, string> = {
  joy: "#f59e0b",
  love: "#ec4899",
  sadness: "#6366f1",
  anger: "#ef4444",
  fear: "#8b5cf6",
  surprise: "#06b6d4",
  disgust: "#84cc16",
};

// Sentiment color stops for the calendar heatmap.
//   Positive: indigo → warm pink.   Negative: cool slate → indigo.
const POSITIVE_COLOR = "#f59e0b"; // warm
const NEGATIVE_COLOR = "#6366f1"; // cool indigo
const EMPTY_COLOR = "hsl(240 14% 14%)"; // matches --muted in dark theme

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function EmotionTimeline({ uploadId }: { uploadId: string }) {
  return (
    <Tooltip.Provider delayDuration={150}>
      <section className="space-y-8" aria-labelledby="emotion-timeline-heading">
        <header>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Module 02
          </p>
          <h2
            id="emotion-timeline-heading"
            className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            Emotion Timeline
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            How the emotional shape of the conversation moved over time —
            who&apos;s felt what, when sentiment shifted, and the moments that
            stand out.
          </p>
        </header>

        <SentimentChartSection uploadId={uploadId} />
        <DistributionSection uploadId={uploadId} />
        <MoodCalendarSection uploadId={uploadId} />
        <PeaksSection uploadId={uploadId} />
      </section>
    </Tooltip.Provider>
  );
}

// ===========================================================================
// Section 1 — Sentiment line chart
// ===========================================================================

function SentimentChartSection({ uploadId }: { uploadId: string }) {
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [data, setData] = useState<SentimentTimeline | null>(null);
  const [peaks, setPeaks] = useState<EmotionalPeaks | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    Promise.all([
      getSentimentTimeline(uploadId, granularity, { signal: ctrl.signal }),
      // Peaks are bucketed by week and don't depend on granularity, so we
      // fetch them once per upload-id load and cache via state.
      peaks
        ? Promise.resolve(peaks)
        : getEmotionalPeaks(uploadId, { signal: ctrl.signal }),
    ])
      .then(([timeline, peaksData]) => {
        if (cancelled) return;
        setData(timeline);
        setPeaks(peaksData);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load timeline");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadId, granularity]);

  const senders = data?.senders ?? [];
  const senderColor = useMemo(
    () =>
      Object.fromEntries(senders.map((s) => [s, hsl(stringToHue(s), 70, 60)])),
    [senders],
  );

  // Flatten timeline points into Recharts-friendly rows: each row has the
  // overall avg + one column per sender. Recharts expects flat objects;
  // we map per_sender slices into top-level keys keyed by sender name.
  const rows: ChartRow[] = useMemo(() => {
    if (!data) return [];
    return data.points.map((p) => {
      const row: ChartRow = {
        date: p.date,
        avg: p.avg_sentiment,
        message_count: p.message_count,
        dominant_emotion: p.dominant_emotion,
      };
      for (const slice of p.per_sender) {
        row[slice.sender] = slice.avg_sentiment;
      }
      return row;
    });
  }, [data]);

  // Top 3 peaks + top 3 valleys to annotate inline. Filter to dates that
  // intersect the current timeline so the dots actually land on points.
  const annotated = useMemo(() => {
    if (!peaks || rows.length === 0) return { peaks: [], valleys: [] };
    const dateSet = new Set(rows.map((r) => r.date as string));
    const findRow = (start: string) => {
      // For week/month granularity the timeline date is the bucket start;
      // peaks always come back as bucket_start (week). We round-trip both
      // values to ISO so a quick lookup works.
      if (dateSet.has(start)) return start;
      // For day granularity, peaks (which are weekly) won't hit; we fall
      // back to the closest day in the visible range.
      const startDate = parseISO(start).getTime();
      let closest: string | null = null;
      let bestDelta = Infinity;
      for (const r of rows) {
        const d = Math.abs(parseISO(r.date as string).getTime() - startDate);
        if (d < bestDelta) {
          bestDelta = d;
          closest = r.date as string;
        }
      }
      return closest;
    };
    return {
      peaks: peaks.peaks
        .slice(0, 3)
        .map((p) => ({ peak: p, x: findRow(p.bucket_start) })),
      valleys: peaks.valleys
        .slice(0, 3)
        .map((p) => ({ peak: p, x: findRow(p.bucket_start) })),
    };
  }, [peaks, rows]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Section 01 · Timeline
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Sentiment over time
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Per-participant sentiment, smoothed. Above the zero line is
            positive.
          </p>
        </div>
        <GranularityToggle value={granularity} onChange={setGranularity} />
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <ChartSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState message="No sentiment data yet for this conversation." />
      ) : (
        <SentimentChart
          rows={rows}
          senders={senders}
          senderColor={senderColor}
          granularity={granularity}
          peaks={annotated.peaks}
          valleys={annotated.valleys}
        />
      )}
    </article>
  );
}

function GranularityToggle({
  value,
  onChange,
}: {
  value: Granularity;
  onChange: (g: Granularity) => void;
}) {
  const options: Granularity[] = ["day", "week", "month"];
  return (
    <div
      className="inline-flex items-center rounded-full border border-border bg-card-elevated/40 p-0.5 text-xs"
      role="radiogroup"
      aria-label="Time granularity"
    >
      {options.map((g) => (
        <button
          key={g}
          type="button"
          role="radio"
          aria-checked={value === g}
          onClick={() => onChange(g)}
          className={cn(
            "rounded-full px-3 py-1 capitalize transition",
            value === g
              ? "bg-primary text-primary-foreground shadow-glow"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {g}
        </button>
      ))}
    </div>
  );
}

interface ChartRow {
  date: string;
  avg: number | null;
  message_count: number;
  dominant_emotion: EmotionClass | null;
  [sender: string]: number | string | null;
}

function SentimentChart({
  rows,
  senders,
  senderColor,
  granularity,
  peaks,
  valleys,
}: {
  rows: ChartRow[];
  senders: string[];
  senderColor: Record<string, string>;
  granularity: Granularity;
  peaks: { peak: EmotionalPeak; x: string | null }[];
  valleys: { peak: EmotionalPeak; x: string | null }[];
}) {
  // Two stacked Areas masked above / below 0 to render the soft red /
  // green wash. We split the overall `avg` series into "positive only"
  // and "negative only" so each Area's gradient only paints on its side.
  const splitRows = useMemo(
    () =>
      rows.map((r) => {
        const v = r.avg;
        return {
          ...r,
          avg_pos: v != null && v >= 0 ? v : 0,
          avg_neg: v != null && v < 0 ? v : 0,
        };
      }),
    [rows],
  );

  // Sample tick selection — too many ticks fight for label space on long
  // chats. Keep ~6 ticks regardless of row count.
  const xTicks = useMemo(
    () =>
      sampleTicks(
        splitRows.map((r) => r.date),
        6,
      ),
    [splitRows],
  );

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer>
        <ComposedChart
          data={splitRows}
          margin={{ top: 16, right: 12, bottom: 8, left: -8 }}
        >
          <defs>
            <linearGradient id="grad-pos" x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor="hsl(var(--success))"
                stopOpacity={0.35}
              />
              <stop
                offset="100%"
                stopColor="hsl(var(--success))"
                stopOpacity={0}
              />
            </linearGradient>
            <linearGradient id="grad-neg" x1="0" y1="1" x2="0" y2="0">
              <stop
                offset="0%"
                stopColor="hsl(var(--destructive))"
                stopOpacity={0.35}
              />
              <stop
                offset="100%"
                stopColor="hsl(var(--destructive))"
                stopOpacity={0}
              />
            </linearGradient>
          </defs>

          <CartesianGrid
            strokeDasharray="3 3"
            stroke="hsl(var(--border))"
            strokeOpacity={0.4}
            vertical={false}
          />

          <XAxis
            dataKey="date"
            ticks={xTicks}
            stroke="hsl(var(--muted-foreground))"
            tick={{ fontSize: 11 }}
            tickFormatter={(d) => formatTick(d, granularity)}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            domain={[-1, 1]}
            ticks={[-1, -0.5, 0, 0.5, 1]}
            stroke="hsl(var(--muted-foreground))"
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => v.toFixed(1)}
            tickLine={false}
            axisLine={false}
            width={36}
          />

          <ReferenceLine
            y={0}
            stroke="hsl(var(--muted-foreground))"
            strokeDasharray="4 4"
            strokeOpacity={0.6}
          />

          {/* Background washes above / below 0 */}
          <Area
            type="monotone"
            dataKey="avg_pos"
            stroke="none"
            fill="url(#grad-pos)"
            isAnimationActive={false}
            connectNulls
          />
          <Area
            type="monotone"
            dataKey="avg_neg"
            stroke="none"
            fill="url(#grad-neg)"
            isAnimationActive={false}
            connectNulls
          />

          {/* Per-sender lines */}
          {senders.map((sender) => (
            <Line
              key={sender}
              type="monotone"
              dataKey={sender}
              stroke={senderColor[sender]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0, fill: senderColor[sender] }}
              isAnimationActive={true}
              animationDuration={800}
              connectNulls
            />
          ))}

          {/* Annotated peaks (top-3 happy) */}
          {peaks.map(({ peak, x }) =>
            x ? (
              <ReferenceDot
                key={`p-${peak.bucket_start}`}
                x={x}
                y={Math.min(1, peak.score)}
                r={5}
                fill={EMOTION_COLORS.joy}
                stroke="hsl(var(--background))"
                strokeWidth={2}
                ifOverflow="extendDomain"
                shape={(props: any) => (
                  <PeakMarker {...props} kind="peak" peak={peak} />
                )}
              />
            ) : null,
          )}
          {/* Annotated valleys (top-3 hard) */}
          {valleys.map(({ peak, x }) =>
            x ? (
              <ReferenceDot
                key={`v-${peak.bucket_start}`}
                x={x}
                y={Math.max(-1, peak.score)}
                r={5}
                fill={EMOTION_COLORS.sadness}
                stroke="hsl(var(--background))"
                strokeWidth={2}
                ifOverflow="extendDomain"
                shape={(props: any) => (
                  <PeakMarker {...props} kind="valley" peak={peak} />
                )}
              />
            ) : null,
          )}

          <RechartsTooltip
            content={(props: any) => (
              <ChartTooltip
                {...props}
                senders={senders}
                senderColor={senderColor}
              />
            )}
            cursor={{
              stroke: "hsl(var(--primary))",
              strokeOpacity: 0.4,
              strokeWidth: 1,
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend senders={senders} senderColor={senderColor} />
    </div>
  );
}

function PeakMarker({
  cx,
  cy,
  kind,
  peak,
}: {
  cx?: number;
  cy?: number;
  kind: "peak" | "valley";
  peak: EmotionalPeak;
}) {
  if (cx == null || cy == null) return null;
  const color = kind === "peak" ? EMOTION_COLORS.joy : EMOTION_COLORS.sadness;
  const labelY = kind === "peak" ? cy - 14 : cy + 18;
  const dateLabel = safeFormatDate(peak.bucket_start, "MMM d");
  return (
    <g pointerEvents="none">
      <circle
        cx={cx}
        cy={cy}
        r={6}
        fill={color}
        stroke="hsl(var(--background))"
        strokeWidth={2}
        style={{ filter: `drop-shadow(0 0 6px ${color})` }}
      />
      <text
        x={cx}
        y={labelY}
        textAnchor="middle"
        fontSize="10"
        fontFamily="var(--font-display), var(--font-sans)"
        fill="hsl(var(--foreground))"
      >
        {dateLabel}
      </text>
    </g>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
  senders,
  senderColor,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartRow }>;
  label?: string;
  senders: string[];
  senderColor: Record<string, string>;
}) {
  if (!active || !payload || payload.length === 0 || !label) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="rounded-xl border border-border bg-card-elevated/95 px-3 py-2 text-xs shadow-soft backdrop-blur">
      <div className="font-display text-sm font-semibold">
        {safeFormatDate(label, "MMM d, yyyy")}
      </div>
      <div className="mt-2 space-y-1">
        {senders.map((s) => {
          const v = row[s] as number | null | undefined;
          if (v == null) return null;
          return (
            <div key={s} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: senderColor[s] }}
                />
                <span className="text-foreground">{s}</span>
              </span>
              <span className="tabular-nums text-muted-foreground">
                {(v * 100).toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
      {row.dominant_emotion ? (
        <div className="mt-2 flex items-center gap-1.5 border-t border-border pt-1.5 text-[11px]">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: EMOTION_COLORS[row.dominant_emotion] }}
          />
          <span className="text-muted-foreground">Dominant:</span>
          <span className="capitalize text-foreground">
            {row.dominant_emotion}
          </span>
        </div>
      ) : null}
      <div className="mt-1 text-[11px] text-muted-foreground">
        {row.message_count.toLocaleString()} messages
      </div>
    </div>
  );
}

function ChartLegend({
  senders,
  senderColor,
}: {
  senders: string[];
  senderColor: Record<string, string>;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      {senders.map((s) => (
        <span key={s} className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: senderColor[s] }}
          />
          <span className="text-foreground">{s}</span>
        </span>
      ))}
      <span className="ml-auto inline-flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full bg-warning" />
        peak
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full bg-primary" />
        difficult
      </span>
    </div>
  );
}

// ===========================================================================
// Section 2 — Emotion distribution (per-participant donuts)
// ===========================================================================

function DistributionSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<EmotionByParticipant | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // No fetchedRef guard — Strict Mode's doubled mount is handled by the
    // AbortController + cancelled flag below. The guard would block the
    // re-fetch after the first abort, freezing the panel on its skeleton.
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getEmotionByParticipant(uploadId, { signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(
          e instanceof Error ? e.message : "Failed to load distribution",
        );
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 02 · Emotion mix
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Emotion distribution
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          What each person tended to feel. Hover a slice for examples.
        </p>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <DonutGridSkeleton />
      ) : data.participants.length === 0 ? (
        <EmptyState message="No emotion-classified messages yet." />
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          {data.participants.map((p) => (
            <DonutCard key={p.sender} participant={p} />
          ))}
        </div>
      )}
    </article>
  );
}

function DonutCard({
  participant,
}: {
  participant: ParticipantEmotionDistribution;
}) {
  const [activeEmotion, setActiveEmotion] = useState<EmotionClass | null>(null);

  // Pre-compute ring geometry. We render the donut ourselves (rather than
  // Recharts PieChart) because we want fine control over hover state, gap
  // between slices, and the centre overlay.
  const r = 56;
  const stroke = 18;
  const c = 2 * Math.PI * r;

  const slices = useMemo(() => {
    let acc = 0;
    return participant.distribution.map((s) => {
      const len = c * s.share;
      const offset = -acc;
      acc += len;
      return { ...s, len, offset };
    });
  }, [participant.distribution, c]);

  const active = useMemo(
    () =>
      activeEmotion
        ? (participant.distribution.find((s) => s.emotion === activeEmotion) ??
          null)
        : null,
    [activeEmotion, participant.distribution],
  );
  const top = useMemo(
    () =>
      [...participant.distribution].sort((a, b) => b.share - a.share)[0] ??
      null,
    [participant.distribution],
  );
  const focus = active ?? top;

  return (
    <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
      <div className="flex items-center justify-between">
        <h4 className="font-display text-sm font-semibold">
          {participant.sender}
        </h4>
        <span className="text-[11px] text-muted-foreground">
          {participant.total_classified.toLocaleString()} classified
        </span>
      </div>

      <div className="mt-3 grid grid-cols-[auto_1fr] gap-4">
        <div className="relative h-36 w-36 shrink-0">
          <svg viewBox="-70 -70 140 140" className="h-full w-full -rotate-90">
            <circle
              r={r}
              cx="0"
              cy="0"
              fill="none"
              stroke="hsl(var(--muted))"
              strokeWidth={stroke}
            />
            {slices.map((s) => (
              <circle
                key={s.emotion}
                r={r}
                cx="0"
                cy="0"
                fill="none"
                stroke={EMOTION_COLORS[s.emotion]}
                strokeWidth={activeEmotion === s.emotion ? stroke + 3 : stroke}
                strokeDasharray={`${s.len} ${c - s.len}`}
                strokeDashoffset={s.offset}
                onMouseEnter={() => setActiveEmotion(s.emotion)}
                onMouseLeave={() => setActiveEmotion(null)}
                style={{
                  cursor: s.share > 0 ? "pointer" : "default",
                  filter:
                    activeEmotion === s.emotion
                      ? `drop-shadow(0 0 6px ${EMOTION_COLORS[s.emotion]})`
                      : undefined,
                  transition: "stroke-width 200ms ease",
                }}
              />
            ))}
          </svg>
          {focus ? (
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
              <span
                className="font-display text-xl font-semibold"
                style={{ color: EMOTION_COLORS[focus.emotion] }}
              >
                {Math.round(focus.share * 100)}%
              </span>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {focus.emotion}
              </span>
            </div>
          ) : null}
        </div>

        <div className="flex flex-col">
          <ul className="grid grid-cols-2 gap-x-2 gap-y-1.5 text-xs">
            {participant.distribution.map((s) => {
              const dim = activeEmotion && activeEmotion !== s.emotion;
              return (
                <li
                  key={s.emotion}
                  className={cn(
                    "flex items-center gap-1.5 transition",
                    dim ? "opacity-40" : "opacity-100",
                  )}
                  onMouseEnter={() => setActiveEmotion(s.emotion)}
                  onMouseLeave={() => setActiveEmotion(null)}
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ background: EMOTION_COLORS[s.emotion] }}
                  />
                  <span className="capitalize text-foreground">
                    {s.emotion}
                  </span>
                  <span className="ml-auto tabular-nums text-muted-foreground">
                    {(s.share * 100).toFixed(0)}%
                  </span>
                </li>
              );
            })}
          </ul>

          <DonutSamples active={active} />
        </div>
      </div>
    </div>
  );
}

function DonutSamples({
  active,
}: {
  active: ParticipantEmotionDistribution["distribution"][number] | null;
}) {
  if (!active || active.sample_messages.length === 0) {
    return (
      <p className="mt-3 text-[11px] text-muted-foreground">
        Hover an emotion to see example messages.
      </p>
    );
  }
  return (
    <ul className="mt-3 space-y-1.5">
      {active.sample_messages.slice(0, 2).map((m) => (
        <li
          key={m.msg_id}
          className="rounded-md border border-border/60 bg-card/50 p-2 text-[11px]"
        >
          <p className="line-clamp-2 italic text-foreground/90">
            “{m.content_preview}”
          </p>
          <p className="mt-1 text-muted-foreground">
            {safeFormatDate(m.timestamp, "MMM d, yyyy")}
          </p>
        </li>
      ))}
    </ul>
  );
}

// ===========================================================================
// Section 3 — Mood calendar
// ===========================================================================

export function MoodCalendarSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<MoodCalendar | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getMoodCalendar(uploadId, { signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load calendar");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4 flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Section 03 · Calendar
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Mood calendar
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            One square per day. Warm = positive, cool = negative.
          </p>
        </div>
        <CalendarLegend />
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <CalendarSkeleton />
      ) : data.days.length === 0 ? (
        <EmptyState message="No daily sentiment data yet." />
      ) : (
        <CalendarHeatmap data={data} />
      )}
    </article>
  );
}

function CalendarLegend() {
  return (
    <div className="hidden items-center gap-2 text-[11px] text-muted-foreground sm:flex">
      <span>−1</span>
      <div
        className="h-2 w-32 rounded-full"
        style={{
          background: `linear-gradient(90deg, ${NEGATIVE_COLOR}, ${EMPTY_COLOR}, ${POSITIVE_COLOR})`,
        }}
      />
      <span>+1</span>
    </div>
  );
}

function CalendarHeatmap({ data }: { data: MoodCalendar }) {
  // Layout: 7 rows (Mon..Sun), columns = ISO weeks. Each entry maps to
  // (col=week_index, row=day_of_week). Month labels render along the
  // top by detecting the first column of each month.
  const grid = useMemo(() => buildCalendarGrid(data.days), [data.days]);

  return (
    <div className="overflow-x-auto">
      <div className="inline-block min-w-full">
        {/* Month labels */}
        <div className="ml-8 mb-1.5 flex gap-[3px] text-[10px] text-muted-foreground">
          {grid.weeks.map((w, i) => (
            <div key={i} className="w-3 text-center">
              {w.firstOfMonth ? safeFormatDate(w.firstDate, "MMM") : ""}
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          {/* Day-of-week labels */}
          <div className="flex flex-col gap-[3px] pt-0.5 text-[10px] text-muted-foreground">
            {["Mon", "", "Wed", "", "Fri", "", "Sun"].map((d, i) => (
              <div key={i} className="h-3 leading-3">
                {d}
              </div>
            ))}
          </div>
          {/* Squares */}
          <div className="flex gap-[3px]">
            {grid.weeks.map((week, wi) => (
              <div key={wi} className="flex flex-col gap-[3px]">
                {week.cells.map((cell, di) => (
                  <CalendarSquare
                    key={di}
                    day={cell}
                    happiest={data.happiest_day}
                    hardest={data.hardest_day}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Highlight chips */}
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {data.happiest_day ? (
            <CalendarHighlight day={data.happiest_day} kind="happiest" />
          ) : null}
          {data.hardest_day ? (
            <CalendarHighlight day={data.hardest_day} kind="hardest" />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function CalendarSquare({
  day,
  happiest,
  hardest,
}: {
  day: MoodCalendarDay | null;
  happiest: MoodCalendarDay | null;
  hardest: MoodCalendarDay | null;
}) {
  if (day === null) {
    // Empty cell (before the first message or after the last) — keep grid shape.
    return <span className="h-3 w-3" aria-hidden />;
  }
  const isHi = happiest && day.date === happiest.date;
  const isLo = hardest && day.date === hardest.date;
  const color = sentimentToColor(day.avg_sentiment, day.message_count);
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          type="button"
          className={cn(
            "h-3 w-3 rounded-[3px] outline-none transition",
            "hover:ring-2 hover:ring-primary/60",
            (isHi || isLo) && "ring-2 ring-foreground/80 hover:ring-foreground",
          )}
          style={{ background: color }}
          aria-label={`${day.date}: ${day.message_count} messages`}
        />
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={6}
          className="z-50 rounded-lg border border-border bg-card-elevated/95 px-3 py-2 text-xs shadow-soft backdrop-blur"
        >
          <div className="font-display text-sm font-semibold">
            {safeFormatDate(day.date, "EEE, MMM d, yyyy")}
          </div>
          {day.message_count > 0 ? (
            <>
              {day.avg_sentiment != null ? (
                <div className="mt-0.5 text-muted-foreground">
                  Avg sentiment{" "}
                  <span
                    className="font-medium"
                    style={{
                      color:
                        day.avg_sentiment >= 0
                          ? POSITIVE_COLOR
                          : NEGATIVE_COLOR,
                    }}
                  >
                    {(day.avg_sentiment * 100).toFixed(0)}%
                  </span>
                </div>
              ) : null}
              {day.dominant_emotion ? (
                <div className="text-muted-foreground">
                  Dominant{" "}
                  <span
                    className="capitalize font-medium"
                    style={{ color: EMOTION_COLORS[day.dominant_emotion] }}
                  >
                    {day.dominant_emotion}
                  </span>
                </div>
              ) : null}
              <div className="text-muted-foreground">
                {day.message_count} messages
              </div>
            </>
          ) : (
            <div className="text-muted-foreground">No messages</div>
          )}
          {isHi ? (
            <div className="mt-1 text-success">★ Happiest day</div>
          ) : null}
          {isLo ? (
            <div className="mt-1 text-destructive">▼ Hardest day</div>
          ) : null}
          <Tooltip.Arrow className="fill-card-elevated" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function CalendarHighlight({
  day,
  kind,
}: {
  day: MoodCalendarDay;
  kind: "happiest" | "hardest";
}) {
  const Icon = kind === "happiest" ? TrendingUp : TrendingDown;
  const color = kind === "happiest" ? POSITIVE_COLOR : NEGATIVE_COLOR;
  return (
    <div
      className="rounded-lg border border-border bg-card-elevated/40 p-3"
      style={{ boxShadow: `inset 2px 0 0 ${color}` }}
    >
      <div className="flex items-center gap-2">
        <Icon className="h-3.5 w-3.5" style={{ color }} />
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {kind === "happiest" ? "Happiest day" : "Hardest day"}
        </p>
      </div>
      <p className="mt-1 font-display text-sm font-semibold">
        {safeFormatDate(day.date, "EEE, MMM d, yyyy")}
      </p>
      <p className="text-xs text-muted-foreground">
        {day.message_count} messages
        {day.avg_sentiment != null ? (
          <>
            {" · "}
            <span style={{ color }}>
              {(day.avg_sentiment * 100).toFixed(0)}%
            </span>
          </>
        ) : null}
      </p>
    </div>
  );
}

// ===========================================================================
// Section 4 — Peaks panel
// ===========================================================================

function PeaksSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<EmotionalPeaks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getEmotionalPeaks(uploadId, { signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load peaks");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 04 · Moments
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Emotional peaks
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          The weeks that registered as the warmest and the hardest.
        </p>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <PeaksSkeleton />
      ) : data.peaks.length === 0 && data.valleys.length === 0 ? (
        <EmptyState message="Not enough sentiment data to surface peaks yet." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <PeaksColumn
            title="Happiest"
            subtitle="Top 5 highs"
            entries={data.peaks}
            kind="peak"
          />
          <PeaksColumn
            title="Most difficult"
            subtitle="Top 5 lows"
            entries={data.valleys}
            kind="valley"
          />
        </div>
      )}
    </article>
  );
}

function PeaksColumn({
  title,
  subtitle,
  entries,
  kind,
}: {
  title: string;
  subtitle: string;
  entries: EmotionalPeak[];
  kind: "peak" | "valley";
}) {
  const Icon = kind === "peak" ? TrendingUp : TrendingDown;
  const color = kind === "peak" ? POSITIVE_COLOR : NEGATIVE_COLOR;
  return (
    <div>
      <header className="mb-2 flex items-center gap-2">
        <Icon className="h-3.5 w-3.5" style={{ color }} />
        <h4 className="font-display text-sm font-semibold">{title}</h4>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {subtitle}
        </span>
      </header>
      {entries.length === 0 ? (
        <EmptyState message="No qualifying weeks yet." compact />
      ) : (
        <ul className="space-y-2">
          {entries.map((p) => (
            <PeakCard key={p.bucket_start} peak={p} kind={kind} />
          ))}
        </ul>
      )}
    </div>
  );
}

function PeakCard({
  peak,
  kind,
}: {
  peak: EmotionalPeak;
  kind: "peak" | "valley";
}) {
  const [open, setOpen] = useState(false);
  const color = kind === "peak" ? POSITIVE_COLOR : NEGATIVE_COLOR;
  return (
    <li
      className="rounded-lg border border-border bg-card-elevated/40 p-3 transition hover:border-primary/30"
      style={{ boxShadow: `inset 2px 0 0 ${color}` }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-display text-sm font-semibold">
            {safeFormatDate(peak.bucket_start, "MMM d")} –{" "}
            {safeFormatDate(peak.bucket_end, "MMM d, yyyy")}
          </p>
          <p className="text-xs text-muted-foreground">
            <span style={{ color }} className="tabular-nums">
              {kind === "peak" ? "+" : ""}
              {(peak.score * 100).toFixed(0)}%
            </span>
            {" · "}
            {peak.message_count} messages
            {peak.dominant_emotion ? (
              <>
                {" · "}
                <span
                  className="capitalize"
                  style={{ color: EMOTION_COLORS[peak.dominant_emotion] }}
                >
                  {peak.dominant_emotion}
                </span>
              </>
            ) : null}
          </p>
        </div>
        {peak.top_messages.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-secondary hover:text-foreground"
            aria-label={open ? "Collapse" : "Expand examples"}
          >
            {open ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>
        ) : null}
      </div>
      {open && peak.top_messages.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {peak.top_messages.slice(0, 3).map((m) => (
            <SampleLine key={m.msg_id} message={m} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function SampleLine({ message }: { message: SampleMessage }) {
  return (
    <li className="rounded-md border border-border/60 bg-card/50 p-2 text-[11px]">
      <p className="line-clamp-3 italic text-foreground/90">
        “{message.content_preview}”
      </p>
      <p className="mt-1 text-muted-foreground">
        {message.sender} · {safeFormatDate(message.timestamp, "MMM d, yyyy")}
        {message.emotion_label ? (
          <>
            {" · "}
            <span
              className="capitalize"
              style={{ color: EMOTION_COLORS[message.emotion_label] }}
            >
              {message.emotion_label}
            </span>
          </>
        ) : null}
      </p>
    </li>
  );
}

// ===========================================================================
// Skeleton + utility components
// ===========================================================================

function ChartSkeleton() {
  return (
    <div className="space-y-3">
      <div className="h-72 w-full rounded-lg border border-border/60 bg-card-elevated/20">
        <div className="shimmer h-full w-full rounded-lg" />
      </div>
    </div>
  );
}

function DonutGridSkeleton() {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      {Array.from({ length: 2 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl border border-border bg-card-elevated/40 p-4"
        >
          <span className="shimmer mb-3 block h-3 w-24 rounded" />
          <div className="grid grid-cols-[auto_1fr] gap-4">
            <span className="shimmer h-36 w-36 rounded-full" />
            <div className="space-y-1.5">
              {Array.from({ length: 7 }).map((_, j) => (
                <span key={j} className="shimmer block h-3 w-full rounded" />
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function CalendarSkeleton() {
  return (
    <div className="space-y-2">
      <div className="ml-8 flex gap-[3px]">
        {Array.from({ length: 26 }).map((_, i) => (
          <span key={i} className="h-2 w-3 rounded bg-muted/40" />
        ))}
      </div>
      <div className="flex gap-[3px]">
        {Array.from({ length: 26 }).map((_, i) => (
          <div key={i} className="flex flex-col gap-[3px]">
            {Array.from({ length: 7 }).map((__, j) => (
              <span key={j} className="h-3 w-3 rounded-[3px] bg-muted/40" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function PeaksSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {Array.from({ length: 2 }).map((_, col) => (
        <div key={col} className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="rounded-lg border border-border bg-card-elevated/40 p-3"
            >
              <span className="shimmer mb-2 block h-3 w-40 rounded" />
              <span className="shimmer block h-3 w-24 rounded" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function SectionError({ error }: { error: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <AlertCircle className="h-4 w-4 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          Couldn&apos;t load this section
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
      </div>
    </div>
  );
}

function EmptyState({
  message,
  compact,
}: {
  message: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-card-elevated/20 px-4 text-center",
        compact ? "h-20" : "h-40",
      )}
    >
      <Calendar className="h-4 w-4 text-muted-foreground" />
      <p className="text-xs text-muted-foreground">{message}</p>
    </div>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function stringToHue(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}

function hsl(h: number, s: number, l: number): string {
  return `hsl(${h} ${s}% ${l}%)`;
}

function sampleTicks(values: string[], target: number): string[] {
  if (values.length <= target) return values;
  const step = Math.max(1, Math.round(values.length / target));
  const out: string[] = [];
  for (let i = 0; i < values.length; i += step) {
    const v = values[i];
    if (v != null) out.push(v);
  }
  // Always include the last so the right edge has a label.
  const last = values[values.length - 1];
  if (last != null && out[out.length - 1] !== last) out.push(last);
  return out;
}

function formatTick(d: string, granularity: Granularity): string {
  if (granularity === "month") return safeFormatDate(d, "MMM yyyy", "");
  if (granularity === "week") return safeFormatDate(d, "MMM d", "");
  return safeFormatDate(d, "MMM d", "");
}

function sentimentToColor(v: number | null, messageCount: number): string {
  if (v == null || messageCount === 0) return EMPTY_COLOR;
  // Magnitude → opacity, sign → hue.
  const mag = Math.min(1, Math.abs(v));
  // Build a stop between EMPTY_COLOR and POSITIVE/NEGATIVE based on mag.
  // Cheap approach: alpha-blend by writing as rgba via a known mix.
  const target = v >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR;
  const alpha = 0.18 + 0.82 * mag; // [0.18, 1.0]
  // Blend target with empty by alpha — express via gradient-via-svg-friendly
  // hex+alpha. We just append the alpha to a fixed RGB for compactness.
  return mixHex(EMPTY_COLOR, target, alpha);
}

/**
 * Mix two CSS color strings (hex or hsl()) at ratio `t` (0..1).
 * Cheap implementation: parse each into r/g/b in [0..255]. Browsers tolerate
 * "rgb(...)" output anywhere a color is expected, so we don't need to
 * round-trip back to hex.
 */
function mixHex(a: string, b: string, t: number): string {
  const ra = colorToRGB(a);
  const rb = colorToRGB(b);
  if (!ra || !rb) return b;
  const r = Math.round(ra.r + (rb.r - ra.r) * t);
  const g = Math.round(ra.g + (rb.g - ra.g) * t);
  const bl = Math.round(ra.b + (rb.b - ra.b) * t);
  return `rgb(${r}, ${g}, ${bl})`;
}

function colorToRGB(c: string): { r: number; g: number; b: number } | null {
  // hex #RRGGBB
  if (c.startsWith("#") && c.length === 7) {
    return {
      r: parseInt(c.slice(1, 3), 16),
      g: parseInt(c.slice(3, 5), 16),
      b: parseInt(c.slice(5, 7), 16),
    };
  }
  // hsl(h s% l%) — convert to RGB. We only call this in two places so the
  // rough conversion is fine.
  const m = c.match(/hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\)/);
  if (m) {
    return hslToRgb(
      parseFloat(m[1]!),
      parseFloat(m[2]!) / 100,
      parseFloat(m[3]!) / 100,
    );
  }
  return null;
}

function hslToRgb(
  h: number,
  s: number,
  l: number,
): { r: number; g: number; b: number } {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let rp = 0,
    gp = 0,
    bp = 0;
  if (h < 60) [rp, gp, bp] = [c, x, 0];
  else if (h < 120) [rp, gp, bp] = [x, c, 0];
  else if (h < 180) [rp, gp, bp] = [0, c, x];
  else if (h < 240) [rp, gp, bp] = [0, x, c];
  else if (h < 300) [rp, gp, bp] = [x, 0, c];
  else [rp, gp, bp] = [c, 0, x];
  return {
    r: Math.round((rp + m) * 255),
    g: Math.round((gp + m) * 255),
    b: Math.round((bp + m) * 255),
  };
}

// Calendar grid construction: groups days into ISO weeks (Mon..Sun cols).
function buildCalendarGrid(days: MoodCalendarDay[]): {
  weeks: {
    firstDate: string;
    firstOfMonth: boolean;
    cells: (MoodCalendarDay | null)[];
  }[];
} {
  if (days.length === 0) return { weeks: [] };
  const byDate = new Map(days.map((d) => [d.date, d]));
  const firstDay = days[0]!.date;
  const lastDay = days[days.length - 1]!.date;

  // Pad start to the previous Monday + end to the following Sunday so the
  // grid is a clean rectangle.
  const start = startOfWeek(firstDay);
  const end = endOfWeek(lastDay);

  const weeks: {
    firstDate: string;
    firstOfMonth: boolean;
    cells: (MoodCalendarDay | null)[];
  }[] = [];
  let cursor = start;
  let currentMonth = -1;
  // Defense in depth: even with the UTC fix below, never let a future date
  // edge case hard-freeze the page. 5000 weeks ≈ 96 years — far beyond any
  // real conversation — so a healthy grid is never truncated.
  let guard = 0;
  while (cursor <= end && guard++ < 5000) {
    const cells: (MoodCalendarDay | null)[] = [];
    const weekStart = cursor;
    for (let i = 0; i < 7; i++) {
      const iso = cursor;
      const day = byDate.get(iso) ?? null;
      // Outside the [firstDay, lastDay] window we render nothing.
      if (iso < firstDay || iso > lastDay) {
        cells.push(null);
      } else {
        cells.push(
          day ?? {
            date: iso,
            avg_sentiment: null,
            dominant_emotion: null,
            message_count: 0,
          },
        );
      }
      cursor = addDays(cursor, 1);
    }
    const month = parseUTCDate(weekStart).getUTCMonth();
    weeks.push({
      firstDate: weekStart,
      firstOfMonth: month !== currentMonth,
      cells,
    });
    currentMonth = month;
  }
  return { weeks };
}

// Parse a YYYY-MM-DD string as UTC midnight. date-fns parseISO treats a
// date-only string as LOCAL midnight; combined with the getUTC*/toISOString
// math below that makes addDays() a no-op in positive-offset zones (e.g. IST,
// UTC+5:30): getUTCDate() reads the *previous* UTC day, setUTCDate(+1) returns
// to the same calendar day, so the string never advances and
// buildCalendarGrid's `while (cursor <= end)` loop spins forever — freezing
// the whole page. Parsing as UTC keeps every step timezone-stable.
function parseUTCDate(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}
function startOfWeek(iso: string): string {
  const d = parseUTCDate(iso);
  // ISO week starts Monday. JS getUTCDay() returns 0=Sunday … 6=Saturday.
  // Convert: Mon=0, Tue=1, …, Sun=6.
  const dow = (d.getUTCDay() + 6) % 7;
  return addDays(iso, -dow);
}
function endOfWeek(iso: string): string {
  return addDays(startOfWeek(iso), 6);
}
function addDays(iso: string, n: number): string {
  const d = parseUTCDate(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** True for either AbortController or axios-style cancellation errors. Both
 *  can fire when Strict Mode unmounts a section's effect mid-flight. */
function _isAbort(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === "CanceledError" || e.name === "AbortError";
}
