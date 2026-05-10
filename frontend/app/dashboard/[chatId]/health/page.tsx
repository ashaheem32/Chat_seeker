"use client";

import { HealthScore } from "@/components/dashboard/HealthScore";
import { ReadyGate } from "@/components/dashboard/ReadyGate";

export default function HealthPage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <HealthScore uploadId={params.chatId} />
    </ReadyGate>
  );
}
