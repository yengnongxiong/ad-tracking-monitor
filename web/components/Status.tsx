import { STATUS_LABEL } from "@/lib/checks";
import type { Status } from "@/lib/types";

// Color is never the only signal: each status also has its own symbol and a text label.
const STYLES: Record<Status, { dot: string; badge: string; symbol: string }> = {
  pass: {
    dot: "bg-emerald-500 text-white",
    badge: "bg-emerald-50 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300",
    symbol: "✓",
  },
  warn: {
    dot: "bg-amber-400 text-amber-950",
    badge: "bg-amber-50 text-amber-800 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300",
    symbol: "!",
  },
  fail: {
    dot: "bg-red-600 text-white",
    badge: "bg-red-50 text-red-800 ring-red-600/20 dark:bg-red-950 dark:text-red-300",
    symbol: "✕",
  },
  info: {
    dot: "bg-sky-200 text-sky-900 dark:bg-sky-800 dark:text-sky-100",
    badge: "bg-sky-50 text-sky-800 ring-sky-600/20 dark:bg-sky-950 dark:text-sky-300",
    symbol: "–",
  },
  error: {
    dot: "bg-zinc-300 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200",
    badge: "bg-zinc-100 text-zinc-700 ring-zinc-500/20 dark:bg-zinc-800 dark:text-zinc-300",
    symbol: "?",
  },
};

export function StatusDot({
  status,
  label,
  size = "md",
}: {
  status: Status | null;
  label: string;
  size?: "sm" | "md";
}) {
  const box = size === "sm" ? "h-4 w-4 text-[10px]" : "h-6 w-6 text-xs";
  if (status === null) {
    return (
      <span
        title={label}
        className={`inline-flex ${box} items-center justify-center rounded-full border border-dashed border-zinc-300 dark:border-zinc-600`}
      >
        <span className="sr-only">{label}</span>
      </span>
    );
  }
  return (
    <span
      title={label}
      className={`inline-flex ${box} items-center justify-center rounded-full font-bold ${STYLES[status].dot}`}
    >
      <span aria-hidden="true">{STYLES[status].symbol}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status].badge}`}
    >
      <span aria-hidden="true">{STYLES[status].symbol}</span>
      {STATUS_LABEL[status]}
    </span>
  );
}
