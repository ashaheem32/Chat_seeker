"use client";

/**
 * Height-reserved placeholder for dynamically-imported dashboard modules.
 *
 * Each lazy module renders this while its chunk is being fetched + compiled.
 * The reserved height keeps the page from reflowing as chunks land, which
 * matters because the overview page renders seven modules vertically and
 * each one would otherwise jump the layout.
 */
export function SectionSkeleton({ height = 360 }: { height?: number }) {
  return (
    <section
      className="space-y-3 animate-pulse-soft"
      aria-busy="true"
      aria-label="Loading section"
    >
      <div className="space-y-1.5">
        <span className="shimmer block h-3 w-24 rounded" />
        <span className="shimmer block h-6 w-64 rounded" />
      </div>
      <div
        className="surface-card overflow-hidden p-5"
        style={{ minHeight: height }}
      >
        <span className="shimmer block h-full w-full rounded-xl" />
      </div>
    </section>
  );
}
