"use client";

/**
 * Privacy — the trust section. A slowly rotating ring of dashes around a
 * lock, with three guarantees that reveal from the side.
 */

import { Database, Lock, ServerOff, Trash2 } from "lucide-react";

import { Reveal } from "./Reveal";
import { SectionHeading } from "./SectionHeading";

const POINTS = [
  {
    icon: Database,
    title: "Your database, your rules",
    copy: "Messages live in a Postgres you run. The only things that leave are the snippets sent to the model for classification and search.",
  },
  {
    icon: ServerOff,
    title: "No accounts, no tracking",
    copy: "There's no sign-up, no analytics pixel and no third-party script on this page. Open the app, upload, done.",
  },
  {
    icon: Trash2,
    title: "Delete in one click",
    copy: "Every dashboard has a delete button that drops the upload, its messages, embeddings and cached analyses together.",
  },
];

export function Privacy() {
  return (
    <section id="privacy" className="relative mx-auto w-full max-w-6xl scroll-mt-20 px-5 py-24 sm:px-6">
      <div className="grid items-center gap-12 lg:grid-cols-[0.9fr_1.1fr]">
        <Reveal from="scale" className="order-2 lg:order-1">
          <div className="relative mx-auto flex h-72 w-72 items-center justify-center">
            <svg
              aria-hidden
              viewBox="0 0 200 200"
              className="lp-spin-slow absolute inset-0 h-full w-full text-primary/40"
            >
              <circle cx="100" cy="100" r="92" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 10" />
              <circle cx="100" cy="100" r="72" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="14 10" opacity="0.6" />
            </svg>
            <svg
              aria-hidden
              viewBox="0 0 200 200"
              className="lp-spin-slow absolute inset-0 h-full w-full text-accent/40 [animation-direction:reverse]"
            >
              <circle cx="100" cy="100" r="52" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 8" />
            </svg>
            <span className="absolute h-28 w-28 rounded-full bg-primary/20 blur-2xl" />
            <span className="lp-float relative flex h-24 w-24 items-center justify-center rounded-3xl bg-gradient-to-br from-primary to-accent text-primary-foreground shadow-glow">
              <Lock className="h-10 w-10" />
              <span className="absolute inset-0 rounded-3xl border border-white/30" />
            </span>
            {["embeddings", "messages", "analysis"].map((label, i) => (
              <span
                key={label}
                className="lp-float absolute rounded-full border border-border bg-card px-2.5 py-1 text-[10px] font-medium text-muted-foreground shadow-soft"
                style={{
                  animationDelay: `${i * 1.1}s`,
                  top: ["12%", "70%", "48%"][i],
                  left: ["62%", "8%", "76%"][i],
                }}
              >
                {label}
              </span>
            ))}
          </div>
        </Reveal>

        <div className="order-1 lg:order-2">
          <SectionHeading
            align="left"
            eyebrow="Private by design"
            title="The most personal data you have deserves the most boring privacy story."
            copy="ChatLens is self-hosted. It ships as a docker-compose with Postgres, Redis and the API, and it never phones home."
          />
          <ul className="mt-8 space-y-4">
            {POINTS.map((p, i) => {
              const Icon = p.icon;
              return (
                <Reveal key={p.title} from="right" delay={i * 140} as="li">
                  <div className="surface-card lp-spotlight flex gap-4 p-5 transition-colors hover:border-primary/40">
                    <span className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="relative z-10">
                      <h3 className="font-display text-base font-semibold text-foreground">{p.title}</h3>
                      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{p.copy}</p>
                    </div>
                  </div>
                </Reveal>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
}
