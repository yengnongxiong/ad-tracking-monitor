import { dateTime } from "@/lib/format";
import type { RunSummary, Status } from "@/lib/types";

const ROWS: { key: string; title: string }[] = [
  { key: "meta_pixel", title: "Meta Pixel" },
  { key: "google_ga4", title: "GA4" },
  { key: "google_ads", title: "Google Ads" },
  { key: "google_gtm", title: "Tag Manager" },
  { key: "page_speed", title: "Mobile speed" },
  { key: "mobile_render", title: "Mobile layout" },
  { key: "page_health", title: "Page health" },
  { key: "message_match", title: "Message match" },
];

const CELL: Record<Status, string> = {
  pass: "bg-emerald-500",
  warn: "bg-amber-400",
  fail: "bg-red-600",
  info: "bg-sky-200 dark:bg-sky-800",
  error: "bg-zinc-300 dark:bg-zinc-600",
};

/** One column per page load (oldest left), one row per check, one colored cell per result. */
export function RunHistoryGrid({ runs }: { runs: RunSummary[] }) {
  const columns = [...runs].reverse();
  if (columns.length === 0) return <p className="text-sm text-zinc-500">No checks yet.</p>;
  // Message match only runs for sites with ad copy, so its row only shows once it has run.
  const hasMessageMatch = runs.some((run) => run.results.some((r) => r.check_key === "message_match"));
  const rows = ROWS.filter((row) => row.key !== "message_match" || hasMessageMatch);
  return (
    <div className="overflow-x-auto">
      <table className="border-separate border-spacing-1 text-xs">
        <caption className="sr-only">Check results for each recent page load, oldest first</caption>
        <thead>
          <tr>
            <th scope="col" className="sr-only">Check</th>
            {columns.map((run) => (
              <th key={run.id} scope="col" title={dateTime(run.started_at)} className="w-5 font-normal text-zinc-500">
                {run.device === "mobile" ? "M" : "D"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row" className="whitespace-nowrap pr-3 text-left font-normal text-zinc-600 dark:text-zinc-400">
                {row.title}
              </th>
              {columns.map((run) => {
                const result = run.results.find((r) => r.check_key === row.key);
                const label = result
                  ? `${dateTime(run.started_at)}, ${run.device}: ${result.status} (${result.code})`
                  : `${dateTime(run.started_at)}, ${run.device}: not checked on this device`;
                return (
                  <td key={run.id} title={label} className="p-0">
                    <span className={`block h-5 w-5 rounded-sm ${result ? CELL[result.status] : "bg-zinc-100 dark:bg-zinc-800"}`}>
                      <span className="sr-only">{label}</span>
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-zinc-500">M = mobile, D = desktop. Hover a cell for details.</p>
    </div>
  );
}
