-- Step 1: Extend dimension_type CHECK constraint to include 'sentiment'
--
-- The inline constraint was created without a name in migration 000005.
-- Postgres assigns it health_dimension_configs_dimension_type_check.
-- Drop and recreate with the full valid set.

ALTER TABLE health_dimension_configs
    DROP CONSTRAINT IF EXISTS health_dimension_configs_dimension_type_check;

ALTER TABLE health_dimension_configs
    ADD CONSTRAINT health_dimension_configs_dimension_type_check
    CHECK (dimension_type IN (
        'email', 'support_ticket', 'slack',
        'platform_event', 'custom_goal', 'csm_score',
        'sentiment'
    ));


-- Step 2: Rebalance existing dimension weights to match the 3-dimension defaults.
--
-- Targets only workspaces still at the original 2-dimension defaults (email=0.7,
-- csm_score=0.3). Workspaces with custom weights are untouched.

UPDATE health_dimension_configs
SET    weight = 0.5
WHERE  dimension_type = 'email'
  AND  weight = 0.7
  AND  deleted_at IS NULL;

UPDATE health_dimension_configs
SET    weight = 0.2
WHERE  dimension_type = 'csm_score'
  AND  weight = 0.3
  AND  deleted_at IS NULL;


-- Step 3: Seed 'sentiment' dimension for workspaces that have 'email' but no 'sentiment'.
--
-- INSERT-where-missing: idempotent, safe to re-run.

INSERT INTO health_dimension_configs
    (workspace_id, dimension_type, name, weight, enabled, config)
SELECT DISTINCT
    hdc.workspace_id,
    'sentiment',
    'Sentiment',
    0.3,
    true,
    '{}'::jsonb
FROM health_dimension_configs hdc
WHERE hdc.dimension_type = 'email'
  AND hdc.deleted_at IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM   health_dimension_configs hdc2
      WHERE  hdc2.workspace_id    = hdc.workspace_id
        AND  hdc2.dimension_type  = 'sentiment'
        AND  hdc2.deleted_at      IS NULL
  );
