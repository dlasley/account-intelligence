-- Migrate health_dimension_configs.config JSONB key score_from → email_score_source.
-- Rows seeded before the Commit 4 rename still carry the legacy "score_from" key.
-- The backwards-compat fallback in _score_and_snapshot is removed after this migration.
-- ADR naming-opacity audit 2026-05-07.

UPDATE health_dimension_configs
SET config = jsonb_set(config - 'score_from', '{email_score_source}', config->'score_from')
WHERE config ? 'score_from';
