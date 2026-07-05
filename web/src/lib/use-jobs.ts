"use client";

import { useEffect, useState } from "react";
import { api, type Job } from "@/lib/api";

// Polls /py/jobs every 2s and returns the newest running job whose kind is
// in `kinds`, else null. Lets pages disable their trigger buttons while an
// identically-kinded job is already in flight.
export function useRunningJob(kinds: string[]): Job | null {
  const [job, setJob] = useState<Job | null>(null);
  // Depend on the joined string so callers can pass array literals without
  // re-subscribing every render.
  const key = kinds.join(",");

  useEffect(() => {
    const wanted = new Set(key.split(","));
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.jobs();
        if (!alive) return;
        // Jobs are ordered oldest -> newest, so scan from the end.
        const match = [...data.jobs]
          .reverse()
          .find((j) => j.status === "running" && wanted.has(j.kind));
        setJob(match ?? null);
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
  }, [key]);

  return job;
}
