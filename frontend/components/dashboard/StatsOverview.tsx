"use client";

/**
 * StatsOverview — top-of-dashboard module.
 *
 * Layout:
 *   Row 1: 4 hero metric cards (Messages, Days, Words, Emojis)
 *   Row 2: 3 behavioral cards (Avg response gauge, Streak, Who texts first donut)
 *   Row 3: per-participant comparison rows
 *   Row 4: 6 fun-facts tiles
 *
 * Animations:
 *   Cards stagger-fade in (50ms apart). Hero numbers count up via
 *   `useCountUp`, with the same per-card delay, so a card's number only
 *   starts counting once the card has visually arrived. Reduced-motion
 *   users skip animations entirely (handled inside useCountUp).
 *
 * Loading / error:
 *   Renders a polished skeleton (shimmer-class shapes that mirror the
 *   final layout) while the request is in flight, and a focused error
 *   panel with retry on failure.
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Calendar,
  Clock,
  Flame,
  Hash,
  Heart,
  MessageCircle,
  MessageSquare,
  RefreshCcw,
  Smile,
  Sparkles,
  Star,
  Type,
  Users,
} from "lucide-react";
import { format } from "date-fns";

import { Button } from "@/components/ui/button";
import { getStatsOverview } from "@/lib/api";
import { useStore } from "@/lib/store";
import { toast } from "@/lib/toast";
import type {
  HourlyHistogramBucket,
  MessageReference,
  OverviewStats,
  ParticipantStats,
  WhoTextsFirstSlice,
} from "@/lib/types";
import { useCountUp } from "@/lib/use-count-up";
import { cn } from "@/lib/utils";

// Stagger between cards (ms). 50ms reads as "kinetic but not slow".
const STAGGER_MS = 50;

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function StatsOverview({ uploadId }: { uploadId: string }) {
  const setOverviewStats = useStore((s) => s.setOverviewStats);
  const cached = useStore((s) => s.overviewStats[uploadId]);

  const [stats, setStats] = useState<OverviewStats | null>(cached ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!cached);

  useEffect(() => {
    // Strict Mode in dev mounts the effect twice. We rely on
    // (AbortController + cancelled flag) to make the doubled run a no-op:
    // the first effect aborts on cleanup and its .catch returns early on
    // AbortError; the second effect fires a fresh fetch that wins.
    let cancelled = false;
    if (cached) {
      setStats(cached);
      setLoading(false);
      return;
    }

    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    getStatsOverview(uploadId, { signal: ctrl.signal })
      .then((data) => {
        if (cancelled) return;
        setStats(data);
        setOverviewStats(uploadId, data);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        const msg = e instanceof Error ? e.message : "Failed to load stats";
        setError(msg);
        setLoading(false);
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
    // We deliberately re-key the effect on uploadId only. setOverviewStats
    // is stable from Zustand and `cached` is read once at mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadId]);

  const onRefresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getStatsOverview(uploadId, { refresh: true });
      setStats(data);
      setOverviewStats(uploadId, data);
      toast.success("Stats refreshed");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Refresh failed";
      setError(msg);
      toast.error("Couldn't refresh stats", msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-labelledby="stats-overview-heading" className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Module 01
          </p>
          <h2
            id="stats-overview-heading"
            className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            Conversation at a Glance
          </h2>
        </div>
        {!loading && stats ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
            Refresh
          </Button>
        ) : null}
      </header>

      {error ? (
        <ErrorPanel error={error} onRetry={onRefresh} />
      ) : loading || !stats ? (
        <SkeletonGrid />
      ) : (
        <Grid stats={stats} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Top-level grid (the four rows)
// ---------------------------------------------------------------------------

function Grid({ stats }: { stats: OverviewStats }) {
  return (
    <div className="space-y-6">
      <Row1Hero stats={stats} />
      <Row2Behavioral stats={stats} />
      <Row3Participants stats={stats} />
      <Row4FunFacts stats={stats} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 1 — Hero metrics
// ---------------------------------------------------------------------------

function Row1Hero({ stats }: { stats: OverviewStats }) {
  // Each card animates with a stagger; pass the same `delay` into the
  // count-up so the number arrives in lockstep with its card.
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <HeroCard
        index={0}
        icon={MessageSquare}
        label="Total Messages"
        value={stats.total_messages}
        sublabel={
          stats.avg_messages_per_day > 0
            ? `${stats.avg_messages_per_day.toFixed(1)} per active day`
            : undefined
        }
      />
      <HeroCard
        index={1}
        icon={Calendar}
        label="Total Days"
        value={stats.conversation_days}
        sublabel={`${stats.active_days.toLocaleString()} active`}
      />
      <HeroCard
        index={2}
        icon={Type}
        label="Total Words"
        value={stats.total_words}
        sublabel={
          stats.total_messages > 0
            ? `${(stats.total_words / Math.max(1, stats.total_messages)).toFixed(1)} per msg`
            : undefined
        }
      />
      <HeroCard
        index={3}
        icon={Smile}
        label="Total Emojis"
        value={stats.total_emojis}
        sublabel={
          stats.total_messages > 0
            ? `${((stats.total_emojis / stats.total_messages) * 100).toFixed(0)}% of messages`
            : undefined
        }
      />
    </div>
  );
}

interface HeroCardProps {
  index: number;
  icon: typeof MessageSquare;
  label: string;
  value: number;
  sublabel?: string;
}

function HeroCard({ index, icon: Icon, label, value, sublabel }: HeroCardProps) {
  const delay = index * STAGGER_MS;
  const animated = useCountUp(value, { duration: 1100, delay });

  return (
    <article
      className={cn(
        "surface-card group relative overflow-hidden p-5",
        "transition-all duration-300",
        "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_8px_40px_-12px_hsl(var(--primary)/0.45)]",
        "animate-fade-up",
      )}
      style={{ animationDelay: `${delay}ms`, animationFillMode: "both" }}
    >
      {/* Soft indigo wash on hover */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 bg-indigo-glow opacity-0 transition-opacity duration-500 group-hover:opacity-60"
      />

      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          {label}
        </span>
        <Icon className="h-4 w-4 text-muted-foreground transition group-hover:text-primary" />
      </div>

      <div className="mt-3 font-display text-4xl font-semibold tracking-tight tabular-nums">
        {formatNumber(animated)}
      </div>
      {sublabel ? (
        <div className="mt-1 text-xs text-muted-foreground">{sublabel}</div>
      ) : null}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Row 2 — Behavioral
