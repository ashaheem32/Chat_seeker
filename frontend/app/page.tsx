"use client";

/**
 * Landing / upload page.
 *
 * Visual structure:
 *   1. Brand strip (top-left wordmark + version pill)
 *   2. Hero — gradient Syne headline, supporting subtitle
 *   3. DropZone surface
 *   4. Feature trio — three line-items that promise what the dashboard delivers
 *
 * State machine is owned by useUploadFlow; this file is composition only.
 * On `ready` we redirect to the dashboard route for the new upload.
 */

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Activity,
  ArrowRight,
  Brain,
  MessageSquareText,
  Search,
  Sparkles,
} from "lucide-react";

import { ErrorCard } from "@/components/upload/error-card";
import { DropZone } from "@/components/upload/DropZone";
import { useUploadFlow } from "@/lib/use-upload-flow";
import { toast } from "@/lib/toast";

export default function HomePage() {
  const router = useRouter();
  const { state, uploadFile, uploadText, reset } = useUploadFlow();

  // On `ready`, hand off to the dashboard. Tiny delay so the user sees the
  // 100% completion state for a beat instead of a jarring instant transition.
  useEffect(() => {
    if (state.stage === "ready" && state.uploadId) {
      toast.success("Chat analyzed", "Opening your dashboard…");
      const t = setTimeout(() => {
        router.push(`/dashboard/${state.uploadId}`);
      }, 600);
      return () => clearTimeout(t);
    }
  }, [state.stage, state.uploadId, router]);

  return (
    <div className="relative min-h-screen overflow-hidden">
      {/* Ambient backgrounds — layered radial glows + dotted grid */}
      <BackgroundOrnaments />

      <Header />

      <main className="relative mx-auto flex min-h-[calc(100vh-72px)] max-w-3xl flex-col items-stretch px-6 pb-24 pt-12 sm:pt-20">
        <Hero />

        <section className="mt-12">
          {state.stage === "failed" && state.error ? (
            <ErrorCard error={state.error} onRetry={reset} />
          ) : (
            <DropZone
              onFile={uploadFile}
              onText={uploadText}
              stage={state.stage}
              progress={state.progress}
              message={state.message}
              detectedPlatform={state.detectedPlatform}
            />
          )}
        </section>

        <FeatureTrio />
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header() {
  return (
    <header className="relative z-10 mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
      <Link
        href="/"
        className="group flex items-center gap-2 text-foreground transition hover:text-primary"
      >
        <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-accent text-primary-foreground shadow-glow">
          <MessageSquareText className="h-4 w-4" />
        </span>
        <span className="font-display text-lg font-semibold tracking-tight">
          ChatLens
        </span>
      </Link>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="hidden rounded-full border border-border bg-card/40 px-2.5 py-1 sm:inline-flex">
          beta · 0.1
        </span>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function Hero() {
  return (
    <section className="text-center animate-fade-up">
      <div className="mx-auto inline-flex items-center gap-2 rounded-full border border-border bg-card/40 px-3 py-1 text-[11px] uppercase tracking-[0.2em] text-muted-foreground backdrop-blur">
        <Sparkles className="h-3 w-3 text-primary" />
        Personal intelligence for your conversations
      </div>

      <h1 className="mt-6 font-display text-5xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
        <span className="gradient-text">Understand</span>
        <br />
        <span className="text-foreground">your conversations</span>
      </h1>

      <p className="mx-auto mt-5 max-w-xl text-balance text-base leading-relaxed text-muted-foreground sm:text-lg">
        Upload any chat export. Get a deep analysis of your relationship
        patterns, emotions, and moments — all private to you.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Feature trio (below the dropzone)
// ---------------------------------------------------------------------------

function FeatureTrio() {
  const items = [
    {
      icon: Activity,
      title: "Emotion timelines",
      copy: "Sentiment + emotion charted across every message and sender, with the moments that shifted the curve.",
    },
    {
      icon: Search,
      title: "Ask anything",
      copy: "Natural-language search across your entire history. \"How did we apologize?\", \"happiest weekends\"…",
    },
    {
      icon: Brain,
      title: "Patterns, not noise",
      copy: "Conflict cycles, love languages, communication health. Insight that respects the shape of how you actually talk.",
    },
  ];

  return (
    <section className="mt-20 grid gap-6 sm:grid-cols-3">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div
            key={item.title}
            className="surface-card group p-5 transition-colors hover:border-primary/30"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10 text-primary transition group-hover:bg-primary/20">
              <Icon className="h-4 w-4" />
            </div>
            <h3 className="mt-4 font-display text-base font-semibold text-foreground">
              {item.title}
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
              {item.copy}
            </p>
          </div>
        );
      })}

      <p className="sm:col-span-3 mt-2 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
        <span>Backed by Claude + pgvector. </span>
        <Link
          href="https://github.com/anthropics/claude-code"
          className="inline-flex items-center gap-1 text-foreground/80 transition hover:text-primary"
        >
          Read about how it works
          <ArrowRight className="h-3 w-3" />
        </Link>
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Background ornaments
// ---------------------------------------------------------------------------

function BackgroundOrnaments() {
  return (
    <>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[640px] bg-indigo-glow opacity-90"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -left-32 top-40 h-[420px] w-[420px] rounded-full bg-violet-glow blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-dots opacity-[0.18]"
        style={{
          maskImage:
            "radial-gradient(60% 50% at 50% 30%, black, transparent 80%)",
          WebkitMaskImage:
            "radial-gradient(60% 50% at 50% 30%, black, transparent 80%)",
        }}
      />
    </>
  );
}
