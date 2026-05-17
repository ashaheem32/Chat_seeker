"use client";

/**
 * useCountUp — animate a numeric value from 0 → target on mount or when
 * `target` changes.
 *
 * Implementation notes:
 *   - Drives the animation with `requestAnimationFrame`, not setInterval.
 *     RAF gives us the browser's actual frame cadence (and pauses while
 *     the tab is backgrounded), which is what users expect.
 *   - Uses an ease-out cubic curve so the number decelerates near the
 *     target — feels more polished than a linear ramp.
 *   - Honors `prefers-reduced-motion`. Users with that preference set get
 *     the final value immediately, no animation.
 *   - Caller picks the precision via `decimals`. The hook returns a number;
 *     formatting (commas, %, etc.) is the consumer's job.
 */

import { useEffect, useRef, useState } from "react";

export interface UseCountUpOptions {
  /** Total animation duration in ms. Default 1100. */
  duration?: number;
  /** Number of decimal places to render. Default 0. */
  decimals?: number;
  /** Delay before the animation starts, in ms. Default 0.
   *  Pair with the card's stagger delay so numbers and cards arrive together. */
  delay?: number;
  /** When false, hold at 0 until flipped true. Useful for stagger-then-count. */
  enabled?: boolean;
}

export function useCountUp(
  rawTarget: number,
  { duration = 1100, decimals = 0, delay = 0, enabled = true }: UseCountUpOptions = {},
): number {
  // Bad numeric inputs (NaN from a corrupted API field, Infinity from a
  // divide-by-zero) would otherwise propagate into setValue and render as
  // literal "NaN" in the DOM. Coerce to 0 at the boundary.
  const target = Number.isFinite(rawTarget) ? rawTarget : 0;
  const [value, setValue] = useState<number>(0);
  // Track the last target we animated to so flipping the prop mid-flight
  // restarts the animation cleanly.
  const lastTargetRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!enabled) return;
    // Respect reduced-motion preference.
    if (
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ) {
      setValue(target);
      lastTargetRef.current = target;
      return;
    }

    // Re-entry guard: cancel any in-flight RAF / timeout from a prior render.
    const cancel = () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      if (timeoutRef.current != null) clearTimeout(timeoutRef.current);
      rafRef.current = null;
      timeoutRef.current = null;
    };
    cancel();

    const start = lastTargetRef.current ?? 0;
    const range = target - start;
    if (range === 0) {
      setValue(target);
      lastTargetRef.current = target;
      return cancel;
    }

    const factor = 10 ** decimals;
    const startAnimation = (t0: number) => {
      const tick = (now: number) => {
        const elapsed = now - t0;
        const progress = Math.min(1, elapsed / duration);
        // Cubic ease-out: 1 - (1 - p)^3
        const eased = 1 - Math.pow(1 - progress, 3);
        const next = start + range * eased;
        // Quantize to the requested precision so renders don't churn on
        // every sub-decimal frame.
        setValue(Math.round(next * factor) / factor);
        if (progress < 1) {
          rafRef.current = requestAnimationFrame(tick);
        } else {
          rafRef.current = null;
          lastTargetRef.current = target;
        }
      };
      rafRef.current = requestAnimationFrame(tick);
    };

    if (delay > 0) {
      timeoutRef.current = setTimeout(() => {
        startAnimation(performance.now());
      }, delay);
    } else {
      startAnimation(performance.now());
    }

    return cancel;
  }, [target, duration, decimals, delay, enabled]);

  return value;
}
