"use client";

/**
 * HealthScore — Module 07 of the dashboard.
 *
 * Layout:
 *   ┌─────────────────────────────┬───────────────────────────────────┐
 *   │ Big circular gauge          │ Narrative paragraph (Claude)      │
 *   │ Score 0-100, animated       │ Disclaimer underneath             │
 *   │ Band-colored (red/amber/    │                                   │
 *   │ green)                      │                                   │
 *   └─────────────────────────────┴───────────────────────────────────┘
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ Factor breakdown — 7 rows: icon + label + score bar + insight   │
 *   └─────────────────────────────────────────────────────────────────┘
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ Methodology (expandable) — explains what the score actually     │
 *   │ measures. Always visible after the disclaimer.                  │
 *   └─────────────────────────────────────────────────────────────────┘
 *
 * Tone: warm, observational, never grading. The disclaimer renders
 * directly under the score so users see it before any factor breakdown.
 */

import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Bell,
  ChevronDown,
  ChevronUp,
  Heart,
  Info,
  Lightbulb,
  MessageCircle,
  Repeat,
  Shield,
  Sparkles,
  Timer,
  TrendingUp,
} from "lucide-react";

import { getHealthScoreReport } from "@/lib/api";
import type {
  HealthFactorKey,
  HealthScoreFactor,
  HealthScoreReport,
} from "@/lib/types";
import { useCountUp } from "@/lib/use-count-up";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FACTOR_ICONS: Record<HealthFactorKey, typeof Activity> = {
  communication_balance: Repeat,
  response_consistency: Timer,
  sentiment_trend: TrendingUp,
  conflict_recovery: Bell,
  affection_frequency: Heart,
  engagement_depth: MessageCircle,
  shared_activities: Sparkles,
};

const BAND_TONES: Record<
  HealthScoreReport["band"],
  { color: string; label: string; copy: string }
