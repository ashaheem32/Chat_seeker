"use client";

/**
 * FinalCta — the closing panel. Gradient surface with drifting orbs and a
 * big pulsing CTA into the upload flow.
 */

import Link from "next/link";
import { ArrowRight, MessageSquareText } from "lucide-react";

import { Reveal } from "./Reveal";

export function FinalCta() {
  return (
    <section className="mx-auto w-full max-w-6xl px-5 pb-24 sm:px-6">
      <Reveal from="scale">
        <div className="relative overflow-hidden rounded-3xl border border-primary/20 bg-gradient-to-br from-primary via-primary-glow to-accent p-10 text-center text-primary-foreground shadow-glow sm:p-16">
          <div aria-hidden className="pointer-events-none absolute inset-0">
            <div className="lp-orb absolute -left-20 -top-20 h-72 w-72 rounded-full bg-white/15 blur-3xl" />
            <div
              className="lp-orb absolute -bottom-24 -right-16 h-80 w-80 rounded-full bg-white/10 blur-3xl"
              style={{ animationDelay: "-9s" }}
            />
            <div
              className="absolute inset-0 opacity-[0.08]"
              style={{
                backgroundImage: "radial-gradient(white 1px, transparent 1px)",
                backgroundSize: "22px 22px",
              }}
            />
          </div>

          <div className="relative z-10">
            <span className="lp-float mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white/15 backdrop-blur">
              <MessageSquareText className="h-6 w-6" />
            </span>
            <h2 className="mt-6 font-display text-3xl font-bold tracking-tight sm:text-5xl">
              Ready to read between the lines?
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-balance text-base text-white/80 sm:text-lg">
              Export a chat, drop it in, and give the pipeline a minute. The dashboard will be
              waiting.
            </p>
            <Link
              href="/upload"
              className="lp-sheen group mt-8 inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-sm font-semibold text-primary shadow-lg transition hover:bg-white/90"
            >
              Open ChatLens
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </Link>
            <p className="mt-4 text-xs text-white/70">.txt · .json · .csv · .zip — up to 50 MB</p>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
