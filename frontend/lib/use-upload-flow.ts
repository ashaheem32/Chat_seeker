"use client";

/**
 * useUploadFlow - state machine for the converter page.
 *
 * The hook abstracts the orchestration so the page component can stay
 * declarative: pass it a File or pasted text, it transitions through
 * idle → parsing → ready (or → failed), exposes WS-driven progress along
 * the way, and surfaces the final UCJ + AI insight when complete.
 *
 * Why a hook (not Zustand)?
 *   The state is local to the converter page - nothing else in the app
 *   needs to read it. A hook keeps the API ergonomic and avoids paying
 *   the bundle cost for a global store we don't otherwise need.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getUCJ,
  subscribeToProgress,
  uploadChat,
  uploadPastedText,
} from "@/lib/api";
import type {
  ClientStage,
  Platform,
  UCJFile,
  UploadProgressEvent,
  UploadResponse,
} from "@/lib/types";

export interface UploadFlowState {
  stage: ClientStage;
  progress: number;
  message: string;
  uploadId: string | null;
  filename: string;
  detectedPlatform: Platform | null;
  detectionConfidence: number;
  detectionReason: string;
  uploadResponse: UploadResponse | null;
  ucj: UCJFile | null;
  error: string | null;
  /** Number of preview messages we cap fetching at - keeps the page snappy. */
  previewMessageLimit: number;
}

const INITIAL: UploadFlowState = {
  stage: "idle",
  progress: 0,
  message: "",
  uploadId: null,
  filename: "",
  detectedPlatform: null,
  detectionConfidence: 0,
  detectionReason: "",
  uploadResponse: null,
  ucj: null,
  error: null,
  previewMessageLimit: 200,
};

export interface UploadFlow {
  state: UploadFlowState;
  uploadFile: (file: File) => Promise<void>;
  uploadText: (text: string, filename?: string) => Promise<void>;
  reset: () => void;
}

export function useUploadFlow(): UploadFlow {
  const [state, setState] = useState<UploadFlowState>(INITIAL);

  // Keep refs to teardown handlers + the active uploadId so a fast reset
  // mid-flight doesn't leave a WebSocket dangling.
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cleanup = useCallback(() => {
    wsCleanupRef.current?.();
    wsCleanupRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  // Always tear down on unmount.
  useEffect(() => cleanup, [cleanup]);

  const reset = useCallback(() => {
    cleanup();
    setState(INITIAL);
  }, [cleanup]);

  /**
   * Internal: drive the flow end-to-end given a function that produces an
   * UploadResponse. Used by both file and text uploads.
   */
  const run = useCallback(
    async (
      filename: string,
      doUpload: (signal: AbortSignal) => Promise<UploadResponse>,
    ) => {
      cleanup();

      const abort = new AbortController();
      abortRef.current = abort;

      setState({
        ...INITIAL,
        filename,
        stage: "detecting",
        progress: 5,
        message: "Detecting platform…",
      });

      try {
        // Kick off the upload. The backend publishes progress events to its
        // in-process broker; we subscribe via WS on the *next tick* once we
        // have an upload_id from the response.
        //
        // Note: on this codepath the response is the terminal "ready" reply -
        // by the time it returns, parsing + persistence is done. The WS feed
        // is most useful for the (usually short) parsing window during
        // long-running large uploads.
        const response = await doUpload(abort.signal);

        // Subscribe retroactively - if there are queued events still buffered
        // on the broker the cached "latest" status will fire immediately so
        // the UI reflects whatever stage the backend is in.
        wsCleanupRef.current = subscribeToProgress(
          response.upload_id,
          (event: UploadProgressEvent) => {
            setState((s) => ({
              ...s,
              uploadId: response.upload_id,
              progress: Math.max(s.progress, event.progress * 100),
              message: event.message,
              // Don't downgrade out of `ready` if a late event lands.
              stage: s.stage === "ready" ? "ready" : event.stage,
              error: event.stage === "failed" ? event.message : s.error,
            }));
          },
        );

        const detected = isPlatform(response.detected_platform)
          ? response.detected_platform
          : "unknown";

        setState((s) => ({
          ...s,
          uploadId: response.upload_id,
          filename: response.filename,
          detectedPlatform: detected,
          detectionConfidence: response.detection_confidence,
          detectionReason: response.detection_reason,
          uploadResponse: response,
          stage: "parsing",
          progress: Math.max(s.progress, 70),
          message: `Detected ${response.detected_platform}`,
        }));

        // Pull the (preview-limited) UCJ so we can render meta/messages/AI.
        const ucj = await getUCJ(response.upload_id, INITIAL.previewMessageLimit);

        setState((s) => ({
          ...s,
          ucj,
          stage: "ready",
          progress: 100,
          message: "Conversion complete",
          // If the AI block already came back as part of meta, surface its
          // status in the message line so the user knows it's there.
          ...(ucj.meta.ai_analysis
            ? { message: "AI analysis available" }
            : {}),
        }));
      } catch (e) {
        if (abort.signal.aborted) return; // user reset; not an error
        const error = e instanceof Error ? e.message : "Upload failed";
        setState((s) => ({
          ...s,
          stage: "failed",
          error,
          message: error,
        }));
      } finally {
        abortRef.current = null;
      }
    },
    [cleanup],
  );

  const uploadFile = useCallback(
    (file: File) =>
      run(file.name, (signal) => uploadChat(file, { signal })),
    [run],
  );

  const uploadText = useCallback(
    (text: string, filename = "pasted_chat.txt") =>
      run(filename, (signal) => uploadPastedText(text, filename, { signal })),
    [run],
  );

  return { state, uploadFile, uploadText, reset };
}

function isPlatform(value: string): value is Platform {
  return ["whatsapp", "telegram", "instagram", "facebook", "csv", "unknown"].includes(
    value,
  );
}
