-- Product telemetry ingest (ADR-012)

-- Extend signals.source_type CHECK to include product_event
ALTER TABLE signals
    DROP CONSTRAINT IF EXISTS signals_source_type_check;
ALTER TABLE signals
    ADD CONSTRAINT signals_source_type_check
    CHECK (source_type IN ('inbound_email', 'json_fixture', 'outbound_email', 'product_event'));

-- Extend signals.channel CHECK to include product
ALTER TABLE signals
    DROP CONSTRAINT IF EXISTS signals_channel_check;
ALTER TABLE signals
    ADD CONSTRAINT signals_channel_check
    CHECK (channel IN ('email', 'ticket', 'chat', 'product'));

-- Extend signals.routing_method CHECK to include api_key_identity
ALTER TABLE signals
    DROP CONSTRAINT IF EXISTS signals_routing_method_check;
ALTER TABLE signals
    ADD CONSTRAINT signals_routing_method_check
    CHECK (routing_method IN (
        'plus_addressing', 'header_domain', 'forward_parse',
        'thread_inherit', 'thread_inherit_split', 'auto_discovery',
        'manual', 'unmatched', 'outbound_bcc', 'api_key_identity'
    ));

-- Add product event columns
ALTER TABLE signals ADD COLUMN IF NOT EXISTS event_name       text;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS event_properties jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS event_id         text;

-- Idempotency: unique event_id per workspace, ignoring NULL
CREATE UNIQUE INDEX IF NOT EXISTS signals_workspace_event_id_uidx
    ON signals (workspace_id, event_id)
    WHERE event_id IS NOT NULL;

-- Indexed event_name lookup for product_usage dimension scoring
CREATE INDEX IF NOT EXISTS signals_workspace_event_name_idx
    ON signals (workspace_id, event_name)
    WHERE event_name IS NOT NULL AND deleted_at IS NULL;

-- Extend health_dimension_configs.dimension_type CHECK
ALTER TABLE health_dimension_configs
    DROP CONSTRAINT IF EXISTS health_dimension_configs_dimension_type_check;
ALTER TABLE health_dimension_configs
    ADD CONSTRAINT health_dimension_configs_dimension_type_check
    CHECK (dimension_type IN (
        'email', 'support_ticket', 'slack',
        'platform_event', 'custom_goal', 'csm_score',
        'sentiment', 'product_usage'
    ));

-- Seed product_usage stub for every workspace that has any dimension config
INSERT INTO health_dimension_configs
    (workspace_id, dimension_type, name, weight, enabled, config)
SELECT DISTINCT
    hdc.workspace_id,
    'product_usage',
    'Product Usage',
    0,
    false,
    '{}'::jsonb
FROM health_dimension_configs hdc
WHERE hdc.deleted_at IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM health_dimension_configs hdc2
      WHERE hdc2.workspace_id    = hdc.workspace_id
        AND hdc2.dimension_type  = 'product_usage'
        AND hdc2.deleted_at      IS NULL
  );

-- Documentation only: key_prefix should be the first 24 chars of the full key
COMMENT ON COLUMN api_keys.key_prefix IS
    'First 24 characters of the full key. csp_pub_<16 random hex> = 64 bits entropy.';
