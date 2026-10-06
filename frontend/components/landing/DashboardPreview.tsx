"use client";

/**
 * DashboardPreview — a self-animating miniature of the real dashboard,
 * used as the hero visual. Nothing here fetches data; the numbers are
 * illustrative. It demonstrates what the modules look like:
 *
 *   - Header strip with a fake chat title + status pill
 *   - Three hero metrics that count up when the card scrolls into view
 *   - Sentiment line chart drawn stroke-by-stroke (SVG dash animation)
 *   - Emotion "equaliser" bars that breathe
 *   - Health-score gauge that fills
 *   - Floating chips (emoji, love-language, conflict flag) orbiting outside
 *   - A scan beam sweeping over the whole surface
 */

import {
  Activity,
  Heart,
  MessageSquareText,
  Sparkles,
  TrendingUp,
} from "lucide-react";

import { useCountUp } from "@/lib/use-count-up";
import { useReveal } from "@/lib/use-reveal";
import { cn } from "@/lib/utils";

import { TiltCard } from "./TiltCard";

const LINE_PATH =
  "M0,78 C30,70 45,40 70,46 S110,86 140,70 S180,22 210,34 S250,80 280,56 S320,18 350,30 S390,64 420,42";
const AREA_PATH = `${LINE_PATH} L420,110 L0,110 Z`;

