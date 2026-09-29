// Mirrors the FastAPI response models in server/src/tagmonitor/api/schemas.py.

export type Status = "pass" | "warn" | "fail" | "info" | "error";

export interface User {
  id: string;
  email: string;
  is_admin: boolean;
}

export interface CheckStatus {
  status: Status;
  code: string;
  summary: string;
}

export interface ActiveJob {
  id: number;
  status: "queued" | "running";
}

export interface Site {
  id: string;
  name: string;
  url: string;
  check_interval_minutes: 60 | 360 | 1440;
  paused: boolean;
  alert_email: string;
  expected_meta_pixel_ids: string[];
  expected_ga4_ids: string[];
  expected_google_ads_ids: string[];
  ad_headline: string | null;
  ad_primary_text: string | null;
  ad_cta: string | null;
  created_at: string;
  next_check_at: string;
  last_checked_at: string | null;
  statuses: Record<string, CheckStatus>;
  active_job: ActiveJob | null;
}

export interface Explanation {
  meaning: string;
  why_it_matters: string;
  how_to_fix: string;
}

export interface TagEvent {
  id: string;
  event: string;
  count: number;
}

// `details` differs per check; these are the fields the dashboard reads.
export interface ResultDetails {
  ids?: string[];
  expected_ids?: string[];
  events?: TagEvent[];
  lcp_ms?: number | null;
  // message_match: the model's verdict
  overall?: number;
  offer_consistency?: number;
  headline_relevance?: number;
  cta_alignment?: number;
  issues?: string[];
  suggestions?: string[];
  model?: string;
  prompt_version?: string;
  [key: string]: unknown;
}

export interface Result {
  check_key: string;
  title: string;
  status: Status;
  code: string;
  summary: string;
  details: ResultDetails;
  explanation: Explanation | null;
}

export interface LatestResult extends Result {
  device: "mobile" | "desktop";
  run_id: number;
  checked_at: string;
}

export interface SiteDetail extends Site {
  latest_results: LatestResult[];
}

export interface RunResultBrief {
  check_key: string;
  status: Status;
  code: string;
}

export interface RunSummary {
  id: number;
  device: "mobile" | "desktop";
  started_at: string;
  status: "completed" | "error";
  error_code: string | null;
  http_status: number | null;
  final_url: string | null;
  results: RunResultBrief[];
}

export interface RunsPage {
  runs: RunSummary[];
  next_cursor: string | null;
}

export interface RunDetail extends Omit<RunSummary, "results"> {
  site_id: string;
  duration_ms: number | null;
  screenshot_url: string | null;
  capture_url: string | null;
  results: Result[];
}

export interface Job {
  id: number;
  type: string;
  status: "queued" | "running" | "succeeded" | "dead";
  reason: string | null;
  attempts: number;
  last_error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Trend {
  metric: string;
  unit: string;
  points: { at: string; value: number }[];
}

export interface Alert {
  id: number;
  site_id: string;
  site_name: string;
  check_key: string;
  kind: "failure" | "reminder" | "recovery";
  subject: string;
  created_at: string;
  sent_at: string | null;
}

// The editable fields of a site, as the add/edit form sends them.
export interface SiteInput {
  url: string;
  name?: string;
  check_interval_minutes: 60 | 360 | 1440;
  alert_email?: string;
  expected_meta_pixel_ids: string[];
  expected_ga4_ids: string[];
  expected_google_ads_ids: string[];
  ad_headline: string | null;
  ad_primary_text: string | null;
  ad_cta: string | null;
}

// GET /api/ops/queue (admins only)
export interface OpsQueue {
  depth: { type: string; status: "queued" | "running"; count: number }[];
  oldest_queued_age_s: number | null;
  last_24h: {
    succeeded: number;
    dead: number;
    success_rate: number | null;
    p50_s: number | null;
    p95_s: number | null;
  };
  dead_jobs: { id: number; type: string; last_error: string | null; finished_at: string | null }[];
  llm_today: { calls: number; limit: number; input_tokens: number; output_tokens: number };
  retention: { days: number; last_run_at: string | null };
}
