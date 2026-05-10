"use client";

/**
 * DropZone — premium drag-and-drop file picker for the landing page.
 *
 * Visual:
 *   - Large dashed border that warms to indigo on drag-over
 *   - Soft inner glow during drag
 *   - File preview card after selection (name, size, detected platform pill)
 *   - "Or paste raw chat text" toggle that swaps the surface for a textarea
 *   - Animated multi-stage progress when a file is being processed
 *   - Privacy reassurance footer
 *
 * Behavior:
 *   The component is presentational only. Caller passes onFile / onText
 *   callbacks and the current upload-flow state (stage, progress, etc.)
 *   so this stays decoupled from useUploadFlow.
 *
 * File constraints:
 *   .txt / .json / .csv / .zip up to 50 MB. Anything else is rejected
 *   with a toast and the dropzone resets.
 */

import { useCallback, useMemo, useState } from "react";
import { useDropzone, type FileRejection } from "react-dropzone";
import {
  ArrowRight,
  Clipboard,
  FileText,
  Loader2,
  ShieldCheck,
  Sparkles,
  Upload,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge, type StatusKind } from "@/components/ui/StatusBadge";
import {
  PLATFORM_INFO,
  type ClientStage,
  type Platform,
} from "@/lib/types";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const MAX_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB — matches backend cap
const ACCEPT = {
  "text/plain": [".txt"],
  "application/json": [".json"],
  "text/csv": [".csv"],
  "application/zip": [".zip"],
};

interface DropZoneProps {
  onFile: (file: File) => void;
  onText: (text: string) => void;
  disabled?: boolean;
  /** Current upload-flow stage from useUploadFlow. */
  stage?: ClientStage;
  /** 0-100 progress for the active stage. */
  progress?: number;
  /** Free-form status detail rendered under the progress bar. */
  message?: string;
  /** Detected platform — drives the badge that shows after upload kicks off. */
  detectedPlatform?: Platform | null;
}

const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "whatsapp", label: "WhatsApp" },
  { id: "telegram", label: "Telegram" },
  { id: "instagram", label: "Instagram" },
  { id: "facebook", label: "Messenger" },
  { id: "csv", label: "CSV" },
];

/** Stage → human label + StatusBadge kind for the progress UI. */
const STAGE_COPY: Record<ClientStage, { label: string; status: StatusKind }> = {
  idle: { label: "", status: "pending" },
  detecting: { label: "Detecting format", status: "processing" },
  queued: { label: "Queued", status: "queued" },
  parsing: { label: "Parsing messages", status: "parsing" },
  persisting: { label: "Saving to your library", status: "persisting" },
  ai_enhancing: { label: "Running AI analysis", status: "nlp_processing" },
  ready: { label: "Ready", status: "ready" },
  failed: { label: "Failed", status: "failed" },
};