export function DashboardPreview({ className }: { className?: string }) {
  const { ref, visible } = useReveal<HTMLDivElement>({ threshold: 0.12 });

  const messages = useCountUp(48213, { enabled: visible, duration: 1600, delay: 300 });
  const days = useCountUp(412, { enabled: visible, duration: 1400, delay: 450 });
  const health = useCountUp(84, { enabled: visible, duration: 1800, delay: 600 });

  // Gauge: 84% of a 3/4 circle. r=26 → circumference ≈ 163.4; 3/4 arc ≈ 122.5
  const ARC = 122.5;
  const gaugeOffset = ARC - (ARC * health) / 100;

  return (
    <div ref={ref} className={cn("relative", className)}>
      {/* Orbiting chips */}
      <FloatingChip
        className="-left-10 -top-3 lp-float"
        style={{ animationDelay: "0s" }}
        icon={<Heart className="h-3 w-3 text-chart-5" />}
        label="Words of affirmation · 62%"
      />
      <FloatingChip
        className="-right-14 top-[38%] lp-float"
        style={{ animationDelay: "1.4s" }}
        icon={<span className="text-sm leading-none">😂</span>}
        label="3,104 uses"
      />
      <FloatingChip
        className="-left-12 bottom-24 lp-float-x"
        style={{ animationDelay: "0.6s" }}
        icon={<Activity className="h-3 w-3 text-warning" />}
        label="Conflict resolved in 2h 14m"
      />
      <FloatingChip
        className="-right-10 -bottom-4 lp-float"
        style={{ animationDelay: "2.1s" }}
        icon={<Sparkles className="h-3 w-3 text-primary" />}
        label="Happiest week · Jun 9"
      />

      <TiltCard
        max={6}
        className="surface-card lp-gradient-border relative overflow-hidden rounded-2xl p-0 shadow-glow"
      >
        {/* Scan beam */}
        <div
          aria-hidden
          className="lp-scan pointer-events-none absolute inset-x-0 top-0 z-20 h-24 bg-gradient-to-b from-transparent via-primary/10 to-transparent"
        />

        {/* Header strip */}
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-gradient-to-br from-primary to-accent text-primary-foreground">
              <MessageSquareText className="h-3 w-3" />
            </span>
            <div className="leading-tight">
              <p className="text-xs font-semibold text-foreground">Sam &amp; Priya</p>
              <p className="text-[10px] text-muted-foreground">Mar 2024 → May 2025 · WhatsApp</p>
            </div>
          </div>
          <span className="relative inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-[10px] font-medium text-success">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
            </span>
            analyzed
          </span>
        </div>

        <div className="grid gap-3 p-4 sm:grid-cols-[1fr_120px]">
          {/* Left column */}
          <div className="space-y-3">
            {/* Metrics */}
            <div className="grid grid-cols-3 gap-2">
              <Metric label="Messages" value={messages.toLocaleString()} />
              <Metric label="Days" value={days.toLocaleString()} />
              <Metric label="Reply time" value="4m 12s" />
            </div>

            {/* Sentiment chart */}
            <div className="rounded-lg border border-border bg-background/60 p-3">
              <div className="mb-1 flex items-center justify-between">
                <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                  Sentiment · weekly
                </p>
                <span className="inline-flex items-center gap-1 text-[10px] text-success">
                  <TrendingUp className="h-3 w-3" /> +12%
                </span>
              </div>
              <svg viewBox="0 0 420 110" className="h-24 w-full overflow-visible">
                <defs>
                  <linearGradient id="lp-area" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.28" />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0" />
                  </linearGradient>
                  <linearGradient id="lp-line" x1="0" x2="1" y1="0" y2="0">
                    <stop offset="0%" stopColor="hsl(var(--primary))" />
                    <stop offset="100%" stopColor="hsl(var(--accent))" />
                  </linearGradient>
                </defs>
                {[22, 44, 66, 88].map((y) => (
                  <line
                    key={y}
                    x1="0"
                    x2="420"
                    y1={y}
                    y2={y}
                    stroke="hsl(var(--border))"
                    strokeDasharray="2 4"
                  />
                ))}
                <path
                  d={AREA_PATH}
                  fill="url(#lp-area)"
                  className={cn(
                    "transition-opacity duration-1000",
                    visible ? "opacity-100" : "opacity-0",
                  )}
                  style={{ transitionDelay: "1400ms" }}
                />
                {visible && (
                  <path
                    d={LINE_PATH}
                    fill="none"
                    stroke="url(#lp-line)"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    className="lp-draw"
                    style={{ ["--lp-len" as string]: 560, ["--lp-delay" as string]: "400ms" }}
                  />
                )}
                {visible &&
                  [
                    { x: 210, y: 34, d: 1700 },
                    { x: 350, y: 30, d: 2300 },
                  ].map((p) => (
                    <g key={p.x} className="lp-pop" style={{ animationDelay: `${p.d}ms` }}>
                      <circle cx={p.x} cy={p.y} r="8" fill="hsl(var(--accent) / 0.18)" />
                      <circle cx={p.x} cy={p.y} r="3.5" fill="hsl(var(--accent))" />
                    </g>
                  ))}
              </svg>
            </div>

            {/* Emotion equaliser */}
            <div className="rounded-lg border border-border bg-background/60 p-3">
              <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                Emotion mix
              </p>
              <div className="flex h-12 items-end gap-1">
                {EMOTIONS.map((e, i) => (
                  <div key={e.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1">
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className="lp-wave w-full rounded-sm"
                        style={{
                          height: `${e.h}%`,
                          background: e.color,
                          ["--lp-delay" as string]: `${i * 140}ms`,
                        }}
                      />
                    </div>
                    <span className="text-[9px] leading-none text-muted-foreground">{e.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Right column: health gauge + love languages */}
          <div className="flex flex-col gap-3">
            <div className="flex flex-col items-center rounded-lg border border-border bg-background/60 p-3">
              <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                Health
              </p>
              <div className="relative mt-1 h-20 w-20">
              <svg viewBox="0 0 64 64" className="h-20 w-20 -rotate-[135deg]">
                <circle
                  cx="32"
                  cy="32"
                  r="26"
                  fill="none"
                  stroke="hsl(var(--muted))"
                  strokeWidth="6"
                  strokeDasharray={`${ARC} 999`}
                  strokeLinecap="round"
                />
                <circle
                  cx="32"
                  cy="32"
                  r="26"
                  fill="none"
                  stroke="url(#lp-line)"
                  strokeWidth="6"
                  strokeDasharray={`${ARC} 999`}
                  strokeDashoffset={gaugeOffset}
                  strokeLinecap="round"
                  style={{ transition: "stroke-dashoffset 120ms linear" }}
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
                <p className="font-display text-xl font-bold tabular-nums text-foreground">{health}</p>
                <p className="mt-0.5 text-[9px] text-muted-foreground">/ 100</p>
              </div>
              </div>
            </div>

            <div className="rounded-lg border border-border bg-background/60 p-3">
              <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
                Love languages
              </p>
              <div className="space-y-1.5">
                {LOVE.map((l, i) => (
                  <div key={l.label}>
                    <div className="flex justify-between text-[9px] text-muted-foreground">
                      <span>{l.label}</span>
                      <span>{l.v}%</span>
                    </div>
                    <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-muted">
                      {visible && (
                        <div
                          className="lp-grow-x h-full rounded-full bg-gradient-to-r from-primary to-accent"
                          style={{ width: `${l.v}%`, ["--lp-delay" as string]: `${800 + i * 160}ms` }}
                        />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </TiltCard>
    </div>
  );
}

const EMOTIONS = [
  { label: "joy", h: 90, color: "hsl(var(--chart-1))" },
  { label: "love", h: 70, color: "hsl(var(--chart-5))" },
  { label: "calm", h: 55, color: "hsl(var(--chart-3))" },
  { label: "surprise", h: 40, color: "hsl(var(--chart-4))" },
  { label: "sad", h: 28, color: "hsl(var(--chart-2))" },
  { label: "anger", h: 18, color: "hsl(var(--destructive))" },
];

const LOVE = [
  { label: "Affirmation", v: 62 },
  { label: "Quality time", v: 48 },
  { label: "Acts", v: 31 },
];

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-background/60 px-2.5 py-2">
      <p className="text-[9px] uppercase tracking-[0.16em] text-muted-foreground">{label}</p>
      <p className="mt-0.5 font-display text-base font-bold tabular-nums text-foreground">
        {value}
      </p>
    </div>
  );
}

function FloatingChip({
  icon,
  label,
  className,
  style,
}: {
  icon: React.ReactNode;
  label: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={style}
      className={cn(
        "absolute z-30 hidden items-center gap-1.5 rounded-full border border-border bg-card/90 px-2.5 py-1 text-[11px] font-medium text-foreground shadow-soft backdrop-blur md:flex",
        className,
      )}
    >
      {icon}
      {label}
    </div>
  );
}
