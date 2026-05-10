import type { Metadata, Viewport } from "next";
import { DM_Sans, Syne } from "next/font/google";

import { Toaster } from "@/components/ui/toaster";

import "./globals.css";

/**
 * Root layout — wires up:
 *   - Type system (Syne for display, DM Sans for body)
 *   - Dark mode (always on; we still set the .dark class for primitives)
 *   - Global Toaster mount
 *
 * The fonts are loaded via next/font so they're served from the same origin
 * with the right preload hints. We expose them through CSS variables that
 * Tailwind reads in `tailwind.config.ts` (font-sans / font-display).
 */

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  // The body uses 400-600 most often; 700 reserved for badges / emphasis.
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-display",
  // Hero gradients use 700; section heads use 600.
  weight: ["500", "600", "700", "800"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "ChatLens — Understand your conversations",
    template: "%s · ChatLens",
  },
  description:
    "ChatLens turns chat exports into a personal intelligence dashboard: emotion timelines, conflict analysis, love languages, and natural-language search across every message.",
  keywords: [
    "chat analysis",
    "WhatsApp analyzer",
    "Telegram analyzer",
    "relationship insights",
    "AI chat",
  ],
  authors: [{ name: "ChatLens" }],
  openGraph: {
    title: "ChatLens",
    description:
      "Upload any chat export. Get a deep analysis of your relationship patterns, emotions, and moments.",
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: "#0f0f13",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      // suppressHydrationWarning protects against future theme-toggle work
      // that flips .dark client-side; safe to keep even though we're dark-only today.
      suppressHydrationWarning
      className={`${dmSans.variable} ${syne.variable} dark`}
    >
      <body className="min-h-screen bg-background font-sans antialiased">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
