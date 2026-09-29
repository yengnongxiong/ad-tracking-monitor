"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Trend } from "@/lib/types";

/** Mobile LCP per check, with Google's "good" (2.5 s) and "poor" (4 s) thresholds. */
export function LcpChart({ trend }: { trend: Trend }) {
  const points = trend.points.map((p) => ({ at: new Date(p.at).getTime(), seconds: p.value / 1000 }));
  if (points.length === 0) return <p className="text-sm text-zinc-500">No speed measurements yet.</p>;
  // Dates for a long history, times of day when all checks fall within two days.
  const span = points[points.length - 1].at - points[0].at;
  const tick = (value: number) =>
    span > 2 * 86_400_000
      ? new Date(value).toLocaleDateString("en", { month: "short", day: "numeric" })
      : new Date(value).toLocaleTimeString("en", { hour: "numeric", minute: "2-digit" });
  return (
    <div className="h-56 w-full" role="img" aria-label={`Mobile LCP over time, latest ${points.at(-1)?.seconds.toFixed(1)} seconds`}>
      <ResponsiveContainer>
        <LineChart data={points} margin={{ top: 8, right: 44, bottom: 0, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
          <XAxis
            dataKey="at"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={tick}
            tick={{ fontSize: 12 }}
          />
          <YAxis unit=" s" tick={{ fontSize: 12 }} domain={[0, (max: number) => Math.max(5, Math.ceil(max))]} />
          <Tooltip
            labelFormatter={(value) => new Date(Number(value)).toLocaleString("en")}
            formatter={(value) => [`${Number(value).toFixed(2)} s`, "LCP (lab)"]}
          />
          <ReferenceLine y={2.5} stroke="#10b981" strokeDasharray="4 4" label={{ value: "good", fontSize: 11, position: "right" }} />
          <ReferenceLine y={4} stroke="#dc2626" strokeDasharray="4 4" label={{ value: "poor", fontSize: 11, position: "right" }} />
          <Line type="monotone" dataKey="seconds" stroke="#18181b" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
