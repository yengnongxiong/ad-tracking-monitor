import type { CheckStatus, Site, Status } from "@/lib/types";

// The dashboard's status columns (PRD §18). "Google" combines GA4, Ads and GTM (ADR-009).
export const STATUS_COLUMNS: { label: string; keys: string[]; hint: string }[] = [
  { label: "Meta", keys: ["meta_pixel"], hint: "Meta Pixel" },
  { label: "Google", keys: ["google_ga4", "google_ads", "google_gtm"], hint: "GA4, Google Ads, Tag Manager" },
  { label: "Speed", keys: ["page_speed"], hint: "Mobile speed (LCP)" },
  { label: "Mobile", keys: ["mobile_render"], hint: "Mobile layout" },
  { label: "Health", keys: ["page_health"], hint: "Page health" },
  { label: "Message", keys: ["message_match"], hint: "Ad-to-page message match (needs ad copy)" },
];

// For a column that combines *different* tags (Google = GA4 + Ads + GTM), a tag that simply
// isn't installed ("info") must not hide one that works: problems first, then working tags.
const COMBINE: Record<Status, number> = { fail: 5, warn: 4, pass: 3, info: 2, error: 1 };

export function columnStatus(
  statuses: Record<string, CheckStatus>,
  keys: string[],
): { status: Status | null; summary: string } {
  const found = keys.map((key) => statuses[key]).filter((s): s is CheckStatus => Boolean(s));
  if (found.length === 0) return { status: null, summary: "Not checked yet" };
  const headline = found.reduce((a, b) => (COMBINE[b.status] > COMBINE[a.status] ? b : a));
  return { status: headline.status, summary: found.map((s) => s.summary).join(" ") };
}

export const STATUS_LABEL: Record<Status, string> = {
  pass: "OK",
  warn: "Warning",
  fail: "Problem",
  info: "Not set up",
  error: "Not checked",
};

/** Message match needs the ad the page is compared with. */
export function hasAdCopy(site: Site): boolean {
  return [site.ad_headline, site.ad_primary_text, site.ad_cta].some((text) => text?.trim());
}
