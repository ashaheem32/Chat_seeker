"use client";

/**
 * Overview page.
 *
 * Composition:
 *   - <StatsOverview /> at the top — Module 01, the at-a-glance numbers,
 *     behavioral cards, participant comparison and fun facts. Manages its
 *     own fetch + caching against /api/stats/{id}/overview.
 *   - Forward-looking placeholder panels for the modules that haven't
 *     landed yet (sentiment timeline, topics, etc). These render polished
 *     empty states so the dashboard structure is reviewable end-to-end.
 *
 * Page-level concern is just the "is this chat ready for the dashboard?"
 * gate. We poll /upload/{id} until status === "ready" so the user sees
 * a clear "still parsing" state instead of a 409 from the stats endpoint.
 */

import { useEffect, useState } from "react";
import {
  AlertCircle,
  Loader2,
  Sparkles,
} from "lucide-react";

import { ConflictAnalysis } from "@/components/dashboard/ConflictAnalysis";
import { EmotionTimeline } from "@/components/dashboard/EmotionTimeline";
import { HealthScore } from "@/components/dashboard/HealthScore";
import { LoveLanguages } from "@/components/dashboard/LoveLanguages";
import { SearchModule } from "@/components/dashboard/SearchModule";
import { StatsOverview } from "@/components/dashboard/StatsOverview";
import { WordAnalytics } from "@/components/dashboard/WordAnalytics";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { getUploadStatus } from "@/lib/api";
import type { ProcessingStage } from "@/lib/types";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 2_500;

export default function OverviewPage({
  params,
}: {
  params: { chatId: string };
}) {
  const [status, setStatus] = useState<ProcessingStage>("queued");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (cancelled) return;
      try {
        const s = await getUploadStatus(params.chatId);
        if (cancelled) return;
        setStatus(s.status);
        if (s.status === "failed") {
          setError(s.error ?? "Processing failed");
          return;
        }
        if (s.status !== "ready") {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load chat");
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [params.chatId]);

  if (error) return <ErrorState message={error} />;
  if (status !== "ready") return <ProcessingState status={status} />;

  return (
    <div className="space-y-10 animate-fade-up">
      <StatsOverview uploadId={params.chatId} />
      <EmotionTimeline uploadId={params.chatId} />
      <WordAnalytics uploadId={params.chatId} />
      <SearchModule uploadId={params.chatId} />
      <ConflictAnalysis uploadId={params.chatId} />
      <LoveLanguages uploadId={params.chatId} />
      <HealthScore uploadId={params.chatId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading + error states
// ---------------------------------------------------------------------------

function ProcessingState({ status }: { status: ProcessingStage }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card shadow-glow">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
      </div>
      <div>
        <h2 className="font-display text-xl font-semibold">Analyzing your chat</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          We&apos;re parsing messages, computing sentiment, and embedding for search.
        </p>
      </div>
      <StatusBadge status={status} />
      <p className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Sparkles className="h-3 w-3 text-primary" />
        This page will refresh automatically.
      </p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <AlertCircle className="h-10 w-10 text-destructive" />
      <h2 className="font-display text-xl font-semibold">Something went wrong</h2>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
    </div>
  );
}
