"use client";

import { EmotionTimeline } from "@/components/dashboard/EmotionTimeline";
import { ReadyGate } from "@/components/dashboard/ReadyGate";

export default function TimelinePage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <EmotionTimeline uploadId={params.chatId} />
    </ReadyGate>
  );
}
