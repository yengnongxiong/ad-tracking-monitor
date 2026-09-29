"use client";

import type { RunDetail, RunSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function Shot({ run, label }: { run: RunSummary | undefined; label: string }) {
  const { data } = useApi<RunDetail>(run ? `/runs/${run.id}` : null);
  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 text-sm font-medium">{label}</figcaption>
      {data?.screenshot_url ? (
        // Presigned MinIO/S3 link (10 minutes); a plain <img> avoids proxying it through Next.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={data.screenshot_url}
          alt={`${label} screenshot of the page`}
          className="w-full rounded-md border border-zinc-200 dark:border-zinc-800"
        />
      ) : (
        <div className="flex h-40 items-center justify-center rounded-md border border-dashed border-zinc-300 text-sm text-zinc-500 dark:border-zinc-700">
          {run ? "No screenshot (the page didn't load)" : "No check yet"}
        </div>
      )}
    </figure>
  );
}

export function Screenshots({ runs }: { runs: RunSummary[] }) {
  const latest = (device: RunSummary["device"]) =>
    runs.find((run) => run.device === device && run.status === "completed");
  return (
    <div className="grid gap-6 sm:grid-cols-[1fr_2fr]">
      <Shot run={latest("mobile")} label="Mobile (Pixel 7)" />
      <Shot run={latest("desktop")} label="Desktop (1440 × 900)" />
    </div>
  );
}
