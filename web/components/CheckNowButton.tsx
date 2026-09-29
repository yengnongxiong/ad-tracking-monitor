"use client";

import { useState } from "react";

import { button } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** Starts a check (or joins the one already queued) and reports back the job id. */
export function CheckNowButton({
  siteId,
  busy,
  onStarted,
}: {
  siteId: string;
  busy: boolean;
  onStarted: (jobId: number) => void;
}) {
  const [message, setMessage] = useState<string | null>(null);

  async function checkNow() {
    setMessage(null);
    try {
      const { job_id } = await api<{ job_id: number }>(`/sites/${siteId}/check-now`, { method: "POST" });
      onStarted(job_id);
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Couldn't start a check.");
    }
  }

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <button onClick={checkNow} disabled={busy} className={button.secondary}>
        {busy ? "Checking…" : "Check now"}
      </button>
      {message && (
        <span role="alert" className="max-w-56 text-right text-xs text-red-700 dark:text-red-300">
          {message}
        </span>
      )}
    </span>
  );
}
