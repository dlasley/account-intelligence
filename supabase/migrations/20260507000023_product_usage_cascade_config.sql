-- Migration 000023: add window_days_cascade to product_usage dimension config
-- ADR-017 D1 amendment: cascade window scoring (2026-05-07)
--
-- The || merge operator adds the new key without touching existing keys
-- (window_days, min_events_for_active, trajectory_decay_ratio).
-- Existing window_days: 7 is preserved; window_days_cascade takes precedence
-- when present (see score_product_usage cascade precedence logic).

UPDATE health_dimension_configs
SET config = config || jsonb_build_object(
    'window_days_cascade', jsonb_build_array(7, 14, 30, 60)
)
WHERE dimension_type = 'product_usage'
  AND deleted_at IS NULL;
