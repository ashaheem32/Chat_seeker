"use client";

/**
 * Features — bento grid of the seven dashboard modules. Each tile is a
 * TiltCard with a cursor spotlight; the first two are double-width and
 * carry a tiny animated illustration.
 */

import {
  Activity,
  AlertTriangle,
  BarChart2,
  Heart,
  LayoutDashboard,
  Search,
  TrendingUp,
} from "lucide-react";

import { Reveal } from "./Reveal";
import { SectionHeading } from "./SectionHeading";
import { TiltCard } from "./TiltCard";

const MODULES = [
  {
    icon: LayoutDashboard,
    title: "Stats overview",
    copy: "Hero metrics, response-time gauge, streaks, who-texts-first, relative airtime and six fun-fact tiles.",
    span: "md:col-span-2",
    illo: "bars",
  },
  {
    icon: Activity,
    title: "Emotion timeline",
    copy: "Sentiment by day, week or month with annotated peaks, per-person emotion donuts and a mood calendar.",
    span: "md:col-span-2",
    illo: "line",
  },
  {
    icon: BarChart2,
    title: "Words & emojis",
    copy: "Word cloud, top-20 words, emoji sentiment, vocabulary richness and the phrases that only you two use.",
    span: "",
    illo: null,
  },
  {
    icon: Search,
    title: "Ask anything",
    copy: "Streaming natural-language search over every message, with cited sources and a context drawer.",
    span: "",
    illo: null,
  },
  {
    icon: AlertTriangle,
    title: "Conflict analysis",
    copy: "Difficult-moment windows, clustered themes, recovery time and the hours when things tend to go wrong.",
    span: "",
    illo: null,
  },
  {
    icon: Heart,
    title: "Love languages",
    copy: "Five-axis radar per participant, side-by-side comparison and a written compatibility insight.",
    span: "",
    illo: null,
  },
  {
    icon: TrendingUp,
    title: "Health score",
    copy: "A 0–100 composite across seven weighted factors, with narrated reasons and what moved it.",
    span: "md:col-span-2",
    illo: "gauge",
  },
];

export function Features() {
  return (
    <section
      id="features"
      className="relative mx-auto w-full max-w-6xl scroll-mt-20 px-5 py-24 sm:px-6"
    >
      <SectionHeading
        eyebrow="Seven modules"
        title="Everything the chat was trying to tell you"
        copy="Each module is a lens on the same conversation. Together they add up to a picture of how two people actually talk."
      />

      <div className="mt-14 grid gap-5 md:grid-cols-4">
        {MODULES.map((m, i) => {
          const Icon = m.icon;
          return (
            <Reveal key={m.title} delay={(i % 4) * 110} className={m.span}>
              <TiltCard max={5} className="surface-card group h-full rounded-2xl p-6 transition-colors hover:border-primary/40">
                <div className="relative z-10 flex h-full flex-col">
                  <div className="flex items-center justify-between">
                    <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary transition group-hover:bg-primary group-hover:text-primary-foreground">
                      <Icon className="h-5 w-5" />
                    </span>
                    {m.illo && <Illustration kind={m.illo} />}
                  </div>
                  <h3 className="mt-5 font-display text-lg font-semibold text-foreground">
                    {m.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{m.copy}</p>
                </div>
              </TiltCard>
            </Reveal>
          );
        })}
      </div>
    </section>
  );
}

function Illustration({ kind }: { kind: string }) {
  if (kind === "bars") {
    return (
      <div className="flex h-10 items-end gap-1" aria-hidden>
        {[40, 70, 55, 90, 65, 80].map((h, i) => (
          <span
            key={i}
            className="lp-wave w-2 rounded-sm bg-gradient-to-t from-primary to-accent"
            style={{ height: `${h}%`, ["--lp-delay" as string]: `${i * 120}ms` }}
          />
        ))}
      </div>
    );
  }
  if (kind === "line") {
    return (
      <svg viewBox="0 0 120 40" className="h-10 w-28 overflow-visible" aria-hidden>
        <path
          d="M0,30 C15,28 20,10 35,14 S55,34 70,24 S95,4 120,12"
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth="2.5"
          strokeLinecap="round"
          className="lp-draw"
          style={{ ["--lp-len" as string]: 160 }}
        />
        <circle cx="120" cy="12" r="3.5" fill="hsl(var(--accent))" className="lp-pop" style={{ animationDelay: "2.2s" }} />
      </svg>
    );
  }
  if (kind === "gauge") {
    return (
      <div className="relative h-10 w-10" aria-hidden>
        <svg viewBox="0 0 40 40" className="h-10 w-10 -rotate-90">
          <circle cx="20" cy="20" r="16" fill="none" stroke="hsl(var(--muted))" strokeWidth="5" />
          <circle
            cx="20"
            cy="20"
            r="16"
            fill="none"
            stroke="hsl(var(--success))"
            strokeWidth="5"
            strokeLinecap="round"
            className="lp-draw"
            style={{ ["--lp-len" as string]: 100.5, strokeDasharray: "84 100.5" }}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center font-display text-[10px] font-bold text-foreground">
          84
        </span>
      </div>
    );
  }
  return null;
}