// ---------------------------------------------------------------------------

function Row2Behavioral({ stats }: { stats: OverviewStats }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <BehavioralCard
        index={4}
        title="Avg response time"
        eyebrow="Cadence"
      >
        <ResponseGauge minutes={stats.avg_response_time_minutes} />
      </BehavioralCard>

      <BehavioralCard
        index={5}
        title="Longest streak"
        eyebrow="Consistency"
      >
        <StreakDisplay days={stats.longest_streak} silence={stats.longest_silence} />
      </BehavioralCard>

      <BehavioralCard
        index={6}
        title="Who texts first"
        eyebrow="Initiative"
      >
        <WhoTextsFirstDonut slices={stats.who_texts_first} />
      </BehavioralCard>
    </div>
  );
}

function BehavioralCard({
  index,
  title,
  eyebrow,
  children,
}: {
  index: number;
  title: string;
  eyebrow: string;
  children: React.ReactNode;
}) {
  const delay = index * STAGGER_MS;
  return (
    <article
      className="surface-card flex h-full flex-col p-5 animate-fade-up transition-colors hover:border-primary/30"
      style={{ animationDelay: `${delay}ms`, animationFillMode: "both" }}
    >
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          {eyebrow}
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">{title}</h3>
      </header>
      <div className="flex-1">{children}</div>
    </article>
  );
}

