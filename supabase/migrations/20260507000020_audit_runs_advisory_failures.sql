-- Rename narrative_audit_runs.warning_failures → advisory_failures.
-- "warning_failures" conflates "failure" (something went wrong) with "advisory" (it's advisory-only).
-- advisory_failures more clearly distinguishes from hard_gate_failures.
-- ADR naming-opacity audit 2026-05-07.

ALTER TABLE narrative_audit_runs RENAME COLUMN warning_failures TO advisory_failures;
