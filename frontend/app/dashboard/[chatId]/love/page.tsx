"use client";

import { LoveLanguages } from "@/components/dashboard/LoveLanguages";
import { ReadyGate } from "@/components/dashboard/ReadyGate";

export default function LovePage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <LoveLanguages uploadId={params.chatId} />
    </ReadyGate>
  );
}
