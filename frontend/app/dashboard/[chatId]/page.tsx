"use client";

/**
 * Overview page.
 *
 * Composition:
 *   - <StatsOverview /> at the top — above-the-fold, no Recharts, kept
 *     eager so the first paint includes real content.
 *   - Six heavy modules below — code-split via next/dynamic AND wrapped
 *     in <LazyMount/>. The dynamic chunk is only fetched once the
 *     wrapper is near the viewport (300px below); until then we render
 *     a <SectionSkeleton/> placeholder that reserves the right height.
 *     This keeps initial Total Blocking Time low because only
 *     StatsOverview's JS executes at first paint — every other module
 *     waits for the user to scroll near it.
 *
 * Page-level concern is just the "is this chat ready for the dashboard?"
 * gate. ReadyGate polls /upload/{id} once for the whole route — the per-
 * module sub-routes share the same gate so polling isn't duplicated across
 * the layout + each module page.
 */

import dynamic from "next/dynamic";

import { LazyMount } from "@/components/dashboard/LazyMount";
import { ReadyGate } from "@/components/dashboard/ReadyGate";
import { SectionSkeleton } from "@/components/dashboard/SectionSkeleton";
import { StatsOverview } from "@/components/dashboard/StatsOverview";

// `next/dynamic` with named exports needs the `.then(m => ({ default: ... }))`
// re-export shim. `ssr: false` because every module is already `"use client"`
// and Recharts needs `window` — skipping SSR avoids a useless server render
// and shrinks the server-rendered HTML the browser has to hydrate.
const EmotionTimeline = dynamic(
  () =>
    import("@/components/dashboard/EmotionTimeline").then((m) => ({
      default: m.EmotionTimeline,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={620} /> },
);

const WordAnalytics = dynamic(
  () =>
    import("@/components/dashboard/WordAnalytics").then((m) => ({
      default: m.WordAnalytics,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={620} /> },
);

const SearchModule = dynamic(
  () =>
    import("@/components/dashboard/SearchModule").then((m) => ({
      default: m.SearchModule,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={320} /> },
);

const ConflictAnalysis = dynamic(
  () =>
    import("@/components/dashboard/ConflictAnalysis").then((m) => ({
      default: m.ConflictAnalysis,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={620} /> },
);

const LoveLanguages = dynamic(
  () =>
    import("@/components/dashboard/LoveLanguages").then((m) => ({
      default: m.LoveLanguages,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={520} /> },
);

const HealthScore = dynamic(
  () =>
    import("@/components/dashboard/HealthScore").then((m) => ({
      default: m.HealthScore,
    })),
  { ssr: false, loading: () => <SectionSkeleton height={420} /> },
);

export default function OverviewPage({
  params,
}: {
  params: { chatId: string };
}) {
  const id = params.chatId;
  return (
    <ReadyGate chatId={id}>
      <div className="space-y-10 animate-fade-up">
        <StatsOverview uploadId={id} />

        <LazyMount fallback={<SectionSkeleton height={620} />}>
          <EmotionTimeline uploadId={id} />
        </LazyMount>

        <LazyMount fallback={<SectionSkeleton height={620} />}>
          <WordAnalytics uploadId={id} />
        </LazyMount>

        <LazyMount fallback={<SectionSkeleton height={320} />}>
          <SearchModule uploadId={id} />
        </LazyMount>

        <LazyMount fallback={<SectionSkeleton height={620} />}>
          <ConflictAnalysis uploadId={id} />
        </LazyMount>

        <LazyMount fallback={<SectionSkeleton height={520} />}>
          <LoveLanguages uploadId={id} />
        </LazyMount>

        <LazyMount fallback={<SectionSkeleton height={420} />}>
          <HealthScore uploadId={id} />
        </LazyMount>
      </div>
    </ReadyGate>
  );
}
