"use client";

/**
 * LandingNav — sticky top bar that turns frosted once you scroll, with a
 * thin gradient scroll-progress indicator along its bottom edge.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, MessageSquareText } from "lucide-react";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "#features", label: "Features" },
  { href: "#how", label: "How it works" },
  { href: "#languages", label: "Languages" },
  { href: "#privacy", label: "Privacy" },
];

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      const y = window.scrollY;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setScrolled(y > 12);
      setProgress(max > 0 ? Math.min(1, y / max) : 0);
      raf = 0;
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-50 transition-all duration-300",
        scrolled
          ? "border-b border-border/70 bg-background/75 shadow-soft backdrop-blur-xl"
          : "border-b border-transparent bg-transparent",
      )}
    >
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-5 sm:px-6">
        <Link href="/" className="group flex items-center gap-2 text-foreground">
          <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-accent text-primary-foreground shadow-glow transition-transform duration-300 group-hover:rotate-[-8deg] group-hover:scale-105">
            <MessageSquareText className="h-4 w-4" />
          </span>
          <span className="font-display text-lg font-semibold tracking-tight">ChatLens</span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex" aria-label="Landing sections">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="relative rounded-full px-3 py-1.5 text-sm text-muted-foreground transition hover:text-foreground after:absolute after:inset-x-3 after:-bottom-0.5 after:h-px after:origin-left after:scale-x-0 after:bg-primary after:transition-transform after:duration-300 hover:after:scale-x-100"
            >
              {l.label}
            </a>
          ))}
        </nav>

        <Link
          href="/upload"
          className="lp-sheen group inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-glow transition hover:bg-primary/90"
        >
          Open app
          <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>

      {/* Scroll progress */}
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-gradient-to-r from-primary via-accent to-chart-3"
        style={{ transform: `scaleX(${progress})` }}
      />
    </header>
  );
}
