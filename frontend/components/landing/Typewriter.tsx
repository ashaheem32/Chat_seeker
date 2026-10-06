"use client";

/**
 * Typewriter — cycles through phrases with a type / hold / erase rhythm.
 *
 * Used in the hero to show the kind of questions you can ask the search
 * module. Honors reduced-motion by rendering the first phrase statically.
 */

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

interface TypewriterProps {
  phrases: string[];
  className?: string;
  typeMs?: number;
  eraseMs?: number;
  holdMs?: number;
}

export function Typewriter({
  phrases,
  className,
  typeMs = 48,
  eraseMs = 22,
  holdMs = 1600,
}: TypewriterProps) {
  const [index, setIndex] = useState(0);
  const [text, setText] = useState("");
  const [phase, setPhase] = useState<"typing" | "holding" | "erasing">("typing");
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (mq?.matches) setReduced(true);
  }, []);

  useEffect(() => {
    if (reduced || phrases.length === 0) return;
    const full = phrases[index % phrases.length] ?? "";
    let t: ReturnType<typeof setTimeout>;

    if (phase === "typing") {
      if (text.length < full.length) {
        t = setTimeout(() => setText(full.slice(0, text.length + 1)), typeMs);
      } else {
        t = setTimeout(() => setPhase("holding"), 0);
      }
    } else if (phase === "holding") {
      t = setTimeout(() => setPhase("erasing"), holdMs);
    } else {
      if (text.length > 0) {
        t = setTimeout(() => setText(full.slice(0, text.length - 1)), eraseMs);
      } else {
        t = setTimeout(() => {
          setIndex((i) => (i + 1) % phrases.length);
          setPhase("typing");
        }, 180);
      }
    }
    return () => clearTimeout(t);
  }, [text, phase, index, phrases, typeMs, eraseMs, holdMs, reduced]);

  if (reduced) {
    return <span className={className}>{phrases[0] ?? ""}</span>;
  }

  return (
    <span className={cn("lp-cursor", className)} aria-live="polite">
      {text}
    </span>
  );
}