function ResponseGauge({ minutes }: { minutes: number | null }) {
  // Map minutes onto a 0-1 fill where 0min = 1.0 (full) and 60min+ = 0.05
  // (near empty). Uses a log-ish curve so single-digit minutes look fast
  // but quick-enough replies (under ~10min) still differ visibly.
  const ratio = useMemo(() => {
    if (minutes == null) return 0;
    const clamped = Math.max(0, Math.min(60, minutes));
    return Math.max(0.05, 1 - Math.log10(1 + clamped) / Math.log10(61));
  }, [minutes]);
  const animatedValue = useCountUp(minutes ?? 0, { duration: 900, decimals: 1 });

  if (minutes == null) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2">
        <Clock className="h-5 w-5 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">Not enough back-and-forth yet.</p>
      </div>
    );
  }

  // Half-circle SVG gauge.
  const r = 52;
  const circumference = Math.PI * r;
  return (
    <div className="flex h-full flex-col items-center justify-between gap-2">
      <svg viewBox="0 0 140 80" width="100%" height="80" aria-hidden>
        <defs>
          <linearGradient id="gauge-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--primary))" />
            <stop offset="100%" stopColor="hsl(var(--accent))" />
          </linearGradient>
        </defs>
        <path
          d="M18 70 A52 52 0 0 1 122 70"
          fill="none"
          stroke="hsl(var(--border))"
          strokeWidth="10"
          strokeLinecap="round"
        />
        <path
          d="M18 70 A52 52 0 0 1 122 70"
          fill="none"
          stroke="url(#gauge-grad)"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={circumference * (1 - ratio)}
          style={{ transition: "stroke-dashoffset 900ms cubic-bezier(0.2, 0.8, 0.2, 1)" }}
        />
      </svg>
      <div className="text-center">
        <div className="font-display text-2xl font-semibold tabular-nums">
          {animatedValue.toFixed(animatedValue >= 10 ? 0 : 1)}
          <span className="ml-1 text-sm font-medium text-muted-foreground">min</span>
        </div>
        <div className="text-xs text-muted-foreground">{describeResponse(minutes)}</div>
      </div>
    </div>
  );
}

function StreakDisplay({ days, silence }: { days: number; silence: number }) {
  const animated = useCountUp(days, { duration: 900 });
  const onFire = days > 30;
  return (
    <div className="flex h-full items-center justify-between gap-4">
      <div>
        <div className="flex items-baseline gap-2">
          <span className="font-display text-4xl font-semibold tabular-nums">
            {animated.toLocaleString()}
          </span>
          <span className="text-sm text-muted-foreground">consecutive days</span>
        </div>
        {silence > 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Longest silence: <span className="text-foreground">{silence} days</span>
          </p>
        ) : null}
      </div>
      <div
        className={cn(
          "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border",
          onFire
            ? "border-warning/40 bg-warning/10 text-warning shadow-[0_0_24px_-4px_hsl(var(--warning)/0.6)]"
            : "border-border bg-muted/40 text-muted-foreground",
        )}
        title={onFire ? "30+ day streak" : "Streak"}
        aria-hidden
      >
        <Flame className={cn("h-5 w-5", onFire && "animate-pulse-soft")} />
      </div>
    </div>
  );
}

