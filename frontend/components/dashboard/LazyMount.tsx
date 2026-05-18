"use client";

/**
 * LazyMount — defer rendering (and therefore the dynamic-import fetch) of
 * the children until the wrapper is near the viewport.
 *
 * Combined with `next/dynamic`, this turns "load every module chunk on
 * page mount and execute on the main thread" into "fetch + execute the
 * module only when the user is about to scroll to it." That keeps the
 * initial Total Blocking Time low because only the above-the-fold
 * components contribute to first-paint work.
 *
 * The wrapper renders a height-reserved fallback while waiting so the
 * page doesn't jump as the real module hydrates.
 */

import { useEffect, useRef, useState } from "react";

export function LazyMount({
  children,
  fallback,
  rootMargin = "300px",
}: {
  children: React.ReactNode;
  fallback: React.ReactNode;
  /** How early (in CSS px below the viewport) to start fetching. */
  rootMargin?: string;
}) {
  const [visible, setVisible] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (visible) return;
    const node = ref.current;
    if (!node) return;

    // Fall back to immediate mount when the API isn't available — better
    // than leaving content hidden forever.
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }

    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          obs.disconnect();
        }
      },
      { rootMargin },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [visible, rootMargin]);

  return <div ref={ref}>{visible ? children : fallback}</div>;
}
