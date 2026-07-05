"use client";

import { TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

// Shown when a /py/* request fails outright (api.ts throws an error with
// name "BackendDown"), i.e. the Flask process isn't running.
export function BackendDownBanner({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-center gap-3 rounded-xl border border-warn/40 bg-warn-soft px-4 py-3 text-sm text-warn"
    >
      <TriangleAlert className="size-4 shrink-0" />
      <p className="min-w-0 flex-1">
        Can&apos;t reach the local backend — is{" "}
        <code className="font-mono">python run_web.py</code> running?
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
