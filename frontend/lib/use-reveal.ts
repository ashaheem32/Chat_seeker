"use client";

/**
 * useReveal — flips `visible` to true once the element scrolls into view.
 *
 * Backed by IntersectionObserver so it costs nothing while the user isn't
 * scrolling. Fires once by default (the reveal shouldn't replay every time
 * a card leaves and re-enters the viewport). Pass `once: false` to toggle.
 */

import { useEffect, useRef, useState } from "react";

export interface UseRevealOptions {
  /** How much of the element must be visible before firing. Default 0.18. */
  threshold?: number;
  /** Root margin — negative bottom margin fires a bit after the top edge. */
  rootMargin?: string;
  /** Fire once and disconnect. Default true. */
  once?: boolean;
}

export function useReveal<T extends HTMLElement = HTMLDivElement>({
  threshold = 0.18,
  rootMargin = "0px 0px -8% 0px",
  once = true,
}: UseRevealOptions = {}) {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    // No observer (very old browsers / some test envs) → just show it.
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            if (once) io.disconnect();
          } else if (!once) {
            setVisible(false);
          }
        }
      },
      { threshold, rootMargin },
    );
    io.observe(node);
    return () => io.disconnect();
  }, [threshold, rootMargin, once]);

  return { ref, visible } as const;
}
