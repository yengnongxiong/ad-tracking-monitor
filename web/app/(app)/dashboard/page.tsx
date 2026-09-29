"use client";

import Link from "next/link";

import { CheckNowButton } from "@/components/CheckNowButton";
import { StatusDot } from "@/components/Status";
import { button } from "@/components/ui";
import { columnStatus, STATUS_COLUMNS } from "@/lib/checks";
import { timeFrom } from "@/lib/format";
import type { Site } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export default function DashboardPage() {
  // Refresh every 5 s so statuses update as checks finish (checks take about 20 s).
  const { data: sites, error, reload } = useApi<Site[]>("/sites", 5000);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your landing pages</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            We load each page on a phone and a desktop, and email you when something breaks.
          </p>
        </div>
        <Link href="/sites/new" className={button.primary}>
          Add a page
        </Link>
      </div>

      {error && <p role="alert" className="mt-6 text-sm text-red-700">{error.message}</p>}
      {sites && sites.length === 0 && (
        <div className="mt-10 rounded-lg border border-dashed border-zinc-300 p-10 text-center dark:border-zinc-700">
          <p className="font-medium">No pages yet</p>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Add the landing page your ads send people to. The first check starts right away.
          </p>
        </div>
      )}

      {sites && sites.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full min-w-[860px] text-sm">
            <caption className="sr-only">Monitored pages and the status of each check</caption>
            <thead className="bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Page</th>
                {STATUS_COLUMNS.map((column) => (
                  <th key={column.label} scope="col" title={column.hint} className="px-2 py-3 text-center font-medium">
                    {column.label}
                  </th>
                ))}
                <th scope="col" className="px-4 py-3 font-medium">Last checked</th>
                <th scope="col" className="px-4 py-3 font-medium">Next check</th>
                <th scope="col" className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {sites.map((site) => (
                <tr key={site.id} className="bg-white dark:bg-zinc-950">
                  <td className="max-w-72 px-4 py-3">
                    <Link href={`/sites/${site.id}`} className="font-medium hover:underline">
                      {site.name}
                    </Link>
                    <div className="truncate text-xs text-zinc-500">{site.url}</div>
                    {site.paused && <div className="text-xs text-amber-700">Paused</div>}
                  </td>
                  {STATUS_COLUMNS.map((column) => {
                    const { status, summary } = columnStatus(site.statuses, column.keys);
                    return (
                      <td key={column.label} className="px-2 py-3 text-center">
                        <StatusDot status={status} label={`${column.hint}: ${summary}`} />
                      </td>
                    );
                  })}
                  <td className="whitespace-nowrap px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {site.last_checked_at ? timeFrom(site.last_checked_at) : "Not yet"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-zinc-600 dark:text-zinc-400">
                    {site.paused ? "Paused" : timeFrom(site.next_check_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {/* A queued check (e.g. a pending confirmation) can be pulled forward; only a
                        running one makes the button wait. */}
                    <CheckNowButton
                      siteId={site.id}
                      busy={site.active_job?.status === "running"}
                      onStarted={reload}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
