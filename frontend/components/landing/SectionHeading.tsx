"use client";

import { Reveal } from "./Reveal";

interface SectionHeadingProps {
  eyebrow: string;
  title: string;
  copy?: string;
  align?: "center" | "left";
}

export function SectionHeading({ eyebrow, title, copy, align = "center" }: SectionHeadingProps) {
  const centered = align === "center";
  return (
    <div className={centered ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}>
      <Reveal>
        <p className="inline-flex items-center gap-2 rounded-full border border-border bg-card/60 px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-primary">
          <span className="h-1.5 w-1.5 rounded-full bg-primary" />
          {eyebrow}
        </p>
      </Reveal>
      <Reveal delay={100}>
        <h2 className="mt-4 font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
          {title}
        </h2>
      </Reveal>
      {copy && (
        <Reveal delay={200}>
          <p className="mt-3 text-balance text-base leading-relaxed text-muted-foreground">
            {copy}
          </p>
        </Reveal>
      )}
    </div>
  );
}
