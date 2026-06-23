"use client";

/**
 * Dashboard shell.
 *
 * Layout grid:
 *   ┌────────────┬────────────────────────────────────────┐
 *   │  Sidebar   │  Header (48px, sticky)                 │
 *   │  (240/56)  ├────────────────────────────────────────┤
 *   │            │                                        │
 *   │  nav       │  Page content (children)               │
 *   │            │                                        │
 *   └────────────┴────────────────────────────────────────┘
 *
 * Sidebar:
 *   - 240px expanded, 56px collapsed
 *   - Logo + chat title + participant avatars at top
 *   - Lucide icon-led nav items, each linking to a `/dashboard/[chatId]/...` route
 *   - Settings + Delete at the bottom (delete behind a confirm prompt)
 *
 * Header:
 *   - Chat name + date range on the left
 *   - StatusBadge if processing isn't `done`
 *   - Export button on the right (downloads the UCJ payload)
 *
 * Data:
 *   We hydrate the upload meta + status from /upload/{id} once on mount and
 *   poll while still processing. The Zustand store holds the current upload
 *   so children can read meta without re-fetching.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Download,
  Heart,
  LayoutDashboard,
  MessageSquareText,
  Search,
  Settings,
  TrendingUp,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge, type StatusKind } from "@/components/ui/StatusBadge";
import { getUCJ, getUploadStatus, ucjDownloadUrl } from "@/lib/api";
import { useStore } from "@/lib/store";
import { toast } from "@/lib/toast";
import type { ChatMeta, ProcessingStage } from "@/lib/types";
import { cn, safeFormatDate } from "@/lib/utils";

const SIDEBAR_OPEN = 240;
const SIDEBAR_CLOSED = 56;
const HEADER_HEIGHT = 48;

const POLL_INTERVAL_MS = 2_500;

interface NavItem {
  href: (chatId: string) => string;
  label: string;
  icon: typeof LayoutDashboard;
  /** When true, the route matches exactly (not just prefix). */
  exact?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    label: "Overview",
    icon: LayoutDashboard,
    href: (id) => `/dashboard/${id}`,
    exact: true,
  },
  {
    label: "Emotion Timeline",
    icon: TrendingUp,
    href: (id) => `/dashboard/${id}/timeline`,
  },
  {
    label: "Words & Emojis",
    icon: BarChart2,
    href: (id) => `/dashboard/${id}/words`,
  },
  {
    label: "Search",
    icon: Search,
    href: (id) => `/dashboard/${id}/search`,
  },
  {
    label: "Conflict Analysis",
    icon: AlertTriangle,
    href: (id) => `/dashboard/${id}/conflicts`,
  },
  {
    label: "Love Languages",
    icon: Heart,
    href: (id) => `/dashboard/${id}/love`,
  },
  {
    label: "Health Score",
    icon: Activity,
    href: (id) => `/dashboard/${id}/health`,
  },
  {
    label: "Timeline",
    icon: Calendar,
    href: (id) => `/dashboard/${id}/calendar`,
  },
];

