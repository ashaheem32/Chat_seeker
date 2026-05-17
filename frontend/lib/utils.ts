/**
 * Shared utility helpers.
 *
 * `cn` is the Shadcn convention for merging Tailwind classes - clsx handles
 * conditional logic, twMerge resolves Tailwind conflicts (e.g. "p-2 p-4" -> "p-4").
 */

import { clsx, type ClassValue } from "clsx";
import { format, parseISO } from "date-fns";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format an ISO timestamp as a short local-time string for chart axes. */
export function formatShortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Format an ISO 8601 string with date-fns. Returns `fallback` (default "—")
 * when the input is null, empty, or not a valid date — so a bad timestamp
 * from the API or a Recharts re-init never crashes the render tree.
 */
export function safeFormatDate(
  iso: string | null | undefined,
  fmt: string,
  fallback = "—",
): string {
  if (!iso) return fallback;
  try {
    const d = parseISO(iso);
    if (Number.isNaN(d.getTime())) return fallback;
    return format(d, fmt);
  } catch {
    return fallback;
  }
}

/** Format a number with thousands separators. Locale-aware. */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return "0";
  return new Intl.NumberFormat().format(n);
}

/** Clamp a value to [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
