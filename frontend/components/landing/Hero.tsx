"use client";

/**
 * Hero — full-viewport opener.
 *
 *   - ParticleField canvas + three drifting gradient orbs behind everything
 *   - Eyebrow pill, word-by-word animated headline, typewriter subline
 *   - Primary CTA → /upload, secondary → #how
 *   - Animated DashboardPreview on the right (stacks below on mobile)
 *   - Scroll cue at the bottom
 */

import Link from "next/link";
import { ArrowRight, ChevronDown, Play, ShieldCheck, Sparkles } from "lucide-react";

import { DashboardPreview } from "./DashboardPreview";
import { ParticleField } from "./ParticleField";
import { Typewriter } from "./Typewriter";

const HEADLINE = ["Understand", "every", "conversation", "you've", "ever", "had."];

const QUESTIONS = [
  "How did we usually apologize?",
  "Which weekends were the happiest?",
  "Who texts first after a fight?",
  "When did we start saying 'love you'?",
  "What do we argue about most?",
];

export function Hero() {
  return (
    <section className="relative isolate overflow-hidden pt-28 sm:pt-36">
      {/* Background layers */}
      <ParticleField className="absolute inset-0 -z-20 h-full w-full" />
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div className="lp-orb absolute -left-40 top-10 h-[480px] w-[480px] rounded-full bg-primary/15 blur-3xl" />
        <div
          className="lp-orb absolute -right-32 top-32 h-[420px] w-[420px] rounded-full bg-accent/15 blur-3xl"
          style={{ animationDelay: "-6s" }}
        />
        <div
          className="lp-orb absolute bottom-0 left-1/3 h-[360px] w-[360px] rounded-full bg-chart-3/10 blur-3xl"
          style={{ animationDelay: "-12s" }}
        />
        <div
          className="lp-grid-bg absolute inset-0 opacity-40"
          style={{
            maskImage: "radial-gradient(70% 60% at 50% 20%, black, transparent 85%)",
            WebkitMaskImage: "radial-gradient(70% 60% at 50% 20%, black, transparent 85%)",
          }}
        />
      </div>

      <div className="mx-auto grid w-full max-w-6xl items-center gap-14 px-5 pb-20 sm:px-6 lg:grid-cols-[1.05fr_1fr] lg:gap-10 lg:pb-28">
        {/* Copy */}
        <div className="text-center lg:text-left">
          <div
            className="lp-word mx-auto inline-flex items-center gap-2 rounded-full border border-border bg-card/70 px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-muted-foreground backdrop-blur lg:mx-0"
            style={{ ["--lp-delay" as string]: "0ms" }}
          >
            <Sparkles className="h-3 w-3 text-primary" />
            Personal intelligence for your chats
          </div>

          <h1 className="mt-6 font-display text-5xl font-bold leading-[1.02] tracking-tight sm:text-6xl lg:text-[4.4rem]">
            {HEADLINE.map((word, i) => (
              <span
                key={word + i}
                className={
                  "lp-word mr-[0.22em] " +
                  (i < 1 ? "gradient-text" : "text-foreground")
                }
                style={{ ["--lp-delay" as string]: `${120 + i * 90}ms` }}
              >
                {word}
              </span>
            ))}
          </h1>

          <p
            className="lp-word mx-auto mt-6 max-w-xl text-balance text-base leading-relaxed text-muted-foreground sm:text-lg lg:mx-0"
            style={{ ["--lp-delay" as string]: "760ms" }}
          >
            Drop in a WhatsApp, Telegram or Instagram export. ChatLens maps the
            emotions, conflicts, love languages and health of the relationship
            behind it, then lets you ask it anything.
          </p>

          <div
            className="lp-word mx-auto mt-5 inline-flex max-w-full items-center gap-2 rounded-xl border border-border bg-card/70 px-4 py-2.5 text-left text-sm shadow-soft backdrop-blur lg:mx-0"
            style={{ ["--lp-delay" as string]: "900ms" }}
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Sparkles className="h-3.5 w-3.5" />
            </span>
            <span className="truncate text-foreground/90">
              <span className="text-muted-foreground">Ask: </span>
              <Typewriter phrases={QUESTIONS} className="font-medium" />
            </span>
          </div>

          <div
            className="lp-word mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center lg:justify-start"
            style={{ ["--lp-delay" as string]: "1040ms" }}
          >
            <Link
              href="/upload"
              className="lp-sheen lp-pulse-border group inline-flex h-12 items-center gap-2 rounded-full bg-primary px-6 text-sm font-semibold text-primary-foreground shadow-glow transition hover:bg-primary/90"
            >
              Analyze a chat
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </Link>
            <a
              href="#how"
              className="group inline-flex h-12 items-center gap-2 rounded-full border border-border bg-card/70 px-6 text-sm font-medium text-foreground backdrop-blur transition hover:border-primary/40 hover:text-primary"
            >
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary transition group-hover:bg-primary group-hover:text-primary-foreground">
                <Play className="h-3 w-3 fill-current" />
              </span>
              See how it works
            </a>
          </div>

          <p
            className="lp-word mt-5 inline-flex items-center gap-1.5 text-xs text-muted-foreground"
            style={{ ["--lp-delay" as string]: "1180ms" }}
          >
            <ShieldCheck className="h-3.5 w-3.5 text-success" />
            Runs on your own stack. Nothing is shared, nothing is sold.
          </p>
        </div>

        {/* Visual */}
        <div
          className="lp-word relative mx-auto w-full max-w-xl lg:max-w-none"
          style={{ ["--lp-delay" as string]: "500ms" }}
        >
          <DashboardPreview />
        </div>
      </div>

      {/* Scroll cue */}
      <a
        href="#platforms"
        aria-label="Scroll to content"
        className="lp-word absolute bottom-5 left-1/2 hidden -translate-x-1/2 flex-col items-center gap-1 text-[10px] uppercase tracking-[0.2em] text-muted-foreground transition hover:text-primary md:flex"
        style={{ ["--lp-delay" as string]: "1600ms" }}
      >
        scroll
        <ChevronDown className="h-4 w-4 animate-bounce" />
      </a>
    </section>
  );
}
