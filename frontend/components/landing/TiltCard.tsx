"use client";

/**
 * TiltCard — 3D perspective tilt that follows the cursor, plus a cursor
 * spotlight (via --mx / --my CSS vars consumed by `.lp-spotlight`).
 * Resets smoothly on pointer leave. No-op on touch / reduced motion.
 */

import { useRef, type ReactNode, type PointerEvent } from "react";

import { cn } from "@/lib/utils";

interface TiltCardProps {
  children: ReactNode;
  className?: string;
  /** Max tilt in degrees. Default 8. */
  max?: number;
  /** Also lift the card on hover. */
  lift?: boolean;
}

export function TiltCard({ children, className, max = 8, lift = true }: TiltCardProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el || e.pointerType === "touch") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width;
    const py = (e.clientY - rect.top) / rect.height;
    const rx = (0.5 - py) * max * 2;
    const ry = (px - 0.5) * max * 2;
    el.style.setProperty("--mx", `${px * 100}%`);
    el.style.setProperty("--my", `${py * 100}%`);
    el.style.transform = `perspective(1000px) rotateX(${rx}deg) rotateY(${ry}deg)${
      lift ? " translateY(-4px)" : ""
    }`;
  };

  const onLeave = () => {
    const el = ref.current;
    if (!el) return;
    el.style.transform = "";
  };

  return (
    <div
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      className={cn("lp-tilt lp-spotlight", className)}
    >
      {children}
    </div>
  );
}
