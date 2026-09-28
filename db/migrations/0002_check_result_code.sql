-- 0002_check_result_code.sql: store each result's machine-readable outcome code.
-- The status says how bad (pass/warn/fail/info/error); the code says what happened
-- ("installed_not_firing") and keys the plain-English explanation shown to the owner.
ALTER TABLE check_results ADD COLUMN code text NOT NULL DEFAULT 'unknown';
ALTER TABLE check_results ALTER COLUMN code DROP DEFAULT;
