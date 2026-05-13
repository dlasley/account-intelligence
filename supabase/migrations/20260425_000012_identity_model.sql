-- service_accounts: workspace-owned principals for integrations
CREATE TABLE service_accounts (
    id           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id uuid        NOT NULL REFERENCES workspaces(id),
    name         text        NOT NULL,
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    UNIQUE (workspace_id, name)
);

CREATE TRIGGER set_service_accounts_updated_at
    BEFORE UPDATE ON service_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- api_keys: scoped credentials owned by a user or service account
CREATE TABLE api_keys (
    id                       uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id             uuid        NOT NULL REFERENCES workspaces(id),
    owner_user_id            uuid        REFERENCES users(id),
    owner_service_account_id uuid        REFERENCES service_accounts(id),
    key_hash                 text        NOT NULL,
    key_prefix               text        NOT NULL,
    label                    text        NOT NULL,
    scopes                   text[]      NOT NULL DEFAULT '{read}',
    last_used_at             timestamptz,
    expires_at               timestamptz,
    revoked_at               timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    deleted_at               timestamptz,
    CHECK (
        (owner_user_id IS NOT NULL AND owner_service_account_id IS NULL) OR
        (owner_user_id IS NULL AND owner_service_account_id IS NOT NULL)
    ),
    UNIQUE (key_hash),
    UNIQUE (key_prefix)
);

CREATE INDEX api_keys_workspace_owner_idx
    ON api_keys (workspace_id, owner_user_id)
    WHERE deleted_at IS NULL AND revoked_at IS NULL;

CREATE TRIGGER set_api_keys_updated_at
    BEFORE UPDATE ON api_keys
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- Extend audit_events.actor_type to include 'api_key'
ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS audit_events_actor_type_check;

ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_actor_type_check
    CHECK (actor_type IN ('user', 'worker', 'system', 'api_key'));


-- RLS: workspace isolation for both new tables
ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys         ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workspace_isolation" ON service_accounts
    FOR ALL USING (workspace_id = current_user_workspace_id());

CREATE POLICY "workspace_isolation" ON api_keys
    FOR ALL USING (workspace_id = current_user_workspace_id());


-- PostgREST grants
GRANT ALL ON service_accounts TO anon, authenticated, service_role;
GRANT ALL ON api_keys         TO anon, authenticated, service_role;