export default function DashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { chatId: string };
}) {
  const collapsed = useStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useStore((s) => s.toggleSidebar);
  const setUpload = useStore((s) => s.setUpload);

  const [meta, setMeta] = useState<ChatMeta | null>(null);
  const [status, setStatus] = useState<ProcessingStage>("queued");
  const [error, setError] = useState<string | null>(null);
  // The polling effect is keyed only on chatId, so the `meta` state it closes
  // over is permanently stale (null). A ref survives across ticks and lets us
  // fetch meta exactly once instead of on every 2.5s poll.
  const metaLoadedRef = useRef(false);

  // ---- Hydrate upload + poll while processing ---------------------------
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (cancelled) return;
      try {
        const s = await getUploadStatus(params.chatId);
        if (cancelled) return;
        setStatus(s.status);

        // Pull meta once it's been parsed. We don't depend on `ready` to
        // start fetching meta — even mid-processing the meta block is
        // populated as soon as parsing finishes.
        if (
          !metaLoadedRef.current &&
          (s.status === "ready" ||
            s.status === "persisting" ||
            s.status === "parsing")
        ) {
          try {
            const ucj = await getUCJ(params.chatId, 0); // 0 messages — meta only
            if (cancelled) return;
            metaLoadedRef.current = true;
            setMeta(ucj.meta);
            setUpload({
              uploadId: params.chatId,
              filename: ucj.meta.source_file,
              meta: ucj.meta,
              status: s.status,
              stageDetail: s.stage_detail,
            });
          } catch {
            // Meta fetch failures are tolerable while still processing —
            // the next poll will retry.
          }
        }

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
    // We intentionally exclude `meta` so the polling loop is keyed only on
    // chatId — the inner check guards against re-fetching meta unnecessarily.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.chatId, setUpload]);

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar
        chatId={params.chatId}
        meta={meta}
        collapsed={collapsed}
        onToggle={toggleSidebar}
      />

      {/* Right-side flex column: sticky header + scrolling content */}
      <div
        className="flex min-w-0 flex-1 flex-col"
        style={{
          // Reserve sidebar space without forcing the children into an extra
          // wrapper — flex handles it because the sidebar is `position: fixed`
          // for its own scroll region.
          marginLeft: collapsed ? SIDEBAR_CLOSED : SIDEBAR_OPEN,
          transition: "margin-left 220ms ease",
        }}
      >
        <DashboardHeader
          chatId={params.chatId}
          meta={meta}
          status={status}
          error={error}
        />

        <main className="relative flex-1" style={{ paddingTop: HEADER_HEIGHT }}>
          <div className="px-6 py-6 sm:px-8 sm:py-8">{children}</div>
        </main>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function Sidebar({
  chatId,
  meta,
  collapsed,
  onToggle,
}: {
  chatId: string;
  meta: ChatMeta | null;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-40 flex h-screen flex-col",
        "border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
        "transition-[width] duration-200 ease-out",
      )}
      style={{ width: collapsed ? SIDEBAR_CLOSED : SIDEBAR_OPEN }}
      aria-label="Dashboard navigation"
    >
      {/* Logo / collapse */}
      <div className="flex h-12 items-center justify-between border-b border-sidebar-border px-3">
        <Link
          href="/"
          className={cn(
            "flex items-center gap-2 text-foreground transition hover:text-primary",
            collapsed && "mx-auto",
          )}
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-primary to-accent text-primary-foreground shadow-glow">
            <MessageSquareText className="h-3.5 w-3.5" />
          </span>
          {!collapsed && (
            <span className="font-display text-sm font-semibold tracking-tight">
              ChatLens
            </span>
          )}
        </Link>

        {!collapsed && (
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-sidebar-accent hover:text-foreground"
            aria-label="Collapse sidebar"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Chat header */}
      <ChatHeaderBlock chatId={chatId} meta={meta} collapsed={collapsed} />

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3">
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => {
            const href = item.href(chatId);
            const active = item.exact
              ? pathname === href
              : pathname.startsWith(href);
            const Icon = item.icon;
            return (
              <li key={item.label}>
                <Link
                  href={href}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "group flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition",
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.3)]"
                      : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                    collapsed && "justify-center",
                  )}
                >
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0 transition",
                      active
                        ? "text-primary"
                        : "text-muted-foreground group-hover:text-foreground",
                    )}
                  />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer actions */}
      <div className="mt-auto border-t border-sidebar-border p-2">
        <SidebarSecondaryAction
          icon={Settings}
          label="Settings"
          href="/settings"
          collapsed={collapsed}
        />
        <DeleteChatAction chatId={chatId} collapsed={collapsed} />

        {collapsed && (
          <button
            type="button"
            onClick={onToggle}
            className="mt-2 flex w-full items-center justify-center rounded-md p-2 text-muted-foreground transition hover:bg-sidebar-accent hover:text-foreground"
            aria-label="Expand sidebar"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        )}
      </div>
    </aside>
  );
}

function ChatHeaderBlock({
  chatId,
  meta,
  collapsed,
}: {
  chatId: string;
  meta: ChatMeta | null;
  collapsed: boolean;
}) {
  if (collapsed) {
    return (
      <div className="border-b border-sidebar-border px-2 py-3">
        <ParticipantAvatars participants={meta?.participants ?? []} max={2} />
      </div>
    );
  }
  return (
    <div className="border-b border-sidebar-border px-3 py-3">
      <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
        Conversation
      </p>
      <p
        className="mt-1 truncate text-sm font-medium text-foreground"
        title={meta?.source_file ?? chatId}
      >
        {meta?.source_file ?? "Loading…"}
      </p>
      <ParticipantAvatars
        participants={meta?.participants ?? []}
        className="mt-2.5"
      />
    </div>
  );
}

