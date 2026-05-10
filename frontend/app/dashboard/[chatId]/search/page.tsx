"use client";

import { ReadyGate } from "@/components/dashboard/ReadyGate";
import { SearchModule } from "@/components/dashboard/SearchModule";

export default function SearchPage({
  params,
}: {
  params: { chatId: string };
}) {
  return (
    <ReadyGate chatId={params.chatId}>
      <SearchModule uploadId={params.chatId} />
    </ReadyGate>
  );
}
