"use client";

import { card } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { OpsQueue } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function seconds(value: number | null): string {
  if (value === null) return "–";
  if (value < 60) return `${value.toFixed(1)} s`;
  if (value < 3600) return `${Math.round(value / 60)} min`;
  return `${(value / 3600).toFixed(1)} h`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className={card}>
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-zinc-500">{hint}</p>}
    </div>
  );
}

/** Queue health for operators (PRD §18): refreshes every 5 s. */
export default function OpsPage() {
  const { data, error } = useApi<OpsQueue>("/ops/queue", 5000);

  if (error?.status === 403) {
    return <p className="text-sm text-zinc-600">This page is for administrators.</p>;
  }
  const count = (status: "queued" | "running") =>
    data?.depth.filter((row) => row.status === status).reduce((sum, row) => sum + row.count, 0) ?? 0;
  const rate = data?.last_24h.success_rate;
  const llm = data?.llm_today;

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Ops</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">The job queue, workers&apos; results and today&apos;s AI budget.</p>
      {error && <p role="alert" className="mt-6 text-sm text-red-700">{error.message}</p>}
      {data && (
        <div className="mt-6 space-y-8">
          <section aria-label="Queue" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Queued" value={String(count("queued"))} />
            <Stat label="Running" value={String(count("running"))} />
            <Stat
              label="Oldest due job waiting"
              value={seconds(data.oldest_queued_age_s)}
              hint="How late the most overdue job is. Growing means workers can't keep up."
            />
            <Stat
              label="Success, last 24 h"
              value={rate === null || rate === undefined ? "–" : `${(rate * 100).toFixed(1)}%`}
              hint={`${data.last_24h.succeeded} succeeded, ${data.last_24h.dead} dead`}
            />
            <Stat label="Job time p50" value={seconds(data.last_24h.p50_s)} hint="Claim to finish, last 24 h" />
            <Stat label="Job time p95" value={seconds(data.last_24h.p95_s)} />
            <Stat
              label="AI calls today (UTC)"
              value={llm ? `${llm.calls} / ${llm.limit}` : "–"}
              hint={llm ? `${llm.input_tokens.toLocaleString()} input, ${llm.output_tokens.toLocaleString()} output tokens` : undefined}
            />
            <Stat
              label="Retention"
              value={`${data.retention.days} days`}
              hint={data.retention.last_run_at ? `Last run ${dateTime(data.retention.last_run_at)}` : "Hasn't run yet"}
            />
          </section>

          <section>
            <h2 className="font-semibold">Active jobs by type</h2>
            {data.depth.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500">The queue is empty.</p>
            ) : (
              <table className="mt-2 w-full max-w-md text-left text-sm">
                <thead className="text-zinc-500">
                  <tr><th className="py-1 font-medium">Type</th><th className="font-medium">Status</th><th className="text-right font-medium">Jobs</th></tr>
                </thead>
                <tbody className="tabular-nums">
                  {data.depth.map((row) => (
                    <tr key={`${row.type}-${row.status}`} className="border-t border-zinc-100 dark:border-zinc-800">
                      <td className="py-1 font-mono text-xs">{row.type}</td>
                      <td>{row.status}</td>
                      <td className="text-right">{row.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section>
            <h2 className="font-semibold">Dead jobs</h2>
            <p className="text-sm text-zinc-500">Out of retries, or failed in a way retrying can&apos;t fix. The latest 20.</p>
            {data.dead_jobs.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500">None.</p>
            ) : (
              <ul className="mt-2 divide-y divide-zinc-200 rounded-lg border border-zinc-200 text-sm dark:divide-zinc-800 dark:border-zinc-800">
                {data.dead_jobs.map((job) => (
                  <li key={job.id} className="flex flex-wrap gap-x-4 gap-y-1 bg-white p-3 dark:bg-zinc-950">
                    <span className="font-mono text-xs">#{job.id} {job.type}</span>
                    <span className="min-w-0 flex-1 break-words text-zinc-600 dark:text-zinc-400">{job.last_error ?? "no error recorded"}</span>
                    <span className="text-xs text-zinc-500">{job.finished_at ? dateTime(job.finished_at) : ""}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </>
  );
}
