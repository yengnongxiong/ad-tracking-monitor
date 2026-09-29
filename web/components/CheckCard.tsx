import { StatusBadge } from "@/components/Status";
import { card } from "@/components/ui";
import type { LatestResult } from "@/lib/types";

/** One check's latest result, in plain English, with how to fix it when it isn't OK. */
export function CheckCard({ result }: { result: LatestResult }) {
  const needsAction = result.status === "fail" || result.status === "warn";
  return (
    <article className={`${card} flex flex-col gap-2`}>
      <header className="flex items-start justify-between gap-3">
        <h3 className="font-medium">{result.title}</h3>
        <StatusBadge status={result.status} />
      </header>
      <p className="text-sm">{result.summary}</p>
      {result.explanation && needsAction && (
        <dl className="space-y-2 border-t border-zinc-100 pt-2 text-sm dark:border-zinc-800">
          <div>
            <dt className="font-medium">Why it matters</dt>
            <dd className="text-zinc-600 dark:text-zinc-400">{result.explanation.why_it_matters}</dd>
          </div>
          <div>
            <dt className="font-medium">How to fix it</dt>
            <dd className="text-zinc-600 dark:text-zinc-400">{result.explanation.how_to_fix}</dd>
          </div>
        </dl>
      )}
      <p className="mt-auto text-xs text-zinc-500">Seen on {result.device}</p>
    </article>
  );
}
