"use client";

import { MoodCalendarSection } from "@/components/dashboard/EmotionTimeline";
import { ReadyGate } from "@/components/dashboard/ReadyGate";

export default function CalendarPage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <MoodCalendarSection uploadId={params.chatId} />
    </ReadyGate>
  );
}
