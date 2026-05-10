"use client";

/**
 * <Toaster /> — global notification viewport.
 *
 * Mounted once at the root layout. Subscribes to the toast bus in
 * `lib/toast.ts` via useSyncExternalStore, which gives us strict-mode-
 * safe, SSR-aware rendering with no provider boilerplate.
 *
 * Visual design:
 *   - Bottom-right viewport (default for desktop chat-style apps).
 *   - Each toast slides in from the right with a soft drop shadow and
 *     a 1px indigo / variant-tinted left border.
 *   - Auto-dismiss after `toast.duration`; user can also click ✕.
 *   - Built on Radix Toast primitives so screen readers announce them
 *     correctly and the keyboard escape hatch works.
 */

import * as ToastPrimitives from "@radix-ui/react-toast";
import { CheckCircle2, AlertTriangle, AlertCircle, Info, X } from "lucide-react";
import { useSyncExternalStore } from "react";

import {
  _getServerSnapshot,
  _getSnapshot,
  _subscribe,
  dismissToast,
  type ToastInstance,
  type ToastVariant,
} from "@/lib/toast";
import { cn } from "@/lib/utils";

const VARIANT_STYLES: Record<
  ToastVariant,
  { border: string; icon: typeof CheckCircle2; iconClass: string }
> = {
  default: {
    border: "border-l-primary",
    icon: Info,
    iconClass: "text-primary",
  },
  success: {
    border: "border-l-success",
    icon: CheckCircle2,
    iconClass: "text-success",
  },
  warning: {
    border: "border-l-warning",
    icon: AlertTriangle,
    iconClass: "text-warning",
  },
  destructive: {
    border: "border-l-destructive",
    icon: AlertCircle,
    iconClass: "text-destructive",
  },
};

export function Toaster() {
  const toasts = useSyncExternalStore(_subscribe, _getSnapshot, _getServerSnapshot);

  return (
    <ToastPrimitives.Provider swipeDirection="right" duration={Infinity}>
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
      <ToastPrimitives.Viewport
        className={cn(
          "fixed bottom-0 right-0 z-[100] m-4 flex w-full max-w-sm flex-col gap-2 outline-none",
          "sm:bottom-4 sm:right-4",
        )}
      />
    </ToastPrimitives.Provider>
  );
}

function ToastItem({ toast }: { toast: ToastInstance }) {
  const variant = VARIANT_STYLES[toast.variant];
  const Icon = variant.icon;

  return (
    <ToastPrimitives.Root
      duration={toast.duration === 0 ? Infinity : toast.duration}
      onOpenChange={(open) => {
        if (!open) dismissToast(toast.id);
      }}
      className={cn(
        "group relative flex items-start gap-3 overflow-hidden rounded-xl",
        "border border-border bg-card-elevated/95 backdrop-blur",
        "border-l-2 px-4 py-3 pr-10 shadow-soft",
        "data-[state=open]:animate-fade-up",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full",
        variant.border,
      )}
    >
      <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", variant.iconClass)} />
      <div className="min-w-0 flex-1">
        {toast.title ? (
          <ToastPrimitives.Title className="text-sm font-medium leading-tight text-foreground">
            {toast.title}
          </ToastPrimitives.Title>
        ) : null}
        {toast.description ? (
          <ToastPrimitives.Description className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {toast.description}
          </ToastPrimitives.Description>
        ) : null}
      </div>
      <ToastPrimitives.Close
        className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground group-hover:opacity-100 focus:opacity-100"
        aria-label="Dismiss notification"
      >
        <X className="h-3.5 w-3.5" />
      </ToastPrimitives.Close>
    </ToastPrimitives.Root>
  );
}
