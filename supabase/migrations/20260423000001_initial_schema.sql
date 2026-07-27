-- Initial schema — squash of Phase 1 + Phase 2 review fixes.
-- Replaces 20260422_000001 through 20260422_000015.
-- Safe to apply to any empty database; never apply on top of the old files.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Shared trigger functions
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- narrative_regen_jobs uses last_updated_at instead of updated_at.
CREATE OR REPLACE FUNCTION set_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- organizations
-- ---------------------------------------------------------------------------

CREATE TABLE organizations (
    id          uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug        text        NOT NULL,
    name        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz,
    UNIQUE (slug)
);

CREATE TRIGGER set_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- workspaces
-- ---------------------------------------------------------------------------

-- inbound_address is NOT stored here; derived in app code as {slug}@{INBOUND_DOMAIN}.
CREATE TABLE workspaces (
    id               uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id  uuid        NOT NULL REFERENCES organizations(id),
    slug             text        NOT NULL,
    name             text        NOT NULL,
    internal_domains text[]      NOT NULL DEFAULT '{}',
    crm_url_template text,
    crm_portal_id    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    UNIQUE (slug)
);

CREATE TRIGGER set_workspaces_updated_at
    BEFORE UPDATE ON workspaces
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id           uuid        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    workspace_id uuid        NOT NULL REFERENCES workspaces(id),
    email        text        NOT NULL,
    display_name text        NOT NULL,
    role         text        NOT NULL DEFAULT 'member',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    CHECK (role IN ('admin', 'member')),
    UNIQUE (workspace_id, email)
);

CREATE TRIGGER set_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- accounts
-- ---------------------------------------------------------------------------

CREATE TABLE accounts (
    id                           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id                 uuid        NOT NULL REFERENCES workspaces(id),
    slug                         text        NOT NULL,
    name                         text        NOT NULL,
    primary_domain               text,
    additional_domains           text[]      NOT NULL DEFAULT '{}',
    vertical                     text,
    crm_slug                     text,
    status                       text        NOT NULL DEFAULT 'candidate',
    last_narrative_generated_at  timestamptz,
    created_at                   timestamptz NOT NULL DEFAULT now(),
    updated_at                   timestamptz NOT NULL DEFAULT now(),
    deleted_at                   timestamptz,
    CHECK (status IN ('candidate', 'active', 'archived')),
    CHECK (vertical IN ('pharma', 'academia', 'policy', 'tech', 'medtech', 'cpg', 'other')),
    UNIQUE (workspace_id, slug)
);

CREATE INDEX accounts_primary_domain_idx ON accounts (workspace_id, primary_domain);
CREATE INDEX accounts_additional_domains_gin_idx ON accounts USING GIN (additional_domains);

CREATE TRIGGER set_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- contacts
-- ---------------------------------------------------------------------------

CREATE TABLE contacts (
    id           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id uuid        NOT NULL REFERENCES workspaces(id),
    account_id   uuid        REFERENCES accounts(id),
    email        text        NOT NULL,
    display_name text,
    is_internal  boolean     NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    UNIQUE (workspace_id, email)
);

CREATE TRIGGER set_contacts_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- raw_inbound_events
-- ---------------------------------------------------------------------------

CREATE TABLE raw_inbound_events (
    id           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id uuid        NOT NULL REFERENCES workspaces(id),
    received_at  timestamptz NOT NULL DEFAULT now(),
    source_type  text        NOT NULL,
    raw_payload  text        NOT NULL,
    parse_status text        NOT NULL DEFAULT 'pending',
    signal_id    uuid,
    error_detail text,
    processed_at timestamptz,
    CHECK (source_type IN ('inbound_email', 'json_fixture')),
    CHECK (parse_status IN ('pending', 'processed', 'parse_failed', 'skipped'))
);

CREATE INDEX raw_inbound_events_workspace_received_idx
    ON raw_inbound_events (workspace_id, received_at DESC);
CREATE INDEX raw_inbound_events_pending_idx
    ON raw_inbound_events (parse_status) WHERE parse_status = 'pending';

-- ---------------------------------------------------------------------------
-- signals
-- ---------------------------------------------------------------------------

CREATE TABLE signals (
    id                    uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id          uuid        NOT NULL REFERENCES workspaces(id),
    account_id            uuid        REFERENCES accounts(id),
    source_type           text        NOT NULL,
    external_id           text        NOT NULL,
    thread_id             text,
    direction             text        NOT NULL,
    channel               text        NOT NULL,
    occurred_at           timestamptz NOT NULL,
    ingested_at           timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    subject               text,
    body                  text        NOT NULL,
    author_contact_id     uuid        REFERENCES contacts(id),
    recipient_contact_ids uuid[]      NOT NULL DEFAULT '{}',
    routing_method        text,
    routing_confidence    real,
    routing_warning       text,
    embedding             vector(1536),
    deleted_at            timestamptz,
    UNIQUE (workspace_id, external_id),
    CHECK (source_type IN ('inbound_email', 'json_fixture')),
    CHECK (direction IN ('inbound', 'outbound', 'internal')),
    CHECK (channel IN ('email', 'ticket', 'chat')),
    CHECK (routing_method IN (
        'plus_addressing', 'header_domain', 'forward_parse',
        'thread_inherit', 'thread_inherit_split', 'auto_discovery',
        'manual', 'unmatched'
    )),
    CHECK (routing_confidence IS NULL OR routing_confidence BETWEEN 0.0 AND 1.0)
);

