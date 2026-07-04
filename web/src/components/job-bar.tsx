"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, X, CheckCircle2, AlertTriangle } from "lucide-react";
import { api, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";

// Fires when a pipeline job (scrape/discover/draft/send) finishes, so the
// active page can refetch its data. Pages listen for "pipeline:done".
export const PIPELINE_DONE = "pipeline:done";

export function JobBar() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dismissed, setDismissed] = useState("");
  const runningIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.jobs();
        if (!alive) return;
        // Detect running -> finished transitions and notify the page.
        const nowRunning = new Set(
          data.jobs.filter((j) => j.status === "running").map((j) => j.id),
        );
        for (const id of runningIds.current) {
          if (!nowRunning.has(id)) {
            window.dispatchEvent(new CustomEvent(PIPELINE_DONE));
            break;
          }
        }
        runningIds.current = nowRunning;
        setJobs(data.jobs);
      } catch {
        /* backend momentarily unreachable */
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const running = jobs.filter((j) => j.status === "running");
  const lastErr = [...jobs].reverse().find((j) => j.status === "error");
  const recent = jobs[jobs.length - 1];

  let content: React.ReactNode = null;
  let tone: "run" | "ok" | "err" = "run";
  let key = "";

  if (running.length) {
    tone = "run";
    key = "run:" + running.map((j) => j.id).join(",");
    const j = running[running.length - 1];
    content = (
      <>
        <Loader2 className="size-4 shrink-0 animate-spin" />
        <span className="min-w-0 flex-1 truncate">
          <b className="font-semibold">
            {running.map((r) => r.label).join(" + ")}…
          </b>
          {j.last && (
            <span className="text-background/60"> — {j.last}</span>
          )}
        </span>
      </>
    );
  } else if (lastErr) {
    tone = "err";
    key = "err:" + lastErr.id;
    content = (
      <>
        <AlertTriangle className="size-4 shrink-0 text-[#f2a99c]" />
        <span className="min-w-0 flex-1 truncate">
          <b className="font-semibold text-[#f2a99c]">{lastErr.label} stopped:</b>{" "}
          {lastErr.error}
        </span>
      </>
    );
  } else if (recent && recent.status === "done") {
    tone = "ok";
    key = "done:" + recent.id;
    content = (
      <>
        <CheckCircle2 className="size-4 shrink-0 text-[#8fd6a5]" />
        <span className="min-w-0 flex-1 truncate">
          <b className="font-semibold text-[#8fd6a5]">{recent.label} — done.</b>
        </span>
      </>
    );
  }

  const visible = !!content && dismissed !== key;
  if (!visible) return null;

  return (
    <div className="fixed inset-x-0 bottom-5 z-50 flex justify-center px-4">
      <div
        className={cn(
          "flex w-full max-w-[720px] items-center gap-3 rounded-xl border px-4 py-3 text-sm shadow-2xl",
          "border-white/10 bg-[#16130d] text-[#ece4d3]",
        )}
      >
        {content}
        {tone !== "run" && (
          <button
            aria-label="Dismiss"
            onClick={() => setDismissed(key)}
            className="shrink-0 rounded-md p-1 text-[#ece4d3]/60 transition-colors hover:text-[#ece4d3]"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}
