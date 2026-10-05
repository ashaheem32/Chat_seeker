"use client";

/**
 * WordAnalytics — Module 03 of the dashboard.
 *
 * Sections:
 *   1. Word cloud — custom CSS-flow layout (sized by frequency, hue per
 *      sender). Combined / per-sender toggle and stopword toggle.
 *   2. Top-words bar chart — Recharts horizontal bars, stacked by sender.
 *   3. Emoji dashboard — top-20 grid, "most unique to sender", sentiment
 *      correlation badges.
 *   4. Vocabulary richness — distinctive-words columns, type-token ratio,
 *      avg-message-length-over-time line.
 *   5. Common phrases — top bigrams + "inside phrases" both senders use.
 *
 * Why a custom word cloud (no react-wordcloud / d3-cloud):
 *   We don't need a packed/spiral layout — a CSS flexbox flow with
 *   variable font sizes reads as a "word cloud" and sidesteps the
 *   overlap-resolving complexity. It also stays performant on long
 *   chats and renders correctly server-side.
 *
 * Each section fetches independently and shows its own skeleton/error
 * state so a slow request in one panel doesn't block the others.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  AlertCircle,
  CloudFog,
  Hash,
  Layers,
  Moon,
  RefreshCcw,
  Smile,
  Sparkles,
  Type,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  getBigrams,
  getEmojiFrequency,
  getLateNightStats,
  getUniqueWords,
  getWordFrequency,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import type {
  BigramItem,
  BigramsResponse,
  EmojiFrequencyItem,
  EmojiFrequencyResponse,
  LateNightStats,
  MessageLengthPoint,
  SenderVocab,
  UniqueWordsResponse,
  WordFrequency,
  WordFrequencyItem,
} from "@/lib/types";
import { cn, safeFormatDate } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function WordAnalytics({ uploadId }: { uploadId: string }) {
  return (
    <Tooltip.Provider delayDuration={150}>
      <section className="space-y-8" aria-labelledby="word-analytics-heading">
        <header>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Module 03
          </p>
          <h2
            id="word-analytics-heading"
            className="mt-1 font-display text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            Words &amp; Emojis
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Vocabulary, recurring phrases, emoji habits — and the specific
            words that mark each person&apos;s voice.
          </p>
        </header>

        <WordCloudAndChart uploadId={uploadId} />
        <EmojiSection uploadId={uploadId} />
        <VocabSection uploadId={uploadId} />
        <PhrasesSection uploadId={uploadId} />
        <LateNightSection uploadId={uploadId} />
      </section>
    </Tooltip.Provider>
  );
}

// ===========================================================================
// Section 1 + 2 — Word cloud + top-words bar chart (share one fetch)
// ===========================================================================

function WordCloudAndChart({ uploadId }: { uploadId: string }) {
  const [excludeStopwords, setExcludeStopwords] = useState(true);
  const [mode, setMode] = useState<"combined" | "split">("combined");
  const [data, setData] = useState<WordFrequency | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getWordFrequency(uploadId, {
      excludeStopwords,
      topN: 120,
      signal: ctrl.signal,
    })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof Error && e.name === "CanceledError") return;
        setError(e instanceof Error ? e.message : "Failed to load words");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId, excludeStopwords]);

  const senders = useMemo(() => {
    if (!data) return [];
    const set = new Set<string>();
    for (const item of data.items) {
      for (const s of Object.keys(item.per_sender)) set.add(s);
    }
    return [...set];
  }, [data]);

  const senderColor = useMemo(
    () => Object.fromEntries(senders.map((s, i) => [s, paletteHue(s, i)])),
    [senders],
  );

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Section 01 · Vocabulary
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Word cloud &amp; top words
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Size = how often the word appears. Color = who said it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ToggleGroup
            value={mode}
            onChange={setMode}
            options={[
              { value: "combined", label: "Combined" },
              { value: "split", label: "Per sender" },
            ]}
          />
          <button
            type="button"
            onClick={() => setExcludeStopwords((v) => !v)}
            className={cn(
              "rounded-full border px-3 py-1 text-[11px] uppercase tracking-wider transition",
              excludeStopwords
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-border bg-card-elevated/40 text-muted-foreground hover:text-foreground",
            )}
            aria-pressed={excludeStopwords}
          >
            Hide common
          </button>
        </div>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <CloudSkeleton />
      ) : data.items.length === 0 ? (
        <EmptyState message="No vocabulary data yet for this conversation." />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
          <WordCloud
            items={data.items}
            mode={mode}
            senders={senders}
            senderColor={senderColor}
            onWord={(w) =>
              toast({
                title: "Click-to-explore landing soon",
                description: `“${w}” will open a filtered timeline view.`,
              })
            }
          />
          <TopWordsChart items={data.items} senders={senders} senderColor={senderColor} />
        </div>
      )}
    </article>
  );
}

function ToggleGroup<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div
      role="radiogroup"
      className="inline-flex items-center rounded-full border border-border bg-card-elevated/40 p-0.5 text-[11px]"
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-full px-2.5 py-0.5 uppercase tracking-wider transition",
            value === o.value
              ? "bg-primary text-primary-foreground shadow-glow"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ----- Word cloud (custom flow layout) -----

function WordCloud({
  items,
  mode,
  senders,
  senderColor,
  onWord,
}: {
  items: WordFrequencyItem[];
  mode: "combined" | "split";
  senders: string[];
  senderColor: Record<string, string>;
  onWord: (word: string) => void;
}) {
  // Cap at 80 words so the cloud stays readable.
  const visible = items.slice(0, 80);

  const max = visible[0]?.count ?? 1;
  const min = visible[visible.length - 1]?.count ?? 1;
  const range = Math.max(1, Math.log(max + 1) - Math.log(min + 1));

  // Pick the dominant sender per word for coloring; combined mode blends
  // colors when senders are roughly even.
  const wordSpec = useMemo(
    () =>
      visible.map((it, idx) => {
        const t = (Math.log(it.count + 1) - Math.log(min + 1)) / range;
        // Font size 0.85rem → 2.6rem, eased so frequent words stand out.
        const size = 0.85 + Math.pow(t, 0.7) * 1.75;
        // Color: in split mode pick the sender's hue; in combined mode
        // pick the dominant sender.
        const dominant = pickDominantSender(it.per_sender);
        // Color resolution: in split mode use the dominant sender's hue;
        // in combined mode default to the dominant if one exists, else the
        // first sender's hue, else the primary token.
        const fallbackKey = dominant ?? senders[0] ?? "";
        const color: string =
          mode === "split"
            ? (dominant ? senderColor[dominant] : undefined) ??
              "hsl(var(--primary))"
            : senderColor[fallbackKey] ?? "hsl(var(--primary))";
        // Slight rotation alternates by index for editorial feel.
        const tilt = (idx % 7 === 0 ? -3 : idx % 11 === 0 ? 2 : 0);
        return { ...it, size, color, tilt };
      }),
    [visible, mode, senders, senderColor, min, range],
  );

  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-5">
      <div className="flex flex-wrap items-baseline justify-center gap-x-4 gap-y-2 leading-tight">
        {wordSpec.map((w) => (
          <Tooltip.Root key={w.word}>
            <Tooltip.Trigger asChild>
              <button
                type="button"
                onClick={() => onWord(w.word)}
                className="inline-block font-display font-semibold transition hover:brightness-125"
                style={{
                  fontSize: `${w.size.toFixed(2)}rem`,
                  color: w.color,
                  transform: `rotate(${w.tilt}deg)`,
                  textShadow: `0 0 18px ${withAlpha(w.color, 0.18)}`,
                }}
              >
                {w.word}
              </button>
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content
                side="top"
                sideOffset={6}
                className="z-50 rounded-lg border border-border bg-card-elevated/95 px-3 py-2 text-xs shadow-soft backdrop-blur"
              >
                <div className="font-display text-sm font-semibold">
                  {w.word}
                </div>
                <div className="text-muted-foreground">
                  {w.count.toLocaleString()} uses · {(w.pct * 100).toFixed(2)}%
                </div>
                {Object.keys(w.per_sender).length > 1 ? (
                  <div className="mt-1 space-y-0.5">
                    {Object.entries(w.per_sender).map(([s, c]) => (
                      <div key={s} className="flex items-center gap-1.5">
                        <span
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: senderColor[s] }}
                        />
                        <span className="text-foreground">{s}</span>
                        <span className="ml-auto tabular-nums text-muted-foreground">
                          {c}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <Tooltip.Arrow className="fill-card-elevated" />
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        ))}
      </div>
    </div>
  );
}

function pickDominantSender(per_sender: Record<string, number>): string | null {
  let best: string | null = null;
  let bestCount = -1;
  for (const [s, c] of Object.entries(per_sender)) {
    if (c > bestCount) {
      best = s;
      bestCount = c;
    }
  }
  return best;
}

// ----- Top-words bar chart -----

function TopWordsChart({
  items,
  senders,
  senderColor,
}: {
  items: WordFrequencyItem[];
  senders: string[];
  senderColor: Record<string, string>;
}) {
  // Take the top 20 words and build flat rows for Recharts: one record per
  // word with one column per sender.
  const rows = useMemo(() => {
    return items.slice(0, 20).map((it) => {
      const row: Record<string, number | string> = {
        word: it.word,
        total: it.count,
      };
      for (const s of senders) {
        row[s] = it.per_sender[s] ?? 0;
      }
      return row;
    });
  }, [items, senders]);

  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Top 20 by frequency
        </p>
      </div>
      <div className="h-[420px] w-full">
        <ResponsiveContainer>
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
            barCategoryGap={4}
          >
            <CartesianGrid
              horizontal={false}
              stroke="hsl(var(--border))"
              strokeOpacity={0.3}
            />
            <XAxis
              type="number"
              stroke="hsl(var(--muted-foreground))"
              tick={{ fontSize: 10 }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="word"
              stroke="hsl(var(--muted-foreground))"
              width={84}
              tick={{ fontSize: 11, fill: "hsl(var(--foreground))" }}
              tickLine={false}
              axisLine={false}
            />
            {senders.map((s) => (
              <Bar
                key={s}
                dataKey={s}
                stackId="counts"
                fill={senderColor[s]}
                radius={[2, 2, 2, 2]}
                isAnimationActive={true}
                animationDuration={700}
              />
            ))}
            <RechartsTooltip
              content={(props: any) => (
                <BarChartTooltip {...props} senderColor={senderColor} />
              )}
              cursor={{ fill: "hsl(var(--primary) / 0.08)" }}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
              iconType="circle"
              align="left"
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function BarChartTooltip({
  active,
  payload,
  label,
  senderColor,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; dataKey: string }>;
  label?: string;
  senderColor: Record<string, string>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const total = payload.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="rounded-xl border border-border bg-card-elevated/95 px-3 py-2 text-xs shadow-soft backdrop-blur">
      <div className="font-display text-sm font-semibold">{label}</div>
      <div className="mt-1 text-muted-foreground">
        {total.toLocaleString()} uses
      </div>
      <div className="mt-1.5 space-y-0.5">
        {payload.map((p) => (
          <div key={p.dataKey} className="flex items-center gap-2">
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: senderColor[p.dataKey] }}
            />
            <span className="text-foreground">{p.dataKey}</span>
            <span className="ml-auto tabular-nums text-muted-foreground">
              {p.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// Section 3 — Emoji dashboard
// ===========================================================================

function EmojiSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<EmojiFrequencyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getEmojiFrequency(uploadId, { topN: 30, signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load emojis");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 02 · Emojis
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Emoji habits
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Top emojis, who leans on which, and how each tracks against sentiment.
        </p>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <EmojiGridSkeleton />
      ) : data.items.length === 0 ? (
        <EmptyState message="No emojis found in this chat." />
      ) : (
        <div className="space-y-6">
          <EmojiGrid items={data.items.slice(0, 20)} totalEmojis={data.total_emojis} />
          {data.unique_to_sender.length > 0 ? (
            <UniqueToSender items={data.unique_to_sender} />
          ) : null}
        </div>
      )}
    </article>
  );
}

function EmojiGrid({
  items,
  totalEmojis,
}: {
  items: EmojiFrequencyItem[];
  totalEmojis: number;
}) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-3">
      {items.map((e) => (
        <EmojiTile key={e.emoji} item={e} totalEmojis={totalEmojis} />
      ))}
    </div>
  );
}

function EmojiTile({
  item,
  totalEmojis,
}: {
  item: EmojiFrequencyItem;
  totalEmojis: number;
}) {
  const senders = Object.keys(item.per_sender);
  const total = senders.reduce((s, k) => s + (item.per_sender[k] ?? 0), 0) || 1;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <div
          className={cn(
            "group relative flex flex-col items-center justify-between gap-2 overflow-hidden rounded-xl",
            "border border-border bg-card-elevated/40 p-3 text-center transition",
            "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-glow",
          )}
        >
          <div className="text-3xl leading-none">{item.emoji}</div>
          <div className="font-display text-xs font-semibold tabular-nums">
            {item.count.toLocaleString()}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {(item.pct * 100).toFixed(1)}%
          </div>

          {/* Per-sender split bar */}
          {senders.length > 0 ? (
            <div className="flex h-1 w-full gap-px overflow-hidden rounded-full bg-muted">
              {senders.map((s) => {
                const share = (item.per_sender[s] ?? 0) / total;
                if (share === 0) return null;
                return (
                  <span
                    key={s}
                    style={{
                      width: `${share * 100}%`,
                      background: paletteHue(s, senders.indexOf(s)),
                    }}
                  />
                );
              })}
            </div>
          ) : null}

          {/* Sentiment correlation pip */}
          {item.avg_sentiment != null ? (
            <span
              className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full"
              style={{
                background: sentimentColor(item.avg_sentiment),
                boxShadow: `0 0 8px ${sentimentColor(item.avg_sentiment)}`,
              }}
              aria-hidden
            />
          ) : null}
        </div>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="top"
          sideOffset={6}
          className="z-50 rounded-lg border border-border bg-card-elevated/95 px-3 py-2 text-xs shadow-soft backdrop-blur"
        >
          <div className="text-2xl leading-none">{item.emoji}</div>
          {item.unicode_name ? (
            <div className="mt-1 font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {item.unicode_name}
            </div>
          ) : null}
          <div className="mt-1 text-muted-foreground">
            {item.count} uses ({((item.count / Math.max(1, totalEmojis)) * 100).toFixed(1)}%
            of all emojis)
          </div>
          {item.avg_sentiment != null ? (
            <div className="mt-1">
              Avg sentiment{" "}
              <span
                className="font-medium"
                style={{ color: sentimentColor(item.avg_sentiment) }}
              >
                {(item.avg_sentiment * 100).toFixed(0)}%
              </span>
            </div>
          ) : null}
          {senders.length > 0 ? (
            <div className="mt-1 space-y-0.5">
              {senders.map((s) => (
                <div key={s} className="flex items-center gap-2">
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: paletteHue(s, senders.indexOf(s)) }}
                  />
                  <span className="text-foreground">{s}</span>
                  <span className="ml-auto tabular-nums text-muted-foreground">
                    {item.per_sender[s]}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          <Tooltip.Arrow className="fill-card-elevated" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function UniqueToSender({
  items,
}: {
  items: EmojiFrequencyResponse["unique_to_sender"];
}) {
  // Group by sender so each row reads "X's emojis: 😍 🙏 🔥"
  const grouped = useMemo(() => {
    const m: Record<string, typeof items> = {};
    for (const u of items) {
      (m[u.sender] ??= []).push(u);
    }
    return m;
  }, [items]);

  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-primary" />
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Most distinctive emojis
        </p>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {Object.entries(grouped).map(([sender, list]) => (
          <div
            key={sender}
            className="rounded-lg border border-border/60 bg-card/40 p-3"
          >
            <p className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{sender}</span> uses
              these much more than the others
            </p>
            <ul className="mt-2 flex flex-wrap items-center gap-2">
              {list.slice(0, 8).map((u) => (
                <li
                  key={u.emoji}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/60 px-2 py-1 text-sm"
                  title={u.unicode_name ?? undefined}
                >
                  <span className="text-base leading-none">{u.emoji}</span>
                  <span className="text-[10px] tabular-nums text-muted-foreground">
                    {Math.round(u.share * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

// ===========================================================================
// Section 4 — Vocabulary richness
// ===========================================================================

function VocabSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<UniqueWordsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getUniqueWords(uploadId, { signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load vocabulary");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  const senders = data?.per_sender.map((s) => s.sender) ?? [];
  const senderColor = useMemo(
    () => Object.fromEntries(senders.map((s, i) => [s, paletteHue(s, i)])),
    [senders],
  );

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 03 · Voice
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Vocabulary richness
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Distinct vocabulary, distinctive phrasing, and how message length
          drifted over time.
        </p>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <VocabSkeleton />
      ) : data.per_sender.length === 0 ? (
        <EmptyState message="Not enough words yet to compute vocabulary stats." />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            {data.per_sender.map((p) => (
              <VocabCard
                key={p.sender}
                vocab={p}
                color={senderColor[p.sender] ?? "hsl(var(--primary))"}
              />
            ))}
          </div>
          <LengthOverTimeChart
            points={data.length_over_time}
            senders={senders}
            senderColor={senderColor}
          />
        </div>
      )}
    </article>
  );
}

function VocabCard({ vocab, color }: { vocab: SenderVocab; color: string }) {
  return (
    <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-display text-sm font-semibold">{vocab.sender}</h4>
        <span
          className="rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wider"
          style={{ borderColor: color, color }}
        >
          {(vocab.richness_score * 100).toFixed(1)}% richness
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
        <Stat label="Unique words" value={vocab.unique_count.toLocaleString()} />
        <Stat label="Total words" value={vocab.total_words.toLocaleString()} />
      </div>

      <p className="mt-4 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        Distinctive vocabulary
      </p>
      {vocab.distinctive_words.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Not enough words to surface distinctive phrasing yet.
        </p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {vocab.distinctive_words.map((w) => (
            <li
              key={w.word}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-card/40 px-2 py-0.5 text-[11px]"
              title={`Score ${(w.score * 100).toFixed(0)} · ${w.count} uses`}
            >
              <span
                className="h-1 w-1 rounded-full"
                style={{ background: color, opacity: 0.5 + 0.5 * w.score }}
              />
              <span className="text-foreground">{w.word}</span>
              <span className="text-muted-foreground tabular-nums">
                ×{w.count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/60 bg-card/30 p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="font-display text-base font-semibold tabular-nums">
        {value}
      </div>
    </div>
  );
}

function LengthOverTimeChart({
  points,
  senders,
  senderColor,
}: {
  points: MessageLengthPoint[];
  senders: string[];
  senderColor: Record<string, string>;
}) {
  // Recharts expects flat objects; flatten per_sender into top-level keys.
  const rows = useMemo(() => {
    return points.map((p) => {
      const row: Record<string, string | number> = { date: p.date };
      for (const s of senders) {
        const v = p.per_sender[s];
        if (v != null) row[s] = v;
      }
      return row;
    });
  }, [points, senders]);

  if (rows.length < 2) return null;

  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Average message length
        </p>
        <span className="text-[10px] text-muted-foreground">
          characters per message
        </span>
      </div>
      <div className="h-44 w-full">
        <ResponsiveContainer>
          <LineChart
            data={rows}
            margin={{ top: 4, right: 12, bottom: 0, left: -8 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="hsl(var(--border))"
              strokeOpacity={0.4}
              vertical={false}
            />
            <XAxis
              dataKey="date"
              stroke="hsl(var(--muted-foreground))"
              tick={{ fontSize: 10 }}
              tickFormatter={(d) => safeFormatDate(d as string, "MMM d")}
              tickLine={false}
              axisLine={false}
              minTickGap={28}
            />
            <YAxis
              stroke="hsl(var(--muted-foreground))"
              tick={{ fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            {senders.map((s) => (
              <Line
                key={s}
                type="monotone"
                dataKey={s}
                stroke={senderColor[s]}
                strokeWidth={2}
                dot={false}
                connectNulls
                isAnimationActive
                animationDuration={700}
              />
            ))}
            <RechartsTooltip
              contentStyle={{
                background: "hsl(var(--card-elevated))",
                border: "1px solid hsl(var(--border))",
                borderRadius: 12,
                fontSize: 11,
              }}
              labelFormatter={(d) => safeFormatDate(d as string, "MMM d, yyyy")}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ===========================================================================
// Section 5 — Common phrases
// ===========================================================================

function PhrasesSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<BigramsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getBigrams(uploadId, { topN: 24, signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load phrases");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
          Section 04 · Phrases
        </p>
        <h3 className="mt-0.5 font-display text-base font-semibold">
          Common &amp; shared phrases
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Frequent two-word combinations and the inside phrases used by
          everyone.
        </p>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <PhrasesSkeleton />
      ) : data.items.length === 0 ? (
        <EmptyState message="Not enough message volume to surface phrases yet." />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
          <PhraseCloud title="Top phrases" items={data.items} />
          <InsidePhrases items={data.inside_phrases} />
        </div>
      )}
    </article>
  );
}

function PhraseCloud({ title, items }: { title: string; items: BigramItem[] }) {
  const max = items[0]?.count ?? 1;
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <p className="mb-3 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {items.map((b) => {
          const t = b.count / max; // [0, 1]
          const padX = 0.6 + t * 0.5; // rem
          const padY = 0.25 + t * 0.25; // rem
          const fontSize = 0.75 + t * 0.4; // rem
          return (
            <span
              key={b.phrase}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border transition",
                "border-primary/30 bg-primary/10 text-primary",
                "hover:border-primary/60 hover:bg-primary/15",
              )}
              style={{
                padding: `${padY}rem ${padX}rem`,
                fontSize: `${fontSize}rem`,
                boxShadow: `0 0 ${4 + t * 12}px hsl(var(--primary) / ${0.05 + t * 0.2})`,
              }}
              title={`${b.count} uses`}
            >
              <span className="font-medium">{b.phrase}</span>
              <span className="text-[10px] tabular-nums opacity-70">
                ×{b.count}
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function InsidePhrases({ items }: { items: BigramItem[] }) {
  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
        <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Inside phrases
        </p>
        <p className="text-xs text-muted-foreground">
          We&apos;ll list phrases everyone leans on once we see enough overlap.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-border bg-card-elevated/30 p-4">
      <div className="flex items-center gap-2">
        <Layers className="h-3.5 w-3.5 text-accent" />
        <p className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Inside phrases
        </p>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Used at least 3 times by everyone — the closest thing to shared
        shorthand.
      </p>
      <ul className="mt-3 space-y-2">
        {items.map((b) => {
          const total =
            Object.values(b.per_sender).reduce((s, n) => s + n, 0) || 1;
          return (
            <li
              key={b.phrase}
              className="rounded-lg border border-border bg-card/40 p-3"
            >
              <div className="flex items-center justify-between">
                <span className="font-display text-sm font-semibold">
                  &ldquo;{b.phrase}&rdquo;
                </span>
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {b.count} total
                </span>
              </div>
              <div className="mt-2 flex h-1 w-full overflow-hidden rounded-full bg-muted">
                {Object.entries(b.per_sender).map(([s, n], i) => (
                  <span
                    key={s}
                    style={{
                      width: `${(n / total) * 100}%`,
                      background: paletteHue(s, i),
                    }}
                    title={`${s}: ${n}`}
                  />
                ))}
              </div>
              <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-muted-foreground">
                {Object.entries(b.per_sender).map(([s, n], i) => (
                  <li key={s} className="inline-flex items-center gap-1">
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ background: paletteHue(s, i) }}
                    />
                    <span className="text-foreground">{s}</span>
                    <span className="tabular-nums">×{n}</span>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ===========================================================================
// Section bonus — Late-night messages (small, lives below phrases)
// ===========================================================================

function LateNightSection({ uploadId }: { uploadId: string }) {
  const [data, setData] = useState<LateNightStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    getLateNightStats(uploadId, { signal: ctrl.signal })
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        if (_isAbort(e)) return;
        setError(e instanceof Error ? e.message : "Failed to load late-night stats");
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [uploadId]);

  return (
    <article className="surface-card p-5 animate-fade-up">
      <header className="mb-4 flex items-center gap-2">
        <Moon className="h-4 w-4 text-accent" />
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Section 05 · Witching hours
          </p>
          <h3 className="mt-0.5 font-display text-base font-semibold">
            Late-night messages
          </h3>
          <p className="text-xs text-muted-foreground">
            What gets said between 11 PM and 5 AM (UTC).
          </p>
        </div>
      </header>

      {error ? (
        <SectionError error={error} />
      ) : loading || !data ? (
        <LateNightSkeleton />
      ) : data.total_late_night === 0 ? (
        <EmptyState message="No late-night messages found." />
      ) : (
        <LateNightContent stats={data} />
      )}
    </article>
  );
}

function LateNightContent({ stats }: { stats: LateNightStats }) {
  const senders = Object.keys(stats.per_sender);
  const total = stats.total_late_night;
  const maxBucket = Math.max(1, ...stats.by_hour.map((h) => h.count));
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_2fr]">
      <div className="space-y-3">
        <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
          <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Of all messages
          </p>
          <p className="mt-1 font-display text-3xl font-semibold tabular-nums">
            {(stats.pct_of_total * 100).toFixed(1)}%
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {stats.total_late_night.toLocaleString()} late-night messages
          </p>
        </div>

        <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
          <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Hour distribution
          </p>
          <div className="mt-2 flex h-12 items-end gap-1">
            {stats.by_hour.map((h) => {
              const ratio = h.count / maxBucket;
              return (
                <div key={h.hour} className="flex flex-1 flex-col items-center gap-1">
                  <div
                    className="w-full rounded-sm bg-primary/70"
                    style={{ height: `${Math.max(4, ratio * 44)}px` }}
                    title={`${labelHour(h.hour)} · ${h.count}`}
                  />
                  <span className="text-[9px] text-muted-foreground">
                    {labelHour(h.hour)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card-elevated/40 p-4">
          <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            By sender
          </p>
          <ul className="mt-2 space-y-1.5">
            {senders.map((s, i) => (
              <li key={s} className="flex items-center gap-2 text-xs">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: paletteHue(s, i) }}
                />
                <span className="text-foreground">{s}</span>
                <span className="ml-auto tabular-nums text-muted-foreground">
                  {stats.per_sender[s]}
                </span>
                <span className="w-10 text-right text-[10px] text-muted-foreground">
                  {Math.round(((stats.per_sender[s] ?? 0) / total) * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div>
        <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          The most charged late-night moments
        </p>
        {stats.sample_messages.length === 0 ? (
          <EmptyState message="No standout late-night messages yet." compact />
        ) : (
          <ul className="space-y-2">
            {stats.sample_messages.map((m) => (
              <li
                key={m.msg_id}
                className="rounded-lg border border-border bg-card/40 p-3 text-xs"
              >
                <p className="line-clamp-3 italic text-foreground/90">
                  &ldquo;{m.content_preview}&rdquo;
                </p>
                <p className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                  <span>{m.sender}</span>
                  <span>·</span>
                  <span>{safeFormatDate(m.timestamp, "MMM d, yyyy h:mm a")}</span>
                  {m.sentiment_score != null ? (
                    <>
                      <span>·</span>
                      <span style={{ color: sentimentColor(m.sentiment_score) }}>
                        {(m.sentiment_score * 100).toFixed(0)}%
                      </span>
                    </>
                  ) : null}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// Skeletons + utility components
// ===========================================================================

function CloudSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
      <div className="flex h-72 items-center justify-center rounded-xl border border-border bg-card-elevated/30">
        <CloudFog className="h-6 w-6 text-muted-foreground/50" />
      </div>
      <div className="h-72 rounded-xl border border-border bg-card-elevated/30">
        <div className="shimmer h-full w-full rounded-xl" />
      </div>
    </div>
  );
}

function EmojiGridSkeleton() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-3">
      {Array.from({ length: 16 }).map((_, i) => (
        <div
          key={i}
          className="flex h-24 flex-col items-center justify-center gap-2 rounded-xl border border-border bg-card-elevated/40"
        >
          <span className="shimmer h-6 w-6 rounded" />
          <span className="shimmer h-3 w-10 rounded" />
        </div>
      ))}
    </div>
  );
}

function VocabSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div
            key={i}
            className="rounded-xl border border-border bg-card-elevated/40 p-4"
          >
            <span className="shimmer mb-3 block h-3 w-24 rounded" />
            <div className="grid grid-cols-2 gap-3">
              <span className="shimmer h-12 rounded" />
              <span className="shimmer h-12 rounded" />
            </div>
            <span className="shimmer mt-4 block h-3 w-32 rounded" />
            <span className="shimmer mt-2 block h-12 w-full rounded" />
          </div>
        ))}
      </div>
      <span className="shimmer block h-44 w-full rounded-xl" />
    </div>
  );
}

function PhrasesSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
      <span className="shimmer block h-48 rounded-xl" />
      <span className="shimmer block h-48 rounded-xl" />
    </div>
  );
}

function LateNightSkeleton() {
  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_2fr]">
      <span className="shimmer h-72 rounded-xl" />
      <span className="shimmer h-72 rounded-xl" />
    </div>
  );
}

function SectionError({ error }: { error: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
      <AlertCircle className="h-4 w-4 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">
          Couldn&apos;t load this section
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{error}</p>
      </div>
    </div>
  );
}

function EmptyState({
  message,
  compact,
}: {
  message: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-card-elevated/20 px-4 text-center",
        compact ? "h-20" : "h-40",
      )}
    >
      <Type className="h-4 w-4 text-muted-foreground" />
      <p className="text-xs text-muted-foreground">{message}</p>
    </div>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

/** Deterministic per-name hue + index nudge so sender colors stay stable
 *  across panels without colliding when two names hash to similar hues. */
function paletteHue(name: string, index: number): string {
  const base = (stringToHue(name) + index * 47) % 360;
  return `hsl(${base} 70% 60%)`;
}

function stringToHue(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}

function withAlpha(color: string, alpha: number): string {
  // Crude "color → rgba" — only used for soft text shadows, so the rough
  // result is fine. Accepts hsl(...) and #hex.
  if (color.startsWith("hsl(")) {
    return color.replace(")", ` / ${alpha})`);
  }
  if (color.startsWith("#") && color.length === 7) {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}

function sentimentColor(v: number): string {
  // Same warm/cool palette as the EmotionTimeline calendar.
  if (v >= 0.05) return "#b76e05";
  if (v <= -0.05) return "#4338ca";
  return "hsl(var(--muted-foreground))";
}

function labelHour(h: number): string {
  if (h === 0) return "12a";
  if (h === 12) return "12p";
  if (h < 12) return `${h}a`;
  return `${h - 12}p`;
}

/** True for either AbortController or axios-style cancellation errors. Both
 *  can fire when Strict Mode unmounts a section's effect mid-flight. */
function _isAbort(e: unknown): boolean {
  if (!(e instanceof Error)) return false;
  return e.name === "CanceledError" || e.name === "AbortError";
}