CREATE INDEX signals_account_occurred_idx
    ON signals (workspace_id, account_id, occurred_at DESC);
CREATE INDEX signals_thread_idx
    ON signals (workspace_id, thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX signals_recipients_gin_idx
    ON signals USING GIN (recipient_contact_ids);

CREATE TRIGGER set_signals_updated_at
    BEFORE UPDATE ON signals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- narratives  (append-only — no updated_at trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE narratives (
    id                   uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id         uuid        NOT NULL REFERENCES workspaces(id),
    account_id           uuid        NOT NULL REFERENCES accounts(id),
    narrative            text        NOT NULL,
    confidence_tier      text        NOT NULL,
    confidence_rationale text        NOT NULL,
    signal_window_start  timestamptz NOT NULL,
    signal_window_end    timestamptz NOT NULL,
    signals_considered   uuid[]      NOT NULL DEFAULT '{}',
    model                text        NOT NULL,
    prompt_version       text        NOT NULL,
    generated_at         timestamptz NOT NULL DEFAULT now(),
    superseded_at        timestamptz,
    CHECK (confidence_tier IN ('high', 'medium', 'low'))
);

-- Prevents concurrent workers from generating two active narratives for the same account.
CREATE UNIQUE INDEX narratives_active_per_account
    ON narratives (account_id) WHERE superseded_at IS NULL;
CREATE INDEX narratives_account_current_idx
    ON narratives (workspace_id, account_id, generated_at DESC) WHERE superseded_at IS NULL;
CREATE INDEX narratives_signals_gin_idx
    ON narratives USING GIN (signals_considered);

-- ---------------------------------------------------------------------------
-- audit_events  (append-only — no updated_at trigger)
-- ---------------------------------------------------------------------------

CREATE TABLE audit_events (
    id            uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  uuid        REFERENCES workspaces(id),
    actor_type    text        NOT NULL,
    actor_id      text        NOT NULL,
    action        text        NOT NULL,
    resource_type text,
    resource_id   uuid,
    metadata      jsonb,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (actor_type IN ('user', 'worker', 'system'))
);

CREATE INDEX audit_events_workspace_occurred_idx
    ON audit_events (workspace_id, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- narrative_regen_jobs
-- ---------------------------------------------------------------------------

CREATE TABLE narrative_regen_jobs (
    id                   uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id         uuid        NOT NULL REFERENCES workspaces(id),
    account_id           uuid        NOT NULL REFERENCES accounts(id),
    requested_at         timestamptz NOT NULL DEFAULT now(),
    scheduled_for        timestamptz NOT NULL,
    status               text        NOT NULL DEFAULT 'pending',
    triggered_by         text        NOT NULL,
    triggered_by_user_id uuid        REFERENCES users(id),
    last_updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('pending', 'running', 'done', 'failed')),
    CHECK (triggered_by IN ('new_signal', 'reroute', 'config_change', 'manual'))
);

CREATE INDEX regen_jobs_account_status_idx
    ON narrative_regen_jobs (workspace_id, account_id, status);
CREATE INDEX regen_jobs_pending_idx
    ON narrative_regen_jobs (status, scheduled_for) WHERE status = 'pending';

CREATE TRIGGER set_narrative_regen_jobs_last_updated_at
    BEFORE UPDATE ON narrative_regen_jobs
    FOR EACH ROW EXECUTE FUNCTION set_last_updated_at();

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

-- SECURITY DEFINER helper avoids infinite recursion when the users policy
-- queries the users table to resolve workspace_id.
-- Worker uses service-role key (bypasses RLS). App code asserts workspace_id
-- on every write as defense-in-depth.
CREATE OR REPLACE FUNCTION current_user_workspace_id()
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER AS $$
  SELECT workspace_id FROM users WHERE id = auth.uid()
$$;

ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts           ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals            ENABLE ROW LEVEL SECURITY;
ALTER TABLE narratives         ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_inbound_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE narrative_regen_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workspace_isolation" ON users
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON accounts
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON contacts
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON signals
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON narratives
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON audit_events
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON raw_inbound_events
    FOR ALL USING (workspace_id = current_user_workspace_id());
CREATE POLICY "workspace_isolation" ON narrative_regen_jobs
    FOR ALL USING (workspace_id = current_user_workspace_id());

-- ---------------------------------------------------------------------------
-- PostgREST grants
-- Required when creating tables via raw SQL rather than the Supabase dashboard.
-- The dashboard adds these automatically; a migration script must do it explicitly.
-- ---------------------------------------------------------------------------

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated, service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON ALL ROUTINES IN SCHEMA public TO anon, authenticated, service_role;
