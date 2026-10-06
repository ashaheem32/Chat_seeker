"use client";

/**
 * Reveal — scroll-triggered entrance wrapper.
 *
 * Renders a div with the `.lp-reveal` class (see globals.css) and adds
 * `.is-visible` once it enters the viewport. `delay` staggers siblings,
 * `from` picks the entrance direction.
 */

import type { CSSProperties, ElementType, ReactNode } from "react";

import { useReveal } from "@/lib/use-reveal";
import { cn } from "@/lib/utils";

interface RevealProps {
  children: ReactNode;
  className?: string;
  /** Stagger delay in ms. */
  delay?: number;
  from?: "up" | "left" | "right" | "scale";
  as?: ElementType;
  style?: CSSProperties;
}

export function Reveal({
  children,
  className,
  delay = 0,
  from = "up",
  as: Tag = "div",
  style,
}: RevealProps) {
  const { ref, visible } = useReveal<HTMLDivElement>();
  return (
    <Tag
      ref={ref}
      data-from={from}
      className={cn("lp-reveal", visible && "is-visible", className)}
      style={{ ...style, ["--lp-delay" as string]: `${delay}ms` }}
    >
      {children}
    </Tag>
  );
}
