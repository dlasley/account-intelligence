-- Migration 000029: Multi-source structured signals foundation (ADR-020 Phase 1)
--
-- Operations:
--   1. Create external_credentials table (D3) — stores both inbound webhook secrets
--      (Plain) and outbound API keys (Granola). AES-256-GCM encrypted secret_enc;
--      authenticated role is granted named columns only, explicitly excluding secret_enc.
--   2. Create integration_state table (D5) — per-credential poll cursor and error counters.
--   3. Add signal_metadata JSONB column to signals (D12).
--   4. Extend signals.source_type CHECK to add plain_ticket, granola_note (D12).
--   5. Extend signals.channel CHECK to add meeting_note (D12).


-- ─── external_credentials ───────────────────────────────────────────────────

CREATE TABLE external_credentials (
    id               uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    workspace_id     uuid NOT NULL REFERENCES workspaces(id),
    kind             text NOT NULL,
    direction        text NOT NULL,
    label            text NOT NULL,
    secret_enc       bytea NOT NULL,
    key_hint         text NOT NULL,
    metadata         jsonb NOT NULL DEFAULT '{}',
    is_active        boolean NOT NULL DEFAULT true,
    last_verified_at timestamptz NULL,
    error_at         timestamptz NULL,
    error_message    text NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz NULL,
    CONSTRAINT external_credentials_kind_check
        CHECK (kind IN ('plain_webhook_secret', 'granola_api_key')),
    CONSTRAINT external_credentials_direction_check
        CHECK (direction IN ('inbound', 'outbound'))
);

CREATE TRIGGER external_credentials_updated_at
    BEFORE UPDATE ON external_credentials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE external_credentials ENABLE ROW LEVEL SECURITY;

CREATE POLICY external_credentials_workspace_isolation
    ON external_credentials
    FOR ALL
    USING  (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

-- Column-level grant: secret_enc intentionally excluded from authenticated.
-- service_role gets full access (worker reads secret_enc to decrypt).
GRANT SELECT (id, workspace_id, kind, direction, label, key_hint,
              metadata, is_active, last_verified_at, error_at,
              error_message, created_at, updated_at, deleted_at)
    ON external_credentials TO authenticated;
GRANT ALL ON external_credentials TO service_role;
-- No INSERT/UPDATE/DELETE to authenticated per ADR-019.

-- GIN index for Plain workspace_id metadata lookups (D9)
CREATE INDEX external_credentials_metadata_gin_idx
    ON external_credentials USING gin (metadata);


-- ─── integration_state ──────────────────────────────────────────────────────

CREATE TABLE integration_state (
    id                uuid PRIMARY KEY DEFAULT extensions.uuid_generate_v4(),
    workspace_id      uuid NOT NULL REFERENCES workspaces(id),
    credential_id     uuid NOT NULL REFERENCES external_credentials(id),
    kind              text NOT NULL,
    cursor            text NULL,
    last_polled_at    timestamptz NULL,
    last_success_at   timestamptz NULL,
    consecutive_errors int NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz NULL,
    UNIQUE (credential_id)
);

CREATE TRIGGER integration_state_updated_at
    BEFORE UPDATE ON integration_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE integration_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY integration_state_workspace_isolation
    ON integration_state
    FOR ALL
    USING  (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

GRANT SELECT ON integration_state TO authenticated, service_role;
-- No INSERT/UPDATE/DELETE to authenticated per ADR-019.
-- The Python worker uses service_role for all writes.


-- ─── signals: new column + extended CHECK constraints ───────────────────────

ALTER TABLE signals ADD COLUMN signal_metadata jsonb NOT NULL DEFAULT '{}';

ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_source_type_check;
ALTER TABLE signals ADD CONSTRAINT signals_source_type_check
    CHECK (source_type IN (
        'inbound_email', 'json_fixture', 'outbound_email',
        'product_event', 'plain_ticket', 'granola_note'
    ));

ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_channel_check;
ALTER TABLE signals ADD CONSTRAINT signals_channel_check
    CHECK (channel IN ('email', 'ticket', 'chat', 'product', 'meeting_note'));
