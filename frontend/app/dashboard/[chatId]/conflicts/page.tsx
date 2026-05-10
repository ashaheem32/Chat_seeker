"use client";

import { ConflictAnalysis } from "@/components/dashboard/ConflictAnalysis";
import { ReadyGate } from "@/components/dashboard/ReadyGate";

export default function ConflictsPage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <ConflictAnalysis uploadId={params.chatId} />
    </ReadyGate>
  );
}
