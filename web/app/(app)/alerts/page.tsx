"use client";

import Link from "next/link";

import { dateTime } from "@/lib/format";
import type { Alert } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const KIND_STYLE: Record<Alert["kind"], string> = {
  failure: "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-300",
  reminder: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  recovery: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
};

export default function AlertsPage() {
  const { data: alerts, error } = useApi<Alert[]>("/alerts", 15000);

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Every email we sent you: one when something breaks (after a confirmation re-check), a
        reminder each day it stays broken, and one when it&apos;s fixed.
      </p>
      {error && <p role="alert" className="mt-6 text-sm text-red-700">{error.message}</p>}
      {alerts && alerts.length === 0 && (
        <p className="mt-8 text-sm text-zinc-500">No alerts yet. That&apos;s good news.</p>
      )}
      {alerts && alerts.length > 0 && (
        <ul className="mt-6 divide-y divide-zinc-200 rounded-lg border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {alerts.map((alert) => (
            <li key={alert.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 bg-white p-4 dark:bg-zinc-950">
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${KIND_STYLE[alert.kind]}`}>
                {alert.kind}
              </span>
              <span className="min-w-0 flex-1 font-medium">{alert.subject}</span>
              <Link href={`/sites/${alert.site_id}`} className="text-sm text-zinc-600 hover:underline dark:text-zinc-400">
                {alert.site_name}
              </Link>
              <span className="w-full text-xs text-zinc-500 sm:w-auto">
                {dateTime(alert.created_at)} · {alert.sent_at ? "emailed" : "sending…"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
