"use client";

import { useEffect, useState } from "react";

interface Proportion {
  label: string;
  successes: number;
  n: number;
  share: number | null;
  ci: [number, number] | null;
}

interface Findings {
  date: string;
  attempted: number;
  loaded: number;
  proportions: Proportion[];
}

const pct = (value: number) => `${Math.round(value * 100)}%`;

/** The research scan's headline numbers, from public/findings.json, which `make findings`
 * writes next to docs/findings.md (same aggregates). Before a scan: a short placeholder. */
export function FindingsHighlights() {
  const [findings, setFindings] = useState<Findings | null>(null);

  useEffect(() => {
    fetch("/findings.json")
      .then((response) => (response.ok ? response.json() : null))
      .then(setFindings, () => setFindings(null));
  }, []);

  if (!findings) {
    return (
      <p className="mt-4 max-w-2xl text-zinc-600 dark:text-zinc-400">
        We&apos;re scanning public small-business websites to measure how often tracking is
        broken in the wild. Findings will be published here once the scan is done.
      </p>
    );
  }
  const shown = findings.proportions.filter((p) => p.share !== null && p.ci !== null && p.n > 0);
  return (
    <div className="mt-4">
      <p className="max-w-2xl text-zinc-600 dark:text-zinc-400">
        We loaded {findings.loaded} small-business websites on a phone, like a visitor from an ad
        would ({findings.date}). Shares with 95% confidence intervals; aggregates only.
      </p>
      <dl className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((p) => (
          <div key={p.label} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <dt className="text-sm text-zinc-600 dark:text-zinc-400">{p.label}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">{pct(p.share ?? 0)}</dd>
            <dd className="text-xs text-zinc-500">
              {p.successes} of {p.n} sites · 95% CI {pct(p.ci![0])} to {pct(p.ci![1])}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
