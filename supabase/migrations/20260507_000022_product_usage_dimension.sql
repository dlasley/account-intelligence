-- Migration 000022: activate product_usage dimension and rebalance weights
-- ADR-017: Product Usage Health Dimension
--
-- Uses window_days=7 (calibrated to actual signal distribution — batch-cycle signals
-- arrive in a ~7-day window, not 28-day; the dual-window split needs to fall within
-- the actual data density band to detect the trajectory pattern).

-- Step 1: activate product_usage stub rows seeded by migration 000014
UPDATE health_dimension_configs
SET
    weight  = 0.35,
    enabled = true,
    config  = jsonb_build_object(
        'window_days',             7,
        'min_events_for_active',   1,
        'trajectory_decay_ratio',  0.5
    )
WHERE dimension_type = 'product_usage'
  AND weight = 0
  AND enabled = false
  AND deleted_at IS NULL;

-- Step 1b: insert product_usage for workspaces created after migration 000014
-- (those workspaces have no stub row; the insert above would have matched nothing).
INSERT INTO health_dimension_configs
    (workspace_id, dimension_type, name, weight, enabled, config)
SELECT DISTINCT
    hdc.workspace_id,
    'product_usage',
    'Product Usage',
    0.35,
    true,
    jsonb_build_object(
        'window_days',             7,
        'min_events_for_active',   1,
        'trajectory_decay_ratio',  0.5
    )
FROM health_dimension_configs hdc
WHERE hdc.deleted_at IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM health_dimension_configs hdc2
      WHERE hdc2.workspace_id   = hdc.workspace_id
        AND hdc2.dimension_type = 'product_usage'
        AND hdc2.deleted_at     IS NULL
  );

-- Step 2: rebalance email, sentiment, csm_score for workspaces
--         that still carry the migration-000010 weights (0.5 / 0.3 / 0.2).
--         Workspaces with custom weights are untouched.

UPDATE health_dimension_configs
SET weight = 0.35
WHERE dimension_type = 'email'
  AND weight = 0.5
  AND deleted_at IS NULL
  AND workspace_id IN (
      SELECT workspace_id FROM health_dimension_configs
      WHERE dimension_type = 'product_usage'
        AND weight = 0.35
        AND deleted_at IS NULL
  );

UPDATE health_dimension_configs
SET weight = 0.2
WHERE dimension_type = 'sentiment'
  AND weight = 0.3
  AND deleted_at IS NULL
  AND workspace_id IN (
      SELECT workspace_id FROM health_dimension_configs
      WHERE dimension_type = 'product_usage'
        AND weight = 0.35
        AND deleted_at IS NULL
  );

UPDATE health_dimension_configs
SET weight = 0.1
WHERE dimension_type = 'csm_score'
  AND weight = 0.2
  AND deleted_at IS NULL
  AND workspace_id IN (
      SELECT workspace_id FROM health_dimension_configs
      WHERE dimension_type = 'product_usage'
        AND weight = 0.35
        AND deleted_at IS NULL
  );
