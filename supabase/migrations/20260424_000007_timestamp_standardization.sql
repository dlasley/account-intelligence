-- 000007: Standardize timestamp column names on mutable tables
--
-- Append-only tables (narratives, audit_events, raw_inbound_events,
-- account_dimension_scores, account_health_snapshots) keep their
-- semantically meaningful timestamps (generated_at, occurred_at,
-- received_at, scored_at, computed_at) -- those are intentional and correct.
--
-- Two mutable tables deviated from the created_at / updated_at convention:
--   signals              ingested_at       → created_at
--   narrative_regen_jobs requested_at      → created_at
--                        last_updated_at   → updated_at
--                        (missing deleted_at added)

-- ── signals ────────────────────────────────────────────────────────────────

ALTER TABLE signals RENAME COLUMN ingested_at TO created_at;

-- ── narrative_regen_jobs ────────────────────────────────────────────────────

ALTER TABLE narrative_regen_jobs RENAME COLUMN requested_at  TO created_at;
ALTER TABLE narrative_regen_jobs RENAME COLUMN last_updated_at TO updated_at;
ALTER TABLE narrative_regen_jobs ADD COLUMN deleted_at timestamptz;

-- Replace the one-off trigger function with the shared set_updated_at()
DROP TRIGGER IF EXISTS set_narrative_regen_jobs_last_updated_at ON narrative_regen_jobs;

CREATE TRIGGER set_narrative_regen_jobs_updated_at
    BEFORE UPDATE ON narrative_regen_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- The set_last_updated_at() function is now dead code
DROP FUNCTION IF EXISTS set_last_updated_at();
