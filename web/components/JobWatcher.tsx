"use client";

import { useEffect } from "react";

import type { Job } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/** Polls a job every 3 s (PRD §18) and calls onDone once it has finished. */
export function JobWatcher({ jobId, onDone }: { jobId: number; onDone: () => void }) {
  const { data: job } = useApi<Job>(`/jobs/${jobId}`, 3000);
  const finished = job?.status === "succeeded" || job?.status === "dead";

  useEffect(() => {
    if (finished) onDone();
  }, [finished, onDone]);

  return (
    <div role="status" className="flex items-center gap-3 rounded-lg bg-sky-50 p-4 text-sm text-sky-900 dark:bg-sky-950 dark:text-sky-100">
      <span className="h-3 w-3 animate-pulse rounded-full bg-sky-500" aria-hidden="true" />
      {job?.status === "running"
        ? "Checking your page on a phone and a desktop… this takes about 20 seconds."
        : job?.reason === "confirm"
          ? "Double-checking a problem we just found before we alert you…"
          : "Check queued. It will start in a moment."}
    </div>
  );
}
