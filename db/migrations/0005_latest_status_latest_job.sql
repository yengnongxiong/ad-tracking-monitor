-- 0005_latest_status_latest_job.sql: the dashboard shows the latest check, and only it.
--
-- The previous view took each check's newest result from *any* job. A check that stopped
-- running therefore stayed on the dashboard forever: delete a site's ad copy and its last
-- message match verdict (say, "poor match") kept showing. Now the view takes every result
-- from the site's latest job (the job of its most recent completed run) and nothing older.
-- A job that died leaves only an error run with no results, so it doesn't count as a check.
--
-- Within that job the rule is unchanged: a check that ran on both devices shows the worse
-- result (fail, warn, info, pass, then error). Keep the order in sync with
-- tagmonitor.checks.base.worst_status. A tie goes to the device captured first (mobile),
-- like alerts.outbox.worst_results, so the dashboard and the email name the same device.
--
-- job_id is NULL on runs whose job the retention job deleted; those are only ever a site's
-- latest run per device (retention keeps those), so NULL sorts last and matches NULL.
CREATE OR REPLACE VIEW site_latest_status AS
WITH latest_job AS (
    SELECT DISTINCT ON (site_id) site_id, job_id
    FROM check_runs
    WHERE site_id IS NOT NULL AND status = 'completed'
    ORDER BY site_id, job_id DESC NULLS LAST, started_at DESC
)
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
FROM latest_job l
JOIN check_runs r ON r.site_id = l.site_id
                 AND r.job_id IS NOT DISTINCT FROM l.job_id
                 AND r.status = 'completed'
JOIN check_results cr ON cr.run_id = r.id
ORDER BY r.site_id,
         cr.check_key,
         CASE cr.status
             WHEN 'fail' THEN 5
             WHEN 'warn' THEN 4
             WHEN 'info' THEN 3
             WHEN 'pass' THEN 2
             ELSE 1
         END DESC,
         r.started_at;
