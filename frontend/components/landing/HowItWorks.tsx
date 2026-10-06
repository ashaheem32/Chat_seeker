"use client";

/**
 * HowItWorks — three steps joined by an animated flowing dashed line.
 * Each card reveals in sequence; the line "draws" once the section is seen.
 */

import { Upload, Cpu, LayoutDashboard } from "lucide-react";

import { useReveal } from "@/lib/use-reveal";
import { cn } from "@/lib/utils";

import { Reveal } from "./Reveal";
import { SectionHeading } from "./SectionHeading";

const STEPS = [
  {
    icon: Upload,
    title: "Drop an export",
    copy: "WhatsApp .txt, Telegram or Instagram JSON, Messenger, CSV or raw text. We detect the platform and parse it into one universal format.",
    tag: "01",
  },
  {
    icon: Cpu,
    title: "The pipeline reads it",
    copy: "Language detection and translation, emotion + sentiment classifiers, topic clustering and a pgvector embedding for every single message.",
    tag: "02",
  },
  {
    icon: LayoutDashboard,
    title: "Explore the dashboard",
    copy: "Seven modules, one streaming search box. Timelines, conflicts, love languages and a health score — narrated by Claude.",
    tag: "03",
  },
];

export function HowItWorks() {
  const { ref, visible } = useReveal<HTMLDivElement>({ threshold: 0.25 });

  return (
    <section id="how" className="relative mx-auto w-full max-w-6xl scroll-mt-20 px-5 py-24 sm:px-6">
      <SectionHeading
        eyebrow="How it works"
        title="From export to insight in three steps"
        copy="No setup, no accounts to connect. Upload, wait for the progress bar, and you're in."
      />

      <div ref={ref} className="relative mt-14">
        {/* Connecting line (desktop) */}
        <svg
          aria-hidden
          className="pointer-events-none absolute left-0 right-0 top-12 hidden h-2 w-full md:block"
          viewBox="0 0 1000 8"
          preserveAspectRatio="none"
        >
          <line x1="0" y1="4" x2="1000" y2="4" stroke="hsl(var(--border))" strokeWidth="2" />
          {visible && (
            <line
              x1="0"
              y1="4"
              x2="1000"
              y2="4"
              stroke="hsl(var(--primary))"
              strokeWidth="2"
              className="lp-draw"
              style={{ ["--lp-len" as string]: 1000, ["--lp-delay" as string]: "300ms" }}
            />
          )}
          {visible && (
            <line
              x1="0"
              y1="4"
              x2="1000"
              y2="4"
              stroke="hsl(var(--accent))"
              strokeWidth="2"
              className="lp-dash-flow opacity-60"
            />
          )}
        </svg>

        <div className="grid gap-6 md:grid-cols-3">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            return (
              <Reveal key={s.tag} delay={i * 180} className="relative">
                <div className="surface-card lp-spotlight group relative h-full p-6 transition-colors hover:border-primary/40">
                  <div className="relative z-10">
                    <div className="flex items-center justify-between">
                      <span
                        className={cn(
                          "flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-accent text-primary-foreground shadow-glow transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-105",
                        )}
                      >
                        <Icon className="h-5 w-5" />
                      </span>
                      <span className="font-display text-3xl font-bold text-muted-foreground/30">
                        {s.tag}
                      </span>
                    </div>
                    <h3 className="mt-5 font-display text-lg font-semibold text-foreground">
                      {s.title}
                    </h3>
                    <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.copy}</p>
                  </div>
                </div>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
