/**
 * Shared utility helpers.
 *
 * `cn` is the Shadcn convention for merging Tailwind classes - clsx handles
 * conditional logic, twMerge resolves Tailwind conflicts (e.g. "p-2 p-4" -> "p-4").
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format an ISO timestamp as a short local-time string for chart axes. */
export function formatShortDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Format a number with thousands separators. Locale-aware. */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat().format(n);
}

/** Clamp a value to [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
