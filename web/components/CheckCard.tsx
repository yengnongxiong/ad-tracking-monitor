import { StatusBadge } from "@/components/Status";
import { card } from "@/components/ui";
import type { LatestResult, ResultDetails } from "@/lib/types";

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
      {result.check_key === "message_match" && result.details.overall !== undefined && (
        <MessageMatchDetails details={result.details} />
      )}
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

const SUB_SCORES: { key: "offer_consistency" | "headline_relevance" | "cta_alignment"; label: string }[] = [
  { key: "offer_consistency", label: "Offer" },
  { key: "headline_relevance", label: "Headline" },
  { key: "cta_alignment", label: "Call to action" },
];

/** The model's sub-scores, what it found, and what it suggests (message match only). */
function MessageMatchDetails({ details }: { details: ResultDetails }) {
  const issues = details.issues ?? [];
  const suggestions = details.suggestions ?? [];
  return (
    <div className="space-y-2 text-sm">
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-zinc-600 dark:text-zinc-400">
        {SUB_SCORES.map(({ key, label }) => (
          <div key={key} className="flex gap-1">
            <dt>{label}</dt>
            <dd className="font-medium text-zinc-900 dark:text-zinc-100">{details[key]}/5</dd>
          </div>
        ))}
      </dl>
      {issues.length > 0 && (
        <div>
          <h4 className="font-medium">What doesn&apos;t match</h4>
          <ul className="list-disc pl-5 text-zinc-600 dark:text-zinc-400">
            {issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        </div>
      )}
      {suggestions.length > 0 && (
        <div>
          <h4 className="font-medium">Suggestions</h4>
          <ul className="list-disc pl-5 text-zinc-600 dark:text-zinc-400">
            {suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}
          </ul>
        </div>
      )}
      <p className="text-xs text-zinc-500">
        Judged by an AI model ({details.model}, prompt {details.prompt_version}) from the page&apos;s text.
      </p>
    </div>
  );
}