function WhoTextsFirstDonut({ slices }: { slices: WhoTextsFirstSlice[] }) {
  if (slices.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2">
        <MessageCircle className="h-5 w-5 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">Not enough days yet.</p>
      </div>
    );
  }

  // Stable, deterministic colour per sender (matches the participant
  // avatars in the sidebar).
  const colored = slices.map((s) => ({ ...s, hue: stringToHue(s.sender) }));

  // Build the donut as a stacked stroke-dasharray on a single circle.
  // Cumulative lengths drive both `strokeDasharray` and `strokeDashoffset`.
  const r = 38;
  const c = 2 * Math.PI * r;
  let acc = 0;

  return (
    <div className="flex h-full items-center gap-4">
      <div className="relative h-28 w-28 shrink-0">
        <svg viewBox="-50 -50 100 100" className="h-full w-full -rotate-90">
          <circle
            r={r}
            cx="0"
            cy="0"
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth="14"
          />
          {colored.map((slice) => {
            const len = c * slice.share;
            const dasharray = `${len} ${c - len}`;
            const dashoffset = -acc;
            acc += len;
            return (
              <circle
                key={slice.sender}
                r={r}
                cx="0"
                cy="0"
                fill="none"
                stroke={`hsl(${slice.hue} 70% 60%)`}
                strokeWidth="14"
                strokeDasharray={dasharray}
                strokeDashoffset={dashoffset}
                style={{ transition: "stroke-dashoffset 700ms ease-out" }}
              />
            );
          })}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-display text-lg font-semibold leading-none">
            {Math.round((colored[0]?.share ?? 0) * 100)}%
          </span>
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {colored[0]?.sender ?? ""}
          </span>
        </div>
      </div>

      <ul className="flex-1 space-y-1.5 text-xs">
        {colored.map((s) => (
          <li key={s.sender} className="flex items-center gap-2">
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: `hsl(${s.hue} 70% 60%)` }}
            />
            <span className="truncate text-foreground">{s.sender}</span>
            <span className="ml-auto tabular-nums text-muted-foreground">
              {Math.round(s.share * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 3 — Participant comparison
// ---------------------------------------------------------------------------

function Row3Participants({ stats }: { stats: OverviewStats }) {
  const total = stats.per_participant.reduce((s, p) => s + p.message_count, 0);
  if (total === 0) return null;
  return (
    <article
      className="surface-card p-5 animate-fade-up"
      style={{ animationDelay: `${7 * STAGGER_MS}ms`, animationFillMode: "both" }}
    >
      <header className="mb-4 flex items-center justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Voices
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Participant comparison
          </h3>
        </div>
        <Users className="h-4 w-4 text-muted-foreground" />
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        {stats.per_participant.map((p, i) => (
          <ParticipantRow
            key={p.name}
            participant={p}
            total={total}
            delayMs={(7 + i) * STAGGER_MS}
          />
        ))}
      </div>
    </article>
  );
}

function ParticipantRow({
  participant: p,
  total,
  delayMs,
}: {
  participant: ParticipantStats;
  total: number;
  delayMs: number;
}) {
  const hue = stringToHue(p.name);
  const share = total > 0 ? p.message_count / total : 0;
  const animatedMsgs = useCountUp(p.message_count, { duration: 800, delay: delayMs });
  const animatedWords = useCountUp(p.word_count, { duration: 800, delay: delayMs });

  return (
    <div
      className="rounded-xl border border-border bg-card-elevated/40 p-4 animate-fade-up"
      style={{ animationDelay: `${delayMs}ms`, animationFillMode: "both" }}
    >
      <div className="flex items-center gap-3">
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 border-card text-xs font-semibold"
          style={{
            background: `linear-gradient(135deg, hsl(${hue} 60% 45%), hsl(${(hue + 30) % 360} 70% 55%))`,
          }}
          aria-hidden
        >
          {getInitials(p.name)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-display text-sm font-semibold">{p.name}</div>
          <div className="text-xs text-muted-foreground">
            <span className="tabular-nums text-foreground">
              {formatNumber(animatedMsgs)}
            </span>{" "}
            messages ·{" "}
            <span className="tabular-nums text-foreground">
              {formatNumber(animatedWords)}
            </span>{" "}
            words ·{" "}
            <span className="tabular-nums text-foreground">
              {p.avg_message_length.toFixed(0)}
            </span>{" "}
            chars/msg
          </div>
        </div>
      </div>

      {/* Proportion bar */}
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-[width] duration-1000 ease-out"
          style={{
            width: `${share * 100}%`,
            background: `linear-gradient(90deg, hsl(${hue} 70% 55%), hsl(${(hue + 30) % 360} 75% 65%))`,
            boxShadow: `0 0 12px hsl(${hue} 70% 55% / 0.45)`,
          }}
        />
      </div>

      {/* Badges */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {p.most_used_word ? (
          <Badge tone="primary" icon={Hash}>
            {p.most_used_word}
          </Badge>
        ) : null}
        {p.favorite_emoji ? (
          <Badge tone="default" icon={Smile}>
            <span className="text-base leading-none">{p.favorite_emoji}</span>
          </Badge>
        ) : null}
        {p.question_count > 0 ? (
          <span className="text-[11px] text-muted-foreground">
            {p.question_count} questions
          </span>
        ) : null}
        {p.exclamation_count > 0 ? (
          <span className="text-[11px] text-muted-foreground">
            {p.exclamation_count} exclamations
          </span>
        ) : null}
      </div>
    </div>
  );
}

function Badge({
  children,
  icon: Icon,
  tone,
}: {
  children: React.ReactNode;
  icon: typeof Hash;
  tone: "primary" | "default";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
        tone === "primary"
          ? "border-primary/30 bg-primary/10 text-primary"
          : "border-border bg-card text-foreground",
      )}
    >
      <Icon className="h-3 w-3" />
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Row 4 — Fun facts
// ---------------------------------------------------------------------------

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Row4FunFacts({ stats }: { stats: OverviewStats }) {
  const items: FunFact[] = [
    {
      title: "Busiest hour",
      value:
        stats.busiest_hour != null
          ? `${formatHour(stats.busiest_hour)}`
          : "—",
      icon: Clock,
      visual: <HourSparkline buckets={stats.hourly_histogram} highlight={stats.busiest_hour} />,
      sublabel: stats.busiest_hour != null ? "Peak across all days" : undefined,
    },
    {
      title: "Busiest day",
      value:
        stats.busiest_day_of_week != null
          ? DAY_NAMES[stats.busiest_day_of_week] ?? "—"
          : "—",
      icon: Calendar,
      sublabel: stats.most_active_month
        ? `Most active month: ${stats.most_active_month}`
        : undefined,
    },
    {
      title: "Longest message",
      value: stats.longest_message
        ? `${stats.longest_message.char_count.toLocaleString()} chars`
        : "—",
      icon: Type,
      sublabel: stats.longest_message
        ? `${stats.longest_message.sender} · ${format(
            new Date(stats.longest_message.timestamp),
            "MMM d, yyyy",
          )}`
        : undefined,
      preview: stats.longest_message?.content_preview,
    },
    {
      title: "Most replied-to",
      value: stats.most_replied_to
        ? `${stats.most_replied_to.reply_count ?? 0} replies`
        : "—",
      icon: Heart,
      sublabel: stats.most_replied_to
        ? `${stats.most_replied_to.sender} · ${format(
            new Date(stats.most_replied_to.timestamp),
            "MMM d, yyyy",
          )}`
        : undefined,
      preview: stats.most_replied_to?.content_preview,
    },
    {
      title: "Most used word",
      value: stats.most_used_word_overall ? `"${stats.most_used_word_overall}"` : "—",
      icon: Star,
      sublabel: "Across both senders",
    },
    {
      title: "First message",
      value: stats.first_message
        ? format(new Date(stats.first_message.timestamp), "MMM d, yyyy")
        : "—",
      icon: Sparkles,
      sublabel: stats.first_message
        ? `${stats.first_message.sender} kicked things off`
        : undefined,
      preview: stats.first_message?.content_preview,
    },
  ];

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item, i) => (
        <FunFactCard key={item.title} {...item} index={9 + i} />
      ))}
    </div>
  );
}

interface FunFact {
  title: string;
  value: string;
  icon: typeof Sparkles;
  sublabel?: string;
  preview?: string;
  visual?: React.ReactNode;
}

function FunFactCard({
  title,
  value,
  icon: Icon,
  sublabel,
  preview,
  visual,
  index,
}: FunFact & { index: number }) {
  const delay = index * STAGGER_MS;
  return (
    <article
      className="surface-card group p-4 transition-colors hover:border-primary/30 animate-fade-up"
      style={{ animationDelay: `${delay}ms`, animationFillMode: "both" }}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          {title}
        </span>
        <Icon className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-primary" />
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="font-display text-2xl font-semibold tracking-tight">
          {value}
        </div>
        {visual}
      </div>
      {sublabel ? (
        <div className="mt-1 text-xs text-muted-foreground">{sublabel}</div>
      ) : null}
      {preview ? (
        <p className="mt-2 line-clamp-2 rounded-md bg-card-elevated/60 px-2 py-1.5 text-xs italic text-muted-foreground">
          “{preview}”
        </p>
      ) : null}
    </article>
  );
}

function HourSparkline({
  buckets,
  highlight,
}: {
  buckets: HourlyHistogramBucket[];
  highlight: number | null;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="flex h-10 items-end gap-[2px]" aria-hidden>
      {buckets.map((b) => {
        const h = Math.max(2, Math.round((b.count / max) * 100));
        const isHi = highlight != null && b.hour === highlight;
        return (
          <div
            key={b.hour}
            className={cn(
              "w-1 rounded-sm transition",
              isHi ? "bg-primary" : "bg-muted-foreground/40 group-hover:bg-muted-foreground/60",
            )}
            style={{ height: `${h}%` }}
          />
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton + error states
// ---------------------------------------------------------------------------

function SkeletonGrid() {
  return (
    <div className="space-y-6">
      {/* Hero row skeleton */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="surface-card flex flex-col gap-3 p-5 animate-fade-up"
            style={{ animationDelay: `${i * STAGGER_MS}ms`, animationFillMode: "both" }}
          >
            <div className="flex items-center justify-between">
              <span className="shimmer h-3 w-20 rounded" />
              <span className="shimmer h-3 w-3 rounded-full" />
            </div>
            <span className="shimmer h-9 w-24 rounded" />
            <span className="shimmer h-3 w-32 rounded" />
          </div>
        ))}
      </div>

      {/* Behavioral row skeleton */}
      <div className="grid gap-4 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="surface-card flex h-40 flex-col gap-3 p-5 animate-fade-up"
            style={{
              animationDelay: `${(4 + i) * STAGGER_MS}ms`,
              animationFillMode: "both",
            }}
          >
            <span className="shimmer h-3 w-20 rounded" />
            <span className="shimmer h-3 w-32 rounded" />
            <div className="mt-auto shimmer h-12 w-full rounded" />
          </div>
        ))}
      </div>

      {/* Fun-facts row skeleton */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="surface-card flex flex-col gap-2 p-4 animate-fade-up"
            style={{
              animationDelay: `${(8 + i) * STAGGER_MS}ms`,
              animationFillMode: "both",
            }}
          >
            <span className="shimmer h-3 w-20 rounded" />
            <span className="shimmer h-6 w-28 rounded" />
            <span className="shimmer h-3 w-32 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorPanel({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="surface-card flex items-start gap-3 border-destructive/30 p-5">
      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="font-display text-sm font-semibold text-foreground">
          Couldn&apos;t load stats
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{error}</p>
      </div>
      <Button size="sm" variant="outline" onClick={onRetry}>
        <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
        Retry
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNumber(n: number): string {
  // Numbers under 10k render with grouping; bigger numbers compact to "12.4k"
  // so heroes don't blow past the card width on busy chats.
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return Math.round(n).toLocaleString();
}

function formatHour(h: number): string {
  // 0..23 → "9 AM" / "9 PM" so the hero card reads conversationally.
  const suffix = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12} ${suffix}`;
}

function describeResponse(minutes: number): string {
  if (minutes < 1) return "Almost real-time";
  if (minutes < 5) return "Very quick replies";
  if (minutes < 15) return "Conversational pace";
  if (minutes < 60) return "Steady back-and-forth";
  return "Slower, deliberate";
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

/** True for either AbortController or axios-style cancellation errors.
 *  Both can fire when Strict Mode unmounts the effect mid-flight. */
function _isAbort(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === "CanceledError" || e.name === "AbortError";
}
