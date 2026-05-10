"use client";

import { AlertCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export function ErrorCard({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/40 bg-destructive/5">
      <CardContent className="space-y-3 py-6">
        <div className="flex items-center gap-2 text-destructive">
          <AlertCircle className="h-5 w-5" />
          <p className="font-medium">Conversion failed</p>
        </div>
        <p className="text-sm text-destructive">{error}</p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}
