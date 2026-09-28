-- 0001_init.sql: the initial schema (PRD §11).
-- Conventions: uuid ids for rows that appear in URLs (not enumerable), bigint identity ids for
-- high-volume internal rows (docs/decisions.md, ADR-007). Every index says which query it serves.

CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------------------------

CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         citext NOT NULL UNIQUE,  -- citext: Alice@x.com and alice@x.com are one account
    password_hash text NOT NULL,           -- argon2id encoded hash (includes its own salt)
    is_admin      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash   bytea NOT NULL UNIQUE,  -- SHA-256 of the cookie token; the token is never stored
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
-- Serves: ON DELETE CASCADE from users (find a user's sessions without a full scan).
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

-- ---------------------------------------------------------------------------------------------
-- Monitored sites
-- ---------------------------------------------------------------------------------------------

CREATE TABLE sites (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name                    text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    url                     text NOT NULL,  -- as the user typed it
    normalized_url          text NOT NULL,  -- canonical form used for de-duplication
    registrable_domain      text NOT NULL,  -- e.g. example.co.uk; key for per-domain politeness
    check_interval_minutes  integer NOT NULL DEFAULT 1440
                            CHECK (check_interval_minutes IN (60, 360, 1440)),
    next_check_at           timestamptz NOT NULL DEFAULT now(),
    paused                  boolean NOT NULL DEFAULT false,
    alert_email             citext NOT NULL,
    expected_meta_pixel_ids text[] NOT NULL DEFAULT '{}',
    expected_ga4_ids        text[] NOT NULL DEFAULT '{}',
    expected_google_ads_ids text[] NOT NULL DEFAULT '{}',
    ad_headline             text,
    ad_primary_text         text,
    ad_cta                  text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    -- Also serves "list my sites" (WHERE user_id = $1): user_id is the leading column.
    UNIQUE (user_id, normalized_url)
);
-- Serves: the scheduler's due-sites query (WHERE NOT paused AND next_check_at <= now()).
-- Partial, so paused sites don't bloat it.
CREATE INDEX sites_due_idx ON sites (next_check_at) WHERE NOT paused;

-- ---------------------------------------------------------------------------------------------
-- Job queue (PRD §12)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    type         text NOT NULL
                 CHECK (type IN ('capture_and_check', 'send_alert', 'scan_url', 'retention')),
    payload      jsonb NOT NULL DEFAULT '{}',
    -- No 'failed' state: per §12 a failure either goes back to 'queued' (retry) or to 'dead'.
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'dead')),
    priority     integer NOT NULL DEFAULT 0,
    run_at       timestamptz NOT NULL DEFAULT now(),
    attempts     integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    locked_by    text,
    locked_at    timestamptz,
    heartbeat_at timestamptz,
    last_error   text,
    dedupe_key   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);
-- Serves: the claim query (WHERE status = 'queued' AND run_at <= now()
-- ORDER BY priority DESC, run_at ... FOR UPDATE SKIP LOCKED). Only queued rows are indexed,
-- so it stays small no matter how many finished jobs pile up.
CREATE INDEX jobs_claim_idx ON jobs (priority DESC, run_at) WHERE status = 'queued';
-- Serves: at most one queued-or-running job per dedupe key (e.g. one capture per site), and
-- the ON CONFLICT target of the scheduler's insert.
CREATE UNIQUE INDEX jobs_active_dedupe_key_idx ON jobs (dedupe_key)
    WHERE status IN ('queued', 'running');
-- Serves: the reaper (WHERE status = 'running' AND heartbeat_at < now() - interval '2 minutes').
CREATE INDEX jobs_running_heartbeat_idx ON jobs (heartbeat_at) WHERE status = 'running';