> = {
  red: {
    color: "hsl(var(--destructive))",
    label: "Tender",
    copy: "Some patterns are louder than others right now.",
  },
  amber: {
    color: "hsl(var(--warning))",
    label: "Mixed",
    copy: "A blend of strong patterns and ones that read as more uneven.",
  },
  green: {
    color: "hsl(var(--success))",
    label: "Strong",
    copy: "Several positive patterns showing up side by side.",
  },
};

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function HealthScore({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<HealthScoreReport | null>(null);
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

    getHealthScoreReport(uploadId, { signal: ctrl.signal })
      .then((r) => {
        if (cancelled) return;
        setData(r);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load health score");
        setLoading(false);
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <section className="space-y-6" aria-labelledby="health-score-heading">
      <header>
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Module 07
        </p>
        <h2
          id="health-score-heading"
          className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
        >
          Communication Health
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          A composite read on seven dimensions of how the conversation moves.
        </p>
      </header>

      {error ? (
        <ErrorPanel error={error} />
      ) : loading || !data ? (
        <ScoreSkeleton />
      ) : (
        <ScoreBody data={data} />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

function ScoreBody({ data }: { data: HealthScoreReport }) {
  const tone = BAND_TONES[data.band];

  return (
    <div className="space-y-6">
      <article className="surface-card relative overflow-hidden p-6 animate-fade-up">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 opacity-60"
          style={{
            background: `radial-gradient(60% 80% at 0% 0%, ${tone.color}1c, transparent 70%)`,
          }}
        />

        <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
          <div className="flex flex-col items-center justify-center">
            <ScoreGauge value={data.overall_score} band={data.band} />
            <div className="mt-3 text-center">
              <p
                className="text-[10px] uppercase tracking-[0.22em]"
                style={{ color: tone.color }}
              >
                {tone.label} signal
              </p>
              <p className="mt-1 max-w-[220px] text-xs text-muted-foreground">
                {tone.copy}
              </p>
            </div>
          </div>

          <div className="flex flex-col">
            <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
              Reflection
            </p>
            <p className="mt-1 text-sm leading-relaxed text-foreground">
              {data.narrative}
            </p>

            <Disclaimer text={data.disclaimer} />
          </div>
        </div>
      </article>

      <FactorList factors={data.factors} />

      <Methodology methodology={data.methodology} />

      {!data.used_llm ? <FallbackNote /> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Score gauge — custom SVG with animated count-up
// ---------------------------------------------------------------------------

function ScoreGauge({
  value,
  band,
}: {
  value: number;
  band: HealthScoreReport["band"];
}) {
  const animated = useCountUp(value, { duration: 1200 });
  const tone = BAND_TONES[band];

  // Single big arc; we render two stacked circles (track + progress) and
  // animate stroke-dashoffset for a smooth fill-up.
  const r = 78;
  const circumference = 2 * Math.PI * r;
  const progressFraction = Math.max(0, Math.min(100, animated)) / 100;
  const offset = circumference * (1 - progressFraction);

  return (
    <div className="relative h-44 w-44">
      <svg viewBox="-100 -100 200 200" className="h-full w-full -rotate-90">
        <defs>
          <linearGradient id="gauge-grad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={tone.color} stopOpacity={0.6} />
            <stop offset="100%" stopColor={tone.color} stopOpacity={1} />
          </linearGradient>
        </defs>

        {/* Track */}
        <circle
          r={r}
          cx="0"
          cy="0"
          fill="none"
          stroke="hsl(var(--muted))"
          strokeWidth={14}
          strokeOpacity={0.35}
        />
        {/* Progress */}
        <circle
          r={r}
          cx="0"
          cy="0"
          fill="none"
          stroke="url(#gauge-grad)"
          strokeWidth={14}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{
            transition: "stroke-dashoffset 1200ms cubic-bezier(0.2, 0.8, 0.2, 1)",
            filter: `drop-shadow(0 0 16px ${tone.color}66)`,
          }}
        />
      </svg>

      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-display text-[3.25rem] font-semibold leading-none tabular-nums"
          style={{ color: tone.color }}
        >
          {Math.round(animated)}
        </span>
        <span className="mt-0.5 text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          / 100
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Disclaimer
// ---------------------------------------------------------------------------

function Disclaimer({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="mt-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3">
      <Shield className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
      <p className="text-[11px] leading-relaxed text-foreground/85">{text}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Factor breakdown
// ---------------------------------------------------------------------------

function FactorList({ factors }: { factors: HealthScoreFactor[] }) {
  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Factor breakdown
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          What goes into the number
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Each factor contributes a weighted slice of the composite. Bars
          show the factor&apos;s standalone score; the percentage on the
          right is its contribution.
        </p>
      </header>

      <ul className="space-y-3">
        {factors.map((f, i) => (
          <FactorRow key={f.key} factor={f} index={i} />
        ))}
      </ul>
    </article>
  );
}

function FactorRow({
  factor,
  index,
}: {
  factor: HealthScoreFactor;
  index: number;
}) {
  const Icon = FACTOR_ICONS[factor.key];
  // Color the bar by the factor's standalone score, not the band — gives
  // the user a quick visual read on which factors are pulling the
  // composite up vs. down.
  const factorBand =
    factor.score >= 0.7
      ? "green"
      : factor.score >= 0.4
      ? "amber"
      : "red";
  const tone = BAND_TONES[factorBand];
  const animated = useCountUp(factor.score * 100, {
    duration: 800,
    delay: 100 + index * 60,
    decimals: 0,
  });

  return (
    <li
      className={cn(
        "rounded-xl border border-border bg-card-elevated/40 p-4",
        "animate-fade-up",
      )}
      style={{
        animationDelay: `${index * 60}ms`,
        animationFillMode: "both",
        boxShadow: `inset 2px 0 0 ${tone.color}`,
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
          style={{
            background: `${tone.color}1c`,
            color: tone.color,
          }}
          aria-hidden
        >
          <Icon className="h-4 w-4" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <h4 className="font-display text-sm font-semibold">
              {factor.label}
            </h4>
            <span className="text-[10px] tabular-nums text-muted-foreground">
              weight {Math.round(factor.weight * 100)}%
            </span>
          </div>

          {/* Score bar */}
          <div className="mt-2 flex items-center gap-3">
            <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full transition-[width] duration-1000 ease-out"
                style={{
                  width: `${Math.round(animated)}%`,
                  background: `linear-gradient(90deg, ${tone.color}80, ${tone.color})`,
                  boxShadow: `0 0 8px ${tone.color}66`,
                }}
              />
            </div>
            <span
              className="w-10 shrink-0 text-right font-display text-sm font-semibold tabular-nums"
              style={{ color: tone.color }}
            >
              {Math.round(animated)}
            </span>
          </div>

          {factor.raw_value ? (
            <p className="mt-1.5 text-[11px] tabular-nums text-muted-foreground">
              {factor.raw_value}
            </p>
          ) : null}

          {factor.insight ? (
            <p className="mt-1 text-[11px] leading-relaxed text-foreground/85">
              {factor.insight}
            </p>
          ) : null}
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Methodology
// ---------------------------------------------------------------------------

function Methodology({ methodology }: { methodology: string }) {
  const [open, setOpen] = useState(false);
  if (!methodology) return null;
  return (
    <article className="surface-card p-5 animate-fade-up">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground" />
          <p className="font-display text-sm font-semibold">What this means</p>
        </div>
        <span className="text-muted-foreground">
          {open ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </span>
      </button>
      {open ? (
        <div className="mt-3 space-y-3 border-t border-border/60 pt-3">
          <p className="text-xs leading-relaxed text-foreground/90">
            {methodology}
          </p>
          <p className="text-[11px] italic leading-relaxed text-muted-foreground">
            One last note: numbers in this section are descriptive, not
            prescriptive. There&apos;s no &ldquo;ideal&rdquo; score for any
            of these factors — what they show you is the shape of how you
            tend to text together.
          </p>
        </div>
      ) : null}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Fallback note + skeletons + error
// ---------------------------------------------------------------------------

function FallbackNote() {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-card-elevated/40 px-3 py-2">
      <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Without an Anthropic API key, the per-factor sentences you see are
        templated. Numbers are computed the same way; only the prose is
        less specific. Set <code>ANTHROPIC_API_KEY</code> for AI-written
        insights.
      </p>
    </div>
  );
}

function ScoreSkeleton() {
  return (
    <div className="space-y-6">
      <div className="surface-card p-6">
        <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
          <div className="flex justify-center">
            <span className="shimmer h-44 w-44 rounded-full" />
          </div>
          <div className="flex flex-col gap-2">
            <span className="shimmer h-3 w-24 rounded" />
            <span className="shimmer h-3 w-full rounded" />
            <span className="shimmer h-3 w-2/3 rounded" />
            <span className="shimmer mt-3 h-12 rounded-lg" />
          </div>
        </div>
      </div>
      <div className="surface-card p-5">
        <span className="shimmer mb-3 block h-3 w-32 rounded" />
        <ul className="space-y-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <li
              key={i}
              className="rounded-xl border border-border bg-card-elevated/40 p-4"
            >
              <div className="flex items-center gap-3">
                <span className="shimmer h-9 w-9 rounded-lg" />
                <div className="flex-1 space-y-1.5">
                  <span className="shimmer block h-3 w-32 rounded" />
                  <span className="shimmer block h-1.5 w-full rounded-full" />
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ErrorPanel({ error }: { error: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <Activity className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          Couldn&apos;t load this section
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
      </div>
    </div>
  );
}