export function DropZone({
  onFile,
  onText,
  disabled,
  stage = "idle",
  progress = 0,
  message,
  detectedPlatform,
}: DropZoneProps) {
  const [pasteMode, setPasteMode] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const isWorking = stage !== "idle" && stage !== "ready" && stage !== "failed";

  const onDrop = useCallback(
    (accepted: File[], rejected: FileRejection[]) => {
      if (rejected.length > 0) {
        const reason = rejected[0]?.errors?.[0];
        if (reason?.code === "file-too-large") {
          toast.error("File too large", "Max 50 MB. Try splitting a smaller window of the chat.");
        } else if (reason?.code === "file-invalid-type") {
          toast.error(
            "Unsupported file",
            "Drop a .txt, .json, .csv, or .zip from your chat platform's export.",
          );
        } else {
          toast.error("Couldn't accept that file", reason?.message);
        }
        return;
      }
      const file = accepted[0];
      if (!file) return;
      setSelectedFile(file);
      onFile(file);
    },
    [onFile],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    accept: ACCEPT,
    maxSize: MAX_SIZE_BYTES,
    maxFiles: 1,
    disabled: disabled || isWorking,
    noClick: pasteMode,
  });

  const reset = useCallback(() => {
    setSelectedFile(null);
    setPasteText("");
    setPasteMode(false);
  }, []);

  return (
    <div className="space-y-5">
      {/* Platform marker strip */}
      <PlatformStrip />

      {!pasteMode ? (
        <DropSurface
          rootProps={getRootProps()}
          inputProps={getInputProps()}
          isDragActive={isDragActive}
          isWorking={isWorking}
          disabled={disabled}
          stage={stage}
          progress={progress}
          message={message}
          file={selectedFile}
          detectedPlatform={detectedPlatform}
          onPickFile={open}
          onClear={reset}
        />
      ) : (
        <PasteSurface
          value={pasteText}
          onChange={setPasteText}
          onSubmit={() => pasteText.trim() && onText(pasteText.trim())}
          onCancel={reset}
          disabled={disabled || isWorking}
        />
      )}

      {/* Secondary action — only when not actively working */}
      {!isWorking && stage !== "ready" ? (
        <div className="flex items-center justify-center gap-1 text-xs text-muted-foreground">
          <span>Or</span>
          <button
            type="button"
            onClick={() => {
              if (disabled) return;
              setPasteMode((m) => !m);
            }}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-foreground/90 transition hover:bg-secondary/40 hover:text-primary"
          >
            <Clipboard className="h-3.5 w-3.5" />
            {pasteMode ? "back to file upload" : "paste raw chat text"}
          </button>
        </div>
      ) : null}

      {/* Privacy footer */}
      <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
        <ShieldCheck className="h-3.5 w-3.5 text-success" />
        <span>Your data is encrypted in transit and never used for model training.</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PlatformStrip() {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2">
      <span className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        Works with
      </span>
      {PLATFORMS.map((p) => {
        const meta = PLATFORM_INFO[p.id];
        return (
          <div
            key={p.id}
            className={cn(
              "group flex items-center gap-1.5 rounded-full border border-border/60",
              "bg-card/40 px-2.5 py-1 text-xs text-muted-foreground",
              "transition hover:border-primary/40 hover:text-foreground",
            )}
          >
            <span
              className="inline-block h-1.5 w-1.5 rounded-full transition group-hover:scale-110"
              style={{ background: meta.color, boxShadow: `0 0 8px ${meta.color}66` }}
            />
            {p.label}
          </div>
        );
      })}
    </div>
  );
}

interface DropSurfaceProps {
  rootProps: ReturnType<ReturnType<typeof useDropzone>["getRootProps"]>;
  inputProps: ReturnType<ReturnType<typeof useDropzone>["getInputProps"]>;
  isDragActive: boolean;
  isWorking: boolean;
  disabled?: boolean;
  stage: ClientStage;
  progress: number;
  message?: string;
  file: File | null;
  detectedPlatform?: Platform | null;
  onPickFile: () => void;
  onClear: () => void;
}

function DropSurface({
  rootProps,
  inputProps,
  isDragActive,
  isWorking,
  disabled,
  stage,
  progress,
  message,
  file,
  detectedPlatform,
  onPickFile,
  onClear,
}: DropSurfaceProps) {
  const stageCopy = STAGE_COPY[stage] ?? STAGE_COPY.idle;

  return (
    <div
      {...rootProps}
      className={cn(
        "group relative overflow-hidden rounded-2xl border-2 border-dashed bg-card",
        "px-6 py-12 text-center transition-all duration-300",
        // Idle / drag states
        !isWorking && !isDragActive && "border-border/70 hover:border-primary/50",
        !isWorking && isDragActive && "border-primary bg-primary/5 shadow-inner-glow",
        // Working state
        isWorking && "border-primary/40 bg-card-elevated cursor-default",
        // Disabled
        disabled && "pointer-events-none opacity-60",
      )}
    >
      {/* Background glow on drag-over */}
      <div
        aria-hidden
        className={cn(
          "absolute inset-0 -z-10 opacity-0 transition-opacity duration-500",
          (isDragActive || isWorking) && "opacity-100",
        )}
      >
        <div className="absolute inset-x-0 top-0 h-32 bg-indigo-glow" />
        <div className="absolute inset-0 bg-violet-glow opacity-50" />
      </div>

      <input {...inputProps} />

      {/* IDLE — empty zone */}
      {stage === "idle" && !file ? (
        <div className="flex flex-col items-center gap-4 animate-fade-up">
          <div
            className={cn(
              "flex h-14 w-14 items-center justify-center rounded-2xl",
              "border border-border bg-card-elevated",
              "transition group-hover:border-primary/40 group-hover:shadow-glow",
              isDragActive && "border-primary shadow-glow",
            )}
          >
            <Upload
              className={cn(
                "h-6 w-6 text-muted-foreground transition",
                isDragActive && "text-primary",
              )}
            />
          </div>

          <div>
            <p className="font-display text-lg text-foreground">
              {isDragActive ? "Release to upload" : "Drop your chat export here"}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              .txt · .json · .csv · .zip — up to 50 MB
            </p>
          </div>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPickFile();
            }}
            className="mt-1 inline-flex items-center gap-1.5 text-sm font-medium text-primary transition hover:text-primary-glow"
          >
            <FileText className="h-4 w-4" />
            choose a file
          </button>
        </div>
      ) : null}

      {/* WORKING — file selected, pipeline running */}
      {(isWorking || stage === "ready") && (file || detectedPlatform) ? (
        <FileProgress
          file={file}
          detectedPlatform={detectedPlatform}
          stage={stage}
          stageLabel={stageCopy.label}
          stageStatus={stageCopy.status}
          progress={progress}
          message={message}
          onClear={onClear}
        />
      ) : null}
    </div>
  );
}