-- ---------------------------------------------------------------------------------------------
-- Research scans (PRD §16). Created before check_runs because runs can belong to a scan.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE scans (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    config     jsonb NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------------------------
-- Check runs and results
-- ---------------------------------------------------------------------------------------------

-- One row per page capture (one device). A monitoring job produces two: mobile and desktop.
CREATE TABLE check_runs (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id        uuid REFERENCES sites (id) ON DELETE CASCADE,
    scan_id        uuid REFERENCES scans (id) ON DELETE CASCADE,
    job_id         bigint REFERENCES jobs (id) ON DELETE SET NULL,
    device         text NOT NULL CHECK (device IN ('mobile', 'desktop')),
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    status         text NOT NULL CHECK (status IN ('completed', 'error')),
    error_code     text,
    error_message  text,
    final_url      text,
    http_status    integer,
    capture_key    text,  -- object storage key of the PageCapture JSON
    screenshot_key text,  -- object storage key of the viewport JPEG
    duration_ms    integer,
    -- A run belongs to exactly one of: a monitored site, or a research scan.
    CONSTRAINT check_runs_owner_ck CHECK ((site_id IS NULL) <> (scan_id IS NULL)),
    CONSTRAINT check_runs_error_code_ck CHECK (status <> 'error' OR error_code IS NOT NULL)
);
-- Serves: a site's run history with keyset pagination
-- (WHERE site_id = $1 AND (started_at, id) < ($2, $3) ORDER BY started_at DESC, id DESC),
-- the trends query, and ON DELETE CASCADE from sites.
CREATE INDEX check_runs_site_history_idx ON check_runs (site_id, started_at DESC, id DESC)
    WHERE site_id IS NOT NULL;
-- Serves: scan analysis (WHERE scan_id = $1) and ON DELETE CASCADE from scans.
CREATE INDEX check_runs_scan_id_idx ON check_runs (scan_id) WHERE scan_id IS NOT NULL;
-- Serves: finding the runs of one job (the latest-status view and job detail).
CREATE INDEX check_runs_job_id_idx ON check_runs (job_id);

CREATE TABLE check_results (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     bigint NOT NULL REFERENCES check_runs (id) ON DELETE CASCADE,
    check_key  text NOT NULL,
    status     text NOT NULL CHECK (status IN ('pass', 'warn', 'fail', 'info', 'error')),
    summary    text NOT NULL,
    details    jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    -- One result per check per run. Also serves "results of run X" (WHERE run_id = $1) and
    -- ON DELETE CASCADE from check_runs.
    UNIQUE (run_id, check_key)
);

-- ---------------------------------------------------------------------------------------------
-- Alerting (PRD §13)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE site_check_states (
    site_id           uuid NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    check_key         text NOT NULL,
    state             text NOT NULL CHECK (state IN ('healthy', 'suspect', 'alerting')),
    consecutive_fails integer NOT NULL DEFAULT 0 CHECK (consecutive_fails >= 0),
    state_entered_at  timestamptz NOT NULL DEFAULT now(),
    last_alerted_at   timestamptz,
    last_pass_at      timestamptz,
    PRIMARY KEY (site_id, check_key)
);

CREATE TABLE alerts (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id    uuid NOT NULL REFERENCES sites (id) ON DELETE CASCADE,
    check_key  text NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('failure', 'reminder', 'recovery')),
    dedupe_key text NOT NULL UNIQUE,  -- makes creating the same alert twice a no-op
    subject    text NOT NULL,
    body_text  text NOT NULL,
    body_html  text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz,  -- set only after the email provider accepted the message
    attempts   integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text
);
-- Serves: the alerts list (sites joined on user_id, ORDER BY created_at DESC) and
-- ON DELETE CASCADE from sites.
CREATE INDEX alerts_site_created_idx ON alerts (site_id, created_at DESC);

-- ---------------------------------------------------------------------------------------------
-- Research scan targets
-- ---------------------------------------------------------------------------------------------

CREATE TABLE scan_targets (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scan_id     uuid NOT NULL REFERENCES scans (id) ON DELETE CASCADE,
    url         text NOT NULL,
    category    text,
    source      text,
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'done', 'skipped', 'failed')),
    skip_reason text,
    run_id      bigint REFERENCES check_runs (id) ON DELETE SET NULL,
    -- Also serves "targets of scan X" (WHERE scan_id = $1): scan_id is the leading column.
    UNIQUE (scan_id, url)
);

-- ---------------------------------------------------------------------------------------------
-- LLM message match (PRD §15)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE llm_cache (
    cache_key      text PRIMARY KEY,  -- sha256(model + prompt_version + ad copy + page text)
    model          text NOT NULL,
    prompt_version text NOT NULL,
    response       jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE llm_usage (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    model         text NOT NULL,
    purpose       text NOT NULL,
    input_tokens  integer NOT NULL,
    output_tokens integer NOT NULL
);
-- Serves: the LLM_MAX_CALLS_PER_DAY guard (WHERE created_at >= date_trunc('day', now())).
CREATE INDEX llm_usage_created_at_idx ON llm_usage (created_at);

-- ---------------------------------------------------------------------------------------------
-- Dashboard view
-- ---------------------------------------------------------------------------------------------

-- One row per (site, check): the result from the site's most recent job. A check can run on
-- two devices in one job; the row shows the worse of the two, so a pixel that is broken on
-- mobile only still shows as broken. Severity order (worst first): fail, warn, info, pass,
-- error. 'error' means "could not evaluate", so it only shows when nothing else ran.
-- Keep this order in sync with tagmonitor.checks.base.worst_status.
CREATE VIEW site_latest_status AS
SELECT DISTINCT ON (r.site_id, cr.check_key)
       r.site_id,
       cr.check_key,
       cr.status,
       cr.summary,
       cr.details,
       r.device,
       r.id AS run_id,
       r.job_id,
       r.started_at
FROM check_results cr
JOIN check_runs r ON r.id = cr.run_id
WHERE r.site_id IS NOT NULL
ORDER BY r.site_id,
         cr.check_key,
         r.job_id DESC NULLS LAST,
         CASE cr.status
             WHEN 'fail' THEN 5
             WHEN 'warn' THEN 4
             WHEN 'info' THEN 3
             WHEN 'pass' THEN 2
             ELSE 1
         END DESC;
