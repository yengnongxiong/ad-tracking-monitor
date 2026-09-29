-- 0003_api_support.sql: what the HTTP API (M6) needs beyond the initial schema.

-- Failed login attempts, for the "5 per 15 minutes per email+IP" limit (PRD §17). Kept in
-- Postgres rather than process memory so the limit survives restarts and holds across API
-- instances.
CREATE TABLE login_attempts (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key          text NOT NULL,  -- lowercased email + "|" + client IP
    attempted_at timestamptz NOT NULL DEFAULT now()
);
-- Serves: counting recent failures (WHERE key = $1 AND attempted_at > now() - interval '15
-- minutes') and deleting a key's attempts after a successful login.
CREATE INDEX login_attempts_key_time_idx ON login_attempts (key, attempted_at);

-- "Check now" is allowed at most once per 5 minutes per site (PRD §17).
ALTER TABLE sites ADD COLUMN last_check_requested_at timestamptz;

-- Serves: the ops page's 24-hour statistics (WHERE finished_at > now() - interval '24 hours')
-- and the retention job (WHERE finished_at < cutoff). Unfinished jobs aren't indexed.
CREATE INDEX jobs_finished_at_idx ON jobs (finished_at) WHERE finished_at IS NOT NULL;

-- The dashboard also needs each result's outcome code (added in 0002). CREATE OR REPLACE VIEW
-- may only append columns, so `code` goes last.
CREATE OR REPLACE VIEW site_latest_status AS
SELECT DISTINCT ON (r.site_id, cr.check_key)
       r.site_id,
       cr.check_key,
       cr.status,
       cr.summary,
       cr.details,
       r.device,
       r.id AS run_id,
       r.job_id,
       r.started_at,
       cr.code
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
