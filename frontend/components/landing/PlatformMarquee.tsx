"use client";

/**
 * PlatformMarquee — infinite horizontal ticker of the supported exports
 * and pipeline stages. Duplicated once so the loop is seamless.
 */

import { FileJson, FileSpreadsheet, FileText, Languages, Braces, Cpu } from "lucide-react";

import { Reveal } from "./Reveal";

const ITEMS = [
  { label: "WhatsApp", color: "#25D366", icon: FileText },
  { label: "Telegram", color: "#2AABEE", icon: FileJson },
  { label: "Instagram", color: "#E1306C", icon: FileJson },
  { label: "Messenger", color: "#0084FF", icon: FileJson },
  { label: "CSV", color: "#F59E0B", icon: FileSpreadsheet },
  { label: "Raw JSON", color: "#6366F1", icon: Braces },
  { label: "18+ language scripts", color: "#8B5CF6", icon: Languages },
  { label: "Claude + pgvector", color: "#0EA5E9", icon: Cpu },
];

export function PlatformMarquee() {
  const loop = [...ITEMS, ...ITEMS];
  return (
    <section id="platforms" className="relative border-y border-border/70 bg-card/40 py-6">
      <Reveal>
        <p className="mb-4 text-center text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
          Works with the exports you already have
        </p>
      </Reveal>
      <div
        className="relative overflow-hidden"
        style={{
          maskImage: "linear-gradient(90deg, transparent, black 12%, black 88%, transparent)",
          WebkitMaskImage:
            "linear-gradient(90deg, transparent, black 12%, black 88%, transparent)",
        }}
      >
        <div className="lp-marquee gap-3" style={{ ["--lp-duration" as string]: "34s" }}>
          {loop.map((item, i) => {
            const Icon = item.icon;
            return (
              <div
                key={item.label + i}
                className="flex shrink-0 items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-sm font-medium text-foreground shadow-soft"
              >
                <span
                  className="flex h-6 w-6 items-center justify-center rounded-full"
                  style={{ background: `${item.color}1f`, color: item.color }}
                >
                  <Icon className="h-3.5 w-3.5" />
                </span>
                {item.label}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