function ParticipantAvatars({
  participants,
  className,
  max = 4,
}: {
  participants: string[];
  className?: string;
  max?: number;
}) {
  const visible = participants.slice(0, max);
  const overflow = Math.max(0, participants.length - max);
  return (
    <div className={cn("flex items-center -space-x-1.5", className)}>
      {visible.map((name) => (
        <Avatar key={name} name={name} />
      ))}
      {overflow > 0 ? (
        <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-sidebar bg-muted text-[10px] font-medium text-muted-foreground">
          +{overflow}
        </span>
      ) : null}
    </div>
  );
}

function Avatar({ name }: { name: string }) {
  const initials = getInitials(name);
  // Deterministic hue from the name so each participant has a stable color.
  const hue = stringToHue(name);
  return (
    <span
      className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-sidebar text-[10px] font-medium text-foreground/90"
      style={{
        background: `linear-gradient(135deg, hsl(${hue} 60% 45%), hsl(${(hue + 30) % 360} 70% 55%))`,
      }}
      title={name}
    >
      {initials}
    </span>
  );
}

function SidebarSecondaryAction({
  icon: Icon,
  label,
  href,
  collapsed,
  destructive,
  onClick,
}: {
  icon: typeof Settings;
  label: string;
  href?: string;
  collapsed: boolean;
  destructive?: boolean;
  onClick?: () => void;
}) {
  const className = cn(
    "flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition",
    destructive
      ? "text-destructive/90 hover:bg-destructive/10"
      : "text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
    collapsed && "justify-center",
  );
  const content = (
    <>
      <Icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span>{label}</span>}
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={className}
        title={collapsed ? label : undefined}
      >
        {content}
      </button>
    );
  }
  return (
    <Link
      href={href ?? "#"}
      className={className}
      title={collapsed ? label : undefined}
    >
      {content}
    </Link>
  );
}

function DeleteChatAction({
  chatId,
  collapsed,
}: {
  chatId: string;
  collapsed: boolean;
}) {
  // Soft handler — actual delete endpoint lands with the management module.
  // Today we just confirm + toast so the UX shape is in place.
  const onClick = () => {
    const ok = window.confirm(
      "Delete this chat and all of its analysis? This cannot be undone.",
    );
    if (!ok) return;
    toast.warning(
      "Delete pending",
      "Chat deletion will be wired up with the management module.",
    );
    // eslint-disable-next-line no-console
    console.info("[dashboard] delete requested for chat", chatId);
  };
  return (
    <SidebarSecondaryAction
      icon={Trash2}
      label="Delete chat"
      collapsed={collapsed}
      destructive
      onClick={onClick}
    />
  );
}

// ---------------------------------------------------------------------------
// Header bar
// ---------------------------------------------------------------------------

function DashboardHeader({
  chatId,
  meta,
  status,
  error,
}: {
  chatId: string;
  meta: ChatMeta | null;
  status: ProcessingStage;
  error: string | null;
}) {
  const dateRange = meta
    ? `${safeFormatDate(meta.date_range.start, "MMM d, yyyy")} – ${safeFormatDate(
        meta.date_range.end,
        "MMM d, yyyy",
      )}`
    : "";

  const showStatus = status !== "ready" || !!error;

  return (
    <header
      className="fixed left-0 right-0 top-0 z-30 flex items-center justify-between gap-4 border-b border-border/80 bg-background/85 px-4 backdrop-blur-md sm:px-6"
      style={{
        height: HEADER_HEIGHT,
        // Match the sidebar offset on the left
        paddingLeft: undefined,
      }}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <h1 className="truncate font-display text-base font-semibold tracking-tight">
          {meta?.source_file ?? "Loading…"}
        </h1>
        {meta ? (
          <span className="hidden truncate text-xs text-muted-foreground sm:inline">
            · {dateRange} · {meta.total_messages.toLocaleString()} messages
          </span>
        ) : null}
        {showStatus ? (
          <StatusBadge
            status={(error ? "failed" : status) as StatusKind}
            label={error ? "Failed" : undefined}
          />
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <Button
          asChild
          variant="outline"
          size="sm"
          className="hidden sm:inline-flex"
        >
          <a href={ucjDownloadUrl(chatId)} download>
            <Download className="mr-1.5 h-3.5 w-3.5" />
            Export UCJ
          </a>
        </Button>
        <Button asChild size="sm" variant="ghost" className="sm:hidden">
          <a href={ucjDownloadUrl(chatId)} download aria-label="Export UCJ">
            <Download className="h-4 w-4" />
          </a>
        </Button>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
