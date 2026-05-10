"use client";

import { ReadyGate } from "@/components/dashboard/ReadyGate";
import { WordAnalytics } from "@/components/dashboard/WordAnalytics";

export default function WordsPage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <WordAnalytics uploadId={params.chatId} />
    </ReadyGate>
  );
}