function FileProgress({
  file,
  detectedPlatform,
  stage,
  stageLabel,
  stageStatus,
  progress,
  message,
  onClear,
}: {
  file: File | null;
  detectedPlatform?: Platform | null;
  stage: ClientStage;
  stageLabel: string;
  stageStatus: StatusKind;
  progress: number;
  message?: string;
  onClear: () => void;
}) {
  const platformMeta = useMemo(
    () => (detectedPlatform ? PLATFORM_INFO[detectedPlatform] : null),
    [detectedPlatform],
  );

  return (
    <div className="flex flex-col items-stretch gap-5 animate-fade-up">
      <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card-elevated/80 p-3 text-left">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
            {stage === "ready" ? (
              <Sparkles className="h-4 w-4 text-primary" />
            ) : (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            )}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">
              {file?.name ?? "Pasted text"}
            </p>
            <p className="text-xs text-muted-foreground">
              {file ? formatBytes(file.size) : ""}
              {platformMeta ? (
                <>
                  {file ? " · " : ""}
                  <span style={{ color: platformMeta.color }}>{platformMeta.name}</span>
                </>
              ) : null}
            </p>
          </div>
        </div>

        {stage === "ready" ? (
          <Button variant="ghost" size="sm" onClick={onClear}>
            <X className="h-4 w-4" />
          </Button>
        ) : null}
      </div>

      {/* Progress bar */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <StatusBadge status={stageStatus} label={stageLabel} />
          </div>
          <span className="font-mono text-muted-foreground">
            {Math.round(progress)}%
          </span>
        </div>
        <div className="relative h-1.5 overflow-hidden rounded-full bg-secondary">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500 ease-out",
              "bg-gradient-to-r from-primary via-primary-glow to-accent",
              "shadow-[0_0_12px_hsl(var(--primary)/0.6)]",
            )}
            style={{ width: `${Math.max(2, Math.min(100, progress))}%` }}
          />
        </div>
        {message ? (
          <p className="text-xs text-muted-foreground">{message}</p>
        ) : null}
      </div>
    </div>
  );
}

function PasteSurface({
  value,
  onChange,
  onSubmit,
  onCancel,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-3 animate-fade-up">
      <div className="surface-card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
          <Clipboard className="h-3.5 w-3.5" />
          Paste any WhatsApp .txt or platform JSON below
        </div>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={
            "12/01/2024, 10:23 AM - Alice: Hey!\n12/01/2024, 10:25 AM - Bob: Hello\n\n…or paste a Telegram / Instagram / Facebook JSON export"
          }
          disabled={disabled}
          className={cn(
            "h-56 w-full resize-y bg-transparent p-4 font-mono text-xs leading-relaxed",
            "text-foreground placeholder:text-muted-foreground/70",
            "focus:outline-none disabled:opacity-60",
          )}
        />
      </div>

      <div className="flex gap-2">
        <Button
          className="flex-1 bg-primary text-primary-foreground hover:bg-primary/90"
          onClick={onSubmit}
          disabled={disabled || !value.trim()}
        >
          Convert
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
        <Button variant="outline" onClick={onCancel} disabled={disabled}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
