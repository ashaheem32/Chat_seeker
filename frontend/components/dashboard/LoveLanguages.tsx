"use client";

/**
 * LoveLanguages — Module 06 of the dashboard.
 *
 * Sections:
 *   1. Section header + warm copy.
 *   2. Per-participant cards: radar chart (5 axes), summary sentence,
 *      and 3 expandable example messages per category.
 *   3. Comparison view: overlaid radar chart for both participants.
 *   4. Compatibility insight (Claude-written paragraph).
 *
 * Tone: warm, observational, never prescriptive. The product framing is
 * "how each of you tends to express care", not "ranking who loves more".
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";
import {
  ChevronDown,
  ChevronUp,
  Gift,
  Hand,
  HeartHandshake,
  Lightbulb,
  MessageSquare,
  Sparkles,
  Star,
  Users,
} from "lucide-react";
import { format, parseISO } from "date-fns";

import { Button } from "@/components/ui/button";
import { getLoveLanguageReport } from "@/lib/api";
import type {
  LoveLanguageBreakdown,
  LoveLanguageCategory,
  LoveLanguageReport,
  SampleMessage,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Category metadata. Keep order matching the backend's `LOVE_LANGUAGE_ORDER`
// so the radar axes stay stable.
const CATEGORY_META: Record<
  LoveLanguageCategory,
  { label: string; icon: typeof Star; color: string; copy: string }
> = {
  words_of_affirmation: {
    label: "Words of Affirmation",
    icon: MessageSquare,
    color: "#ec4899", // pink
    copy: "Compliments, encouragement, the explicit \"I love you\".",
  },
  acts_of_service: {
    label: "Acts of Service",
    icon: HeartHandshake,
    color: "#22c55e", // green
    copy: "Showing up, taking care of things, lightening the load.",
  },
  quality_time: {
    label: "Quality Time",
    icon: Users,
    color: "#06b6d4", // cyan
    copy: "Plans to be together, missing each other, undivided attention.",
  },
  physical_touch: {
    label: "Physical Touch",
    icon: Hand,
    color: "#f97316", // orange
    copy: "Hugs, kisses, holding — closeness through the body.",
  },
  gift_giving: {
    label: "Gift Giving",
    icon: Gift,
    color: "#a855f7", // purple
    copy: "Surprises, picking up something thoughtful, bringing things.",
  },
};

const CATEGORY_ORDER: LoveLanguageCategory[] = [
  "words_of_affirmation",
  "acts_of_service",
  "quality_time",
  "physical_touch",
  "gift_giving",
];

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function LoveLanguages({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<LoveLanguageReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchedRef = useRef<string | null>(null);

  useEffect(() => {
    if (fetchedRef.current === uploadId) return;
    fetchedRef.current = uploadId;
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    getLoveLanguageReport(uploadId, { signal: ctrl.signal })
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load love languages");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <section className="space-y-6" aria-labelledby="love-languages-heading">
      <header>
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Module 06
        </p>
        <h2
          id="love-languages-heading"
          className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
        >
          How You Both Express Love
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          A read on how affection shows up in your messages — based on Gary
          Chapman&apos;s five love languages framework. There&apos;s no
          ranking here, just a portrait of what each of you tends to reach for.
        </p>
      </header>

      {error ? (
        <ErrorPanel error={error} />
      ) : loading || !data ? (
        <LoveSkeleton />
      ) : data.participants.length === 0 ? (
        <EmptyState />
      ) : (
        <Body data={data} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

function Body({ data }: { data: LoveLanguageReport }) {
  return (
    <div className="space-y-6">
      <CategoryLegend />

      <div className="grid gap-4 lg:grid-cols-2">
        {data.participants.map((p) => (
          <ParticipantCard key={p.sender} breakdown={p} />
        ))}
      </div>

      {data.participants.length >= 2 ? (
        <ComparisonCard breakdowns={data.participants} />
      ) : null}

      <CompatibilityCard
        text={data.compatibility_insight}
        usedLlm={data.used_llm}
      />

      {!data.used_llm ? <FallbackNote /> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Category legend
// ---------------------------------------------------------------------------

function CategoryLegend() {
  return (
    <article className="surface-card p-4 animate-fade-up">
      <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
        The five love languages
      </p>
      <ul className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {CATEGORY_ORDER.map((cat) => {
          const meta = CATEGORY_META[cat];
          const Icon = meta.icon;
          return (
            <li key={cat} className="flex items-start gap-2">
              <span
                className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border"
                style={{
                  borderColor: meta.color,
                  background: `${meta.color}1a`,
                  color: meta.color,
                }}
                aria-hidden
              >
                <Icon className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0">
                <p className="font-display text-xs font-semibold">{meta.label}</p>
                <p className="text-[11px] leading-snug text-muted-foreground">
                  {meta.copy}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Participant card
// ---------------------------------------------------------------------------

function ParticipantCard({
  breakdown,
}: {
  breakdown: LoveLanguageBreakdown;
}) {
  const hue = stringToHue(breakdown.sender);
  const senderColor = `hsl(${hue} 70% 60%)`;

  // Build the Recharts-shaped data: one entry per category with the
  // sender's share scaled to 0-100 for readability on the radial axis.
  const radarData = useMemo(
    () =>
      breakdown.distribution.map((d) => ({
        category: CATEGORY_META[d.category].label,
        share: Math.round(d.share * 100),
        // Carry the raw category through so the tick formatter can reach it.
        _key: d.category,
      })),
    [breakdown.distribution],
  );

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="flex h-8 w-8 items-center justify-center rounded-full text-[11px] font-semibold"
            style={{
              background: `linear-gradient(135deg, hsl(${hue} 60% 45%), hsl(${(hue + 30) % 360} 70% 55%))`,
            }}
            aria-hidden
          >
            {getInitials(breakdown.sender)}
          </span>
          <div className="min-w-0">
            <h3 className="truncate font-display text-base font-semibold">
              {breakdown.sender}
            </h3>
            <p className="text-[11px] text-muted-foreground">
              {breakdown.total_classified.toLocaleString()} affectionate messages classified
            </p>
          </div>
        </div>
        {breakdown.primary ? (
          <PrimaryBadge category={breakdown.primary} />
        ) : null}
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="h-56 w-full">
          <ResponsiveContainer>
            <RadarChart data={radarData} cx="50%" cy="50%" outerRadius="75%">
              <PolarGrid stroke="hsl(var(--border))" strokeOpacity={0.4} />
              <PolarAngleAxis
                dataKey="category"
                tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 100]}
                tick={false}
                axisLine={false}
              />
              <Radar
                name={breakdown.sender}
                dataKey="share"
                stroke={senderColor}
                fill={senderColor}
                fillOpacity={0.35}
                strokeWidth={2}
                isAnimationActive
                animationDuration={700}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Summary
          </p>
          <p className="mt-1 text-sm leading-relaxed text-foreground">
            {breakdown.summary}
          </p>
        </div>
      </div>

      <CategoryAccordion distribution={breakdown.distribution} />
    </article>
  );
}

function PrimaryBadge({ category }: { category: LoveLanguageCategory }) {
  const meta = CATEGORY_META[category];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider"
      style={{ borderColor: meta.color, color: meta.color }}
    >
      <Star className="h-2.5 w-2.5" />
      Primary · {meta.label.split(" ")[0]}
    </span>
  );
}

function CategoryAccordion({
  distribution,
}: {
  distribution: LoveLanguageBreakdown["distribution"];
}) {
  const [expanded, setExpanded] = useState<LoveLanguageCategory | null>(null);
  // Keep meaningful entries only — collapse zero-share categories so the
  // expand list isn't padded with empty rows.
  const visible = distribution.filter((d) => d.count > 0);
  if (visible.length === 0) return null;
  return (
    <div className="mt-4 border-t border-border/60 pt-3">
      <p className="mb-2 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
        Top examples per category
      </p>
      <ul className="space-y-1.5">
        {visible.map((d) => {
          const meta = CATEGORY_META[d.category];
          const Icon = meta.icon;
          const isOpen = expanded === d.category;
          return (
            <li
              key={d.category}
              className="rounded-lg border border-border/60 bg-card-elevated/40"
            >
              <button
                type="button"
                onClick={() =>
                  setExpanded(isOpen ? null : d.category)
                }
                className="flex w-full items-center gap-2 px-3 py-2 text-left"
                aria-expanded={isOpen}
              >
                <span
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md"
                  style={{
                    background: `${meta.color}22`,
                    color: meta.color,
                  }}
                  aria-hidden
                >
                  <Icon className="h-3 w-3" />
                </span>
                <span className="flex-1 truncate text-xs font-medium">
                  {meta.label}
                </span>
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {Math.round(d.share * 100)}% · {d.count}
                </span>
                <span className="text-muted-foreground">
                  {isOpen ? (
                    <ChevronUp className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5" />
                  )}
                </span>
              </button>
              {isOpen ? (
                <div className="border-t border-border/60 px-3 py-2">
                  {d.examples.length === 0 ? (
                    <p className="text-[11px] italic text-muted-foreground">
                      No standout examples — most matches were brief.
                    </p>
                  ) : (
                    <ul className="space-y-1.5">
                      {d.examples.map((m) => (
                        <ExampleMessage
                          key={m.msg_id}
                          message={m}
                          accent={meta.color}
                        />
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ExampleMessage({
  message,
  accent,
}: {
  message: SampleMessage;
  accent: string;
}) {
  return (
    <li
      className="rounded-md border border-border/60 bg-card/50 p-2 text-[11px]"
      style={{ boxShadow: `inset 2px 0 0 ${accent}` }}
    >
      <p className="line-clamp-3 italic text-foreground/90">
        “{message.content_preview}”
      </p>
      <p className="mt-1 text-muted-foreground">
        {message.sender} · {format(parseISO(message.timestamp), "MMM d, yyyy")}
      </p>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Comparison
// ---------------------------------------------------------------------------

function ComparisonCard({
  breakdowns,
}: {
  breakdowns: LoveLanguageBreakdown[];
}) {
  // Recharts radar wants one record per category with one column per
  // sender. We zip the per-sender shares into category-keyed records so
  // every category row carries every sender's value.
  const data = useMemo(() => {
    return CATEGORY_ORDER.map((cat) => {
      const row: Record<string, string | number> = {
        category: CATEGORY_META[cat].label,
      };
      for (const b of breakdowns) {
        const entry = b.distribution.find((d) => d.category === cat);
        row[b.sender] = entry ? Math.round(entry.share * 100) : 0;
      }
      return row;
    });
  }, [breakdowns]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-2">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Side by side
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          How your styles overlap
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Each shape shows where someone&apos;s expressions cluster. Where
          they overlap, you tend to speak the same dialect.
        </p>
      </header>

      <div className="h-72 w-full">
        <ResponsiveContainer>
          <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
            <PolarGrid stroke="hsl(var(--border))" strokeOpacity={0.4} />
            <PolarAngleAxis
              dataKey="category"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
            />
            <PolarRadiusAxis
              angle={90}
              domain={[0, 100]}
              tick={false}
              axisLine={false}
            />
            {breakdowns.map((b) => {
              const color = `hsl(${stringToHue(b.sender)} 70% 60%)`;
              return (
                <Radar
                  key={b.sender}
                  name={b.sender}
                  dataKey={b.sender}
                  stroke={color}
                  fill={color}
                  fillOpacity={0.25}
                  strokeWidth={2}
                  isAnimationActive
                  animationDuration={700}
                />
              );
            })}
          </RadarChart>
        </ResponsiveContainer>
      </div>

      <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {breakdowns.map((b) => (
          <li key={b.sender} className="inline-flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: `hsl(${stringToHue(b.sender)} 70% 60%)` }}
            />
            <span className="text-foreground">{b.sender}</span>
            {b.primary ? (
              <span className="text-muted-foreground">
                · primary {CATEGORY_META[b.primary].label}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Compatibility insight
// ---------------------------------------------------------------------------

function CompatibilityCard({
  text,
  usedLlm,
}: {
  text: string;
  usedLlm: boolean;
}) {
  if (!text || !usedLlm) return null;
  return (
    <article className="surface-card relative overflow-hidden p-5 animate-fade-up">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-50"
        style={{
          background:
            "radial-gradient(60% 80% at 0% 0%, hsl(var(--primary) / 0.12), transparent 70%)",
        }}
      />
      <div className="flex items-start gap-3">
        <span
          className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
          aria-hidden
        >
          <Sparkles className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Your love-language match
          </p>
          <p className="mt-1 text-sm leading-relaxed text-foreground">{text}</p>
        </div>
      </div>
    </article>
  );
}

function FallbackNote() {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-card-elevated/40 px-3 py-2">
      <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Without an Anthropic API key configured, classification falls back
        to a small keyword heuristic. The shapes are directionally right
        but less precise — set <code>ANTHROPIC_API_KEY</code> for the
        AI-classified read.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + empty state
// ---------------------------------------------------------------------------

function LoveSkeleton() {
  return (
    <div className="space-y-4">
      <span className="shimmer block h-20 rounded-2xl" />
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="surface-card flex flex-col gap-3 p-5">
            <span className="shimmer h-10 w-40 rounded" />
            <span className="shimmer h-56 rounded-xl" />
            <span className="shimmer h-8 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorPanel({ error }: { error: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
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
      <Sparkles className="h-5 w-5 text-primary" />
      <h3 className="font-display text-base font-semibold">
        Not enough affectionate signal yet
      </h3>
      <p className="max-w-sm text-xs text-muted-foreground">
        We didn&apos;t find enough positive, emotionally-charged messages to
        classify into love languages. That&apos;s rarely about whether the
        feeling is there — most chats just don&apos;t spell it out very often.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
