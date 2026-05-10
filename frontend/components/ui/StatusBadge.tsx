"use client";

/**
 * StatusBadge — animated pill for processing-pipeline states.
 *
 * Maps each backend ProcessingStage (plus a couple UI-only states) to:
 *   - an icon + animation appropriate to the state
 *   - a copy-friendly label
 *   - a color tint
 *
 * Compact mode is used inside dense rows (table cells, list items) and
 * drops the text label, leaving just the dot + animation.
 */

import { Check, Loader2, X } from "lucide-react";

import { cn } from "@/lib/utils";

export type StatusKind =
  | "pending"
  | "queued"
  | "processing"
  | "parsing"
  | "persisting"
  | "nlp_processing"
  | "embedding"
  | "ready"
  | "done"
  | "failed";

interface StatusBadgeProps {
  status: StatusKind;
  /** Override the auto-generated label. */
  label?: string;
  compact?: boolean;
  className?: string;
}

interface StyleSpec {
  label: string;
  bg: string;
  fg: string;
  border: string;
  dot: string;
  /** Lucide icon component or null when we use just the dot. */
  icon: typeof Check | typeof X | typeof Loader2 | null;
  /** Optional extra animation class on the dot itself. */
  dotAnim?: string;
  /** Should the icon spin? */
  spin?: boolean;
}

/**
 * Style atlas. Grouping done/ready and pending/queued under shared specs
 * keeps the visual language consistent across UCJ states and the older
 * status names that other endpoints might still use.
 */
const STYLES: Record<StatusKind, StyleSpec> = {
  pending: {
    label: "Pending",
    bg: "bg-muted/50",
    fg: "text-muted-foreground",
    border: "border-border",
    dot: "bg-muted-foreground",
    icon: null,
    dotAnim: "animate-pulse-soft",
  },
  queued: {
    label: "Queued",
    bg: "bg-muted/50",
    fg: "text-muted-foreground",
    border: "border-border",
    dot: "bg-muted-foreground",
    icon: null,
    dotAnim: "animate-pulse-soft",
  },
  processing: {
    label: "Processing",
    bg: "bg-primary/10",
    fg: "text-primary",
    border: "border-primary/30",
    dot: "bg-primary",
    icon: Loader2,
    spin: true,
  },
  parsing: {
    label: "Parsing",
    bg: "bg-primary/10",
    fg: "text-primary",
    border: "border-primary/30",
    dot: "bg-primary",
    icon: Loader2,
    spin: true,
  },
  persisting: {
    label: "Saving",
    bg: "bg-primary/10",
    fg: "text-primary",
    border: "border-primary/30",
    dot: "bg-primary",
    icon: Loader2,
    spin: true,
  },
  nlp_processing: {
    label: "Analyzing",
    bg: "bg-accent/10",
    fg: "text-accent",
    border: "border-accent/30",
    dot: "bg-accent",
    icon: Loader2,
    spin: true,
  },
  embedding: {
    label: "Indexing",
    bg: "bg-accent/10",
    fg: "text-accent",
    border: "border-accent/30",
    dot: "bg-accent",
    icon: Loader2,
    spin: true,
  },
  ready: {
    label: "Ready",
    bg: "bg-success/10",
    fg: "text-success",
    border: "border-success/30",
    dot: "bg-success",
    icon: Check,
  },
  done: {
    label: "Done",
    bg: "bg-success/10",
    fg: "text-success",
    border: "border-success/30",
    dot: "bg-success",
    icon: Check,
  },
  failed: {
    label: "Failed",
    bg: "bg-destructive/10",
    fg: "text-destructive",
    border: "border-destructive/30",
    dot: "bg-destructive",
    icon: X,
  },
};

export function StatusBadge({
  status,
  label,
  compact = false,
  className,
}: StatusBadgeProps) {
  const style = STYLES[status];
  const Icon = style.icon;
  const text = label ?? style.label;

  if (compact) {
    return (
      <span
        className={cn(
          "relative inline-flex h-2 w-2 shrink-0 rounded-full",
          style.dot,
          style.dotAnim,
          className,
        )}
        aria-label={text}
      >
        {/* Outward ring for active states — gives the "this is alive" cue. */}
        {style.spin ? (
          <span
            aria-hidden
            className={cn(
              "absolute inset-0 rounded-full",
              style.dot,
              "animate-ring-pulse opacity-70",
            )}
          />
        ) : null}
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1",
        "text-[11px] font-medium uppercase tracking-wider",
        style.bg,
        style.fg,
        style.border,
        className,
      )}
      role="status"
      aria-live={style.spin ? "polite" : "off"}
    >
      {Icon ? (
        <Icon
          className={cn("h-3 w-3", style.spin && "animate-spin")}
          aria-hidden
        />
      ) : (
        <span
          aria-hidden
          className={cn("h-1.5 w-1.5 rounded-full", style.dot, style.dotAnim)}
        />
      )}
      {text}
    </span>
  );
}
