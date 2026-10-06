"use client";

/**
 * Languages — shows the Layer-4 normalization pipeline: a two-row ticker of
 * detected scripts plus an animated "translate" card where a romanized
 * Malayalam message resolves into English.
 */

import { useEffect, useState } from "react";
import { ArrowRight, Languages as LanguagesIcon } from "lucide-react";

import { useReveal } from "@/lib/use-reveal";
import { cn } from "@/lib/utils";

import { Reveal } from "./Reveal";
import { SectionHeading } from "./SectionHeading";

const SCRIPTS = [
  "Manglish", "Hinglish", "Tanglish", "Tenglish", "Bengali Roman", "Arabic",
  "Devanagari", "Tamil", "Telugu", "Kannada", "Bengali", "Han", "Hangul",
  "Cyrillic", "French", "Spanish", "German", "Code-switched",
];

const SAMPLES = [
  {
    from: "Nee evide? njan wait cheyyunnu 😅",
    lang: "Manglish",
    to: "Where are you? I'm waiting 😅",
  },
  {
    from: "Kal milte hain, pakka promise",
    lang: "Hinglish",
    to: "Let's meet tomorrow, I promise",
  },
  {
    from: "Enakku unna romba miss pannuren",
    lang: "Tanglish",
    to: "I miss you so much",
  },
];

export function Languages() {
  const { ref, visible } = useReveal<HTMLDivElement>({ threshold: 0.3 });
  const [i, setI] = useState(0);
  const [phase, setPhase] = useState<"in" | "translating" | "done">("in");

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    const t1 = setTimeout(() => !cancelled && setPhase("translating"), 900);
    const t2 = setTimeout(() => !cancelled && setPhase("done"), 2000);
    const t3 = setTimeout(() => {
      if (cancelled) return;
      setPhase("in");
      setI((v) => (v + 1) % SAMPLES.length);
    }, 4600);
    return () => {
      cancelled = true;
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
  }, [visible, i]);

  const sample = SAMPLES[i] ?? SAMPLES[0]!;

  return (
    <section id="languages" className="relative scroll-mt-20 overflow-hidden bg-card/40 py-24">
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="lp-orb absolute -right-40 top-0 h-[420px] w-[420px] rounded-full bg-accent/10 blur-3xl" />
      </div>

      <div className="mx-auto w-full max-w-6xl px-5 sm:px-6">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading
              align="left"
              eyebrow="Any language, any script"
              title="Chats don't happen in one language. Neither does the analysis."
              copy="A detection and translation layer reads 18+ scripts and romanized blends, keeps the original, and stores an English twin every downstream module can use."
            />

            <div className="mt-8 space-y-3" aria-hidden>
              {[0, 1].map((row) => (
                <div
                  key={row}
                  className="overflow-hidden"
                  style={{
                    maskImage: "linear-gradient(90deg, transparent, black 10%, black 90%, transparent)",
                    WebkitMaskImage:
                      "linear-gradient(90deg, transparent, black 10%, black 90%, transparent)",
                  }}
                >
                  <div
                    className={cn("lp-marquee gap-2", row === 1 && "[animation-direction:reverse]")}
                    style={{ ["--lp-duration" as string]: row === 0 ? "40s" : "46s" }}
                  >
                    {[...SCRIPTS, ...SCRIPTS].map((s, k) => (
                      <span
                        key={s + k}
                        className="shrink-0 rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <Reveal from="right">
            <div ref={ref} className="surface-card lp-gradient-border relative rounded-2xl p-6">
              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                <LanguagesIcon className="h-3.5 w-3.5 text-primary" />
                Layer 4 · normalization
              </div>

              <div className="mt-5 space-y-4">
                {/* Incoming bubble */}
                <div key={`in-${i}`} className="lp-pop flex items-end gap-2">
                  <span className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-chart-4 to-chart-5" />
                  <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-border bg-background px-4 py-2.5 text-sm text-foreground shadow-soft">
                    {sample.from}
                  </div>
                </div>

                {/* Detection + translation status */}
                <div className="flex items-center gap-2 pl-9 text-xs text-muted-foreground">
                  <span
                    className={cn(
                      "rounded-full border border-border bg-card px-2 py-0.5 font-medium transition-colors duration-500",
                      phase !== "in" && "border-primary/40 text-primary",
                    )}
                  >
                    {sample.lang}
                  </span>
                  <ArrowRight
                    className={cn(
                      "h-3.5 w-3.5 transition-opacity duration-500",
                      phase === "in" ? "opacity-20" : "opacity-100",
                    )}
                  />
                  <span
                    className={cn(
                      "rounded-full border border-border bg-card px-2 py-0.5 font-medium transition-colors duration-500",
                      phase === "done" && "border-success/40 text-success",
                    )}
                  >
                    English
                  </span>
                  {phase === "translating" && (
                    <span className="ml-1 inline-flex gap-0.5">
                      {[0, 1, 2].map((d) => (
                        <span
                          key={d}
                          className="h-1 w-1 animate-bounce rounded-full bg-primary"
                          style={{ animationDelay: `${d * 120}ms` }}
                        />
                      ))}
                    </span>
                  )}
                </div>

                {/* Translated bubble */}
                <div
                  className={cn(
                    "flex items-end justify-end gap-2 transition-all duration-700",
                    phase === "done" ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0",
                  )}
                >
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-primary to-accent px-4 py-2.5 text-sm text-primary-foreground shadow-glow">
                    {sample.to}
                  </div>
                </div>
              </div>

              <div className="mt-6 grid grid-cols-3 gap-2 border-t border-border pt-4 text-center">
                {[
                  ["18+", "scripts"],
                  ["30d", "translation cache"],
                  ["2×", "forms stored"],
                ].map(([v, l]) => (
                  <div key={l}>
                    <p className="font-display text-lg font-bold text-foreground">{v}</p>
                    <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">{l}</p>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
