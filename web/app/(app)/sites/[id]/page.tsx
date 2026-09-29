"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";

import { CheckCard } from "@/components/CheckCard";
import { CheckNowButton } from "@/components/CheckNowButton";
import { JobWatcher } from "@/components/JobWatcher";
import { LcpChart } from "@/components/LcpChart";
import { RunHistoryGrid } from "@/components/RunHistoryGrid";
import { Screenshots } from "@/components/Screenshots";
import { TagsSeen } from "@/components/TagsSeen";
import { button } from "@/components/ui";
import { dateTime, timeFrom } from "@/lib/format";
import type { RunsPage, SiteDetail, Trend } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-lg font-semibold tracking-tight">{title}</h2>
      {children}
    </section>
  );
}

export default function SitePage() {
  const { id } = useParams<{ id: string }>();
  // A job started from this page; otherwise follow whatever job the API says is active.
  const [startedJob, setStartedJob] = useState<number | null>(null);
  const [watching, setWatching] = useState(false);
  // While a check is queued or running, refresh the site so the page follows it.
  const site = useApi<SiteDetail>(`/sites/${id}`, watching ? 3000 : undefined);
  const runs = useApi<RunsPage>(`/sites/${id}/runs?limit=30`);
  const trend = useApi<Trend>(`/sites/${id}/trends?metric=lcp&days=30`);
  const activeJob = startedJob ?? site.data?.active_job?.id ?? null;
  const running = site.data?.active_job?.status === "running";
  if (watching !== (activeJob !== null)) setWatching(activeJob !== null);

  const { reload: reloadSite } = site;
  const { reload: reloadRuns } = runs;
  const { reload: reloadTrend } = trend;
  const refresh = useCallback(() => {
    setStartedJob(null);
    reloadSite();
    reloadRuns();
    reloadTrend();
  }, [reloadSite, reloadRuns, reloadTrend]);

  if (site.error) return <p role="alert" className="text-sm text-red-700">{site.error.message}</p>;
  if (!site.data) return <p className="text-sm text-zinc-500">Loading…</p>;
  const detail = site.data;

  return (
    <>
      <Link href="/dashboard" className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">
        ← All pages
      </Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">{detail.name}</h1>
          <a href={detail.url} className="break-all text-sm text-zinc-600 hover:underline dark:text-zinc-400" rel="noreferrer" target="_blank">
            {detail.url}
          </a>
          <p className="mt-1 text-xs text-zinc-500">
            {detail.last_checked_at ? `Last checked ${timeFrom(detail.last_checked_at)}` : "Not checked yet"}
            {" · "}
            {detail.paused ? "Paused" : `next check ${timeFrom(detail.next_check_at)}`}
          </p>
        </div>
        <div className="flex gap-2">
          <Link href={`/sites/${id}/edit`} className={button.secondary}>
            Edit
          </Link>
          <CheckNowButton siteId={id} busy={running} onStarted={setStartedJob} />
        </div>
      </div>

      {activeJob !== null && (
        <div className="mt-6">
          <JobWatcher key={activeJob} jobId={activeJob} onDone={refresh} />
        </div>
      )}

      <Section title="Status">
        {detail.latest_results.length === 0 ? (
          <p className="text-sm text-zinc-500">The first check hasn&apos;t finished yet.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {detail.latest_results.map((result) => (
              <CheckCard key={result.check_key} result={result} />
            ))}
          </div>
        )}
      </Section>

      <Section title="Tracking tags we saw">
        <TagsSeen site={detail} onChanged={reloadSite} />
      </Section>

      <Section title="What the page looks like">
        <Screenshots runs={runs.data?.runs ?? []} />
      </Section>

      <Section title="Mobile speed over the last 30 days">
        {trend.data && <LcpChart trend={trend.data} />}
        <p className="mt-1 text-xs text-zinc-500">
          Largest Contentful Paint, measured in our lab browser (not real visitors&apos; phones).
        </p>
      </Section>

      <Section title="Recent checks">
        <RunHistoryGrid runs={runs.data?.runs ?? []} />
        {runs.data?.runs[0] && (
          <p className="mt-1 text-xs text-zinc-500">Latest: {dateTime(runs.data.runs[0].started_at)}</p>
        )}
      </Section>
    </>
  );
}
