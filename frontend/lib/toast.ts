"use client";

/**
 * Lightweight toast bus.
 *
 * The actual rendering happens in <Toaster /> (mounted once in the root
 * layout). Anywhere else in the app calls `toast(...)` and gets a
 * notification — no provider, no context plumbing, no React import.
 *
 * Implementation: a tiny event emitter on top of a useSyncExternalStore-
 * compatible snapshot. We deliberately avoid Zustand here so that
 * `lib/toast.ts` stays a leaf module — anything (a fetch wrapper, a
 * non-React file) can `import { toast }` and not pull React into its
 * dependency graph.
 */

export type ToastVariant = "default" | "success" | "warning" | "destructive";

export interface ToastInput {
  title?: string;
  description?: string;
  variant?: ToastVariant;
  /** ms before auto-dismiss. 0 disables. Default: 4500ms (5500 for destructive). */
  duration?: number;
}

export interface ToastInstance extends Required<Omit<ToastInput, "duration">> {
  id: string;
  duration: number;
  createdAt: number;
}

type Listener = (toasts: ToastInstance[]) => void;

let queue: ToastInstance[] = [];
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l(queue);
}

function genId(): string {
  // Crypto.randomUUID is available in modern browsers; fallback for SSR.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Public: queue a toast. Returns the toast id so callers can dismiss it. */
export function toast(input: ToastInput | string): string {
  const normalized: ToastInput =
    typeof input === "string" ? { title: input } : input;

  const variant = normalized.variant ?? "default";
  const duration =
    normalized.duration ??
    (variant === "destructive" ? 5500 : 4500);

  const t: ToastInstance = {
    id: genId(),
    title: normalized.title ?? "",
    description: normalized.description ?? "",
    variant,
    duration,
    createdAt: Date.now(),
  };

  queue = [...queue, t];
  emit();
  return t.id;
}

toast.success = (msg: string, description?: string): string =>
  toast({ title: msg, description, variant: "success" });
toast.error = (msg: string, description?: string): string =>
  toast({ title: msg, description, variant: "destructive" });
toast.warning = (msg: string, description?: string): string =>
  toast({ title: msg, description, variant: "warning" });

/** Public: dismiss a toast by id. No-op if it's already gone. */
export function dismissToast(id: string): void {
  queue = queue.filter((t) => t.id !== id);
  emit();
}

// ---- Internal API for <Toaster /> ----------------------------------------

/** Snapshot accessor. Stable identity guard handled by the consumer. */
export function _getSnapshot(): ToastInstance[] {
  return queue;
}

const EMPTY_QUEUE: readonly ToastInstance[] = [];

/** SSR snapshot — Toaster must render server-side as an empty viewport. */
export function _getServerSnapshot(): ToastInstance[] {
  return EMPTY_QUEUE as ToastInstance[];
}

export function _subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
