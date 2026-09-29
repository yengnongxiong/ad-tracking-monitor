-- Retention (PRD §11): a daily job deletes monitoring history older than RETENTION_DAYS.

-- Serves: the retention job's batches (WHERE site_id IS NOT NULL AND started_at < cutoff).
-- Partial: research-scan runs (site_id IS NULL) are kept, so they don't need indexing here.
CREATE INDEX check_runs_retention_idx ON check_runs (started_at) WHERE site_id IS NOT NULL;

-- Serves: the scheduler's "is a retention run due?" check, every 30 s
-- (WHERE type = 'retention' AND ...). Partial, so it holds about one row per day.
CREATE INDEX jobs_retention_idx ON jobs (finished_at) WHERE type = 'retention';
