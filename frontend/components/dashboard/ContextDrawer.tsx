"use client";

/**
 * ContextDrawer — slide-in panel that shows messages around a target.
 *
 * Used by both the search module ("See in context" on a hit) and the
 * conflict module ("Read conversation" on a difficult moment).
 *
 * Lookup keys:
 *   The backend's /context endpoint takes the message's DB UUID. Some
 *   callers (search) only have the UCJ-level msg_id token in hand;
 *   they pass an `evidenceById` map that lets us resolve the token to
 *   a UUID. Other callers (conflict windows) already have the UUID and
 *   pass it directly via `targetDbId`. The component prefers the UUID
 *   when both are available and gracefully shows an error otherwise.
 *
 * Built on Radix Dialog so the focus-trap, ESC-to-close, and aria-modal
 * semantics are correct without us reimplementing them.
 */

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { format, parseISO } from "date-fns";

import { getMessageContext } from "@/lib/api";
import type {
  ContextMessage,
  MessageContextResponse,
  StreamEvidence,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ContextDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  uploadId: string;
  /** UCJ msg_id token. Prefer `targetDbId` when available — see below. */
  targetMsgId?: string | null;
  /** DB UUID of the target message; used directly with the API. */
  targetDbId?: string | null;
  /**
   * Resolution map for callers that only have msg_id tokens. When both
   * `targetMsgId` and an entry in `evidenceById` are provided, we use
   * the entry's `id` (the UUID) for the API call.
   */
  evidenceById?: Record<string, StreamEvidence>;
  /** Number of messages to load on each side. Default 10. */
  windowSize?: number;
}

export function ContextDrawer({
  open,
  onOpenChange,
  uploadId,
  targetMsgId,
  targetDbId,
  evidenceById,
  windowSize = 10,
}: ContextDrawerProps) {
  const [data, setData] = useState<MessageContextResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setData(null);
      setError(null);
      return;
    }

    // Pick the best id we have: explicit UUID > resolved-from-evidence > raw msg_id.
    const resolved =
      targetDbId ??
      (targetMsgId && evidenceById?.[targetMsgId]?.id) ??
      targetMsgId ??
      null;

    if (!resolved) {
      setError("Couldn't resolve the message id.");
      return;
    }

    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    getMessageContext(uploadId, resolved, {
      window: windowSize,
      signal: ctrl.signal,
    })
      .then((r) => {
        if (cancelled) return;
        setData(r);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load context");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [open, targetMsgId, targetDbId, uploadId, evidenceById, windowSize]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-background/70 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
          )}
        />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-md flex-col",
            "border-l border-border bg-card",
            "shadow-[-12px_0_40px_-12px_rgba(0,0,0,0.5)]",
            "data-[state=open]:animate-in data-[state=open]:slide-in-from-right",
            "data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right",
          )}
        >
          <Dialog.Title className="sr-only">Message context</Dialog.Title>
          <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                Conversation context
              </p>
              <p className="truncate font-display text-sm font-semibold">
                {data?.target.sender ?? (loading ? "Loading…" : "—")}
                {data ? (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {format(parseISO(data.target.timestamp), "MMM d, yyyy")}
                  </span>
                ) : null}
              </p>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded-md p-1 text-muted-foreground transition hover:bg-secondary hover:text-foreground"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex-1 overflow-y-auto p-4">
            {error ? (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-foreground">
                <p className="font-medium">Couldn&apos;t load context</p>
                <p className="mt-1 text-xs text-muted-foreground">{error}</p>
              </div>
            ) : loading || !data ? (
              <ContextSkeleton />
            ) : (
              <ContextThread context={data} />
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ContextThread({ context }: { context: MessageContextResponse }) {
  const ordered: ContextMessage[] = [
    ...context.before,
    context.target,
    ...context.after,
  ];
  return (
    <ol className="space-y-2">
      {ordered.map((m) => {
        const isTarget = m.msg_id === context.target.msg_id;
        const hue = stringToHue(m.sender);
        return (
          <li
            key={`${m.msg_id}|${isTarget ? "t" : ""}`}
            className={cn(
              "rounded-lg border p-3 transition",
              isTarget
                ? "border-primary/60 bg-primary/10 shadow-glow"
                : "border-border bg-card-elevated/30",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className="flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-semibold"
                  style={{
                    background: `linear-gradient(135deg, hsl(${hue} 60% 45%), hsl(${(hue + 30) % 360} 70% 55%))`,
                  }}
                  aria-hidden
                >
                  {getInitials(m.sender)}
                </span>
                <p
                  className={cn(
                    "truncate text-sm font-medium",
                    isTarget ? "text-foreground" : "text-foreground/80",
                  )}
                >
                  {m.sender}
                </p>
              </div>
              <span className="text-[10px] text-muted-foreground">
                {format(parseISO(m.timestamp), "h:mm a")}
              </span>
            </div>
            <p
              className={cn(
                "mt-1 whitespace-pre-wrap text-sm leading-relaxed",
                isTarget ? "text-foreground" : "text-foreground/85",
              )}
            >
              {m.content}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function ContextSkeleton() {
  return (
    <ol className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <li
          key={i}
          className="rounded-lg border border-border bg-card-elevated/30 p-3"
        >
          <span className="shimmer mb-1 block h-3 w-24 rounded" />
          <span className="shimmer block h-3 w-full rounded" />
        </li>
      ))}
    </ol>
  );
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0]!;
  if (parts.length === 1) return first.slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1]!;
  return (first[0]! + last[0]!).toUpperCase();
}

function stringToHue(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}
