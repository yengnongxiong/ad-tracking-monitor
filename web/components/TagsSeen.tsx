"use client";

import { useState } from "react";

import { button, card } from "@/components/ui";
import { api } from "@/lib/api";
import type { LatestResult, SiteDetail } from "@/lib/types";

const TAGS: { key: string; label: string; expectedField: keyof SiteDetail & `expected_${string}` }[] = [
  { key: "meta_pixel", label: "Meta Pixel", expectedField: "expected_meta_pixel_ids" },
  { key: "google_ga4", label: "GA4", expectedField: "expected_ga4_ids" },
  { key: "google_ads", label: "Google Ads", expectedField: "expected_google_ads_ids" },
];

/** The tracking IDs and events we actually saw on the page. If the owner hasn't told us
 *  which IDs to expect, one click turns the detected ones into expected ones, so a pixel
 *  that later disappears raises an alert instead of a quiet "not installed". */
export function TagsSeen({ site, onChanged }: { site: SiteDetail; onChanged: () => void }) {
  const [saving, setSaving] = useState<string | null>(null);
  const byKey = new Map(site.latest_results.map((r) => [r.check_key, r] as [string, LatestResult]));
  const gtm = byKey.get("google_gtm")?.details.ids ?? [];

  async function expect(field: string, ids: string[]) {
    setSaving(field);
    await api(`/sites/${site.id}`, { method: "PATCH", body: { [field]: ids } });
    setSaving(null);
    onChanged();
  }

  return (
    <div className={`${card} space-y-4`}>
      {TAGS.map((tag) => {
        const result = byKey.get(tag.key);
        const ids = result?.details.ids ?? [];
        const events = result?.details.events ?? [];
        const expected = site[tag.expectedField] as string[];
        return (
          <div key={tag.key} className="flex flex-wrap items-start justify-between gap-3 text-sm">
            <div>
              <div className="font-medium">{tag.label}</div>
              {ids.length === 0 ? (
                <div className="text-zinc-500">None seen</div>
              ) : (
                <ul className="text-zinc-600 dark:text-zinc-400">
                  {ids.map((id) => (
                    <li key={id}>
                      <code className="text-xs">{id}</code>
                      {events.filter((e) => e.id === id).map((e) => (
                        <span key={e.event} className="ml-2 text-xs">
                          {e.event} ×{e.count}
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {ids.length > 0 && expected.length === 0 && (
              <button
                onClick={() => expect(tag.expectedField, ids)}
                disabled={saving !== null}
                className={button.secondary}
              >
                {saving === tag.expectedField ? "Saving…" : "Alert me if these disappear"}
              </button>
            )}
            {expected.length > 0 && (
              <span className="text-xs text-zinc-500">Expecting {expected.join(", ")}</span>
            )}
          </div>
        );
      })}
      <div className="text-sm">
        <div className="font-medium">Tag Manager</div>
        <div className="text-zinc-600 dark:text-zinc-400">{gtm.length ? gtm.join(", ") : "None seen"}</div>
      </div>
    </div>
  );
}
