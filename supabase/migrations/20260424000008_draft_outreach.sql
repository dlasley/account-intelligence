-- Extend signals source_type CHECK to include outbound_email
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_source_type_check;
ALTER TABLE signals ADD CONSTRAINT signals_source_type_check
    CHECK (source_type IN ('inbound_email', 'json_fixture', 'outbound_email'));

-- Extend signals routing_method CHECK to include outbound_bcc
ALTER TABLE signals DROP CONSTRAINT IF EXISTS signals_routing_method_check;
ALTER TABLE signals ADD CONSTRAINT signals_routing_method_check
    CHECK (routing_method IN (
        'plus_addressing', 'header_domain', 'forward_parse',
        'thread_inherit', 'thread_inherit_split',
        'auto_discovery', 'manual', 'unmatched',
        'outbound_bcc'
    ));

-- Workspace outbound sender fields
ALTER TABLE workspaces
    ADD COLUMN outbound_sender_email text,
    ADD COLUMN outbound_sender_name  text;

-- outreach_drafts table
CREATE TABLE outreach_drafts (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid        NOT NULL REFERENCES workspaces(id),
    account_id        uuid        NOT NULL REFERENCES accounts(id),
    contact_id        uuid        REFERENCES contacts(id),
    intent            text        NOT NULL CHECK (intent IN ('check_in', 'expansion', 'custom')),
    user_context       text,
    subject           text        NOT NULL DEFAULT '',
    body              text        NOT NULL DEFAULT '',
    generated_by      text        NOT NULL DEFAULT 'llm' CHECK (generated_by IN ('llm', 'human')),
    status            text        NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
    sent_at           timestamptz,
    sent_by_user_id   uuid        REFERENCES users(id),
    model             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz
);

-- One active (unsent) draft per account
CREATE UNIQUE INDEX outreach_drafts_one_active_per_account
    ON outreach_drafts (account_id)
    WHERE status = 'draft' AND deleted_at IS NULL;

CREATE TRIGGER set_outreach_drafts_updated_at
    BEFORE UPDATE ON outreach_drafts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE outreach_drafts ENABLE ROW LEVEL SECURITY;

CREATE POLICY outreach_drafts_workspace_isolation ON outreach_drafts
    USING (workspace_id = current_user_workspace_id());

GRANT ALL ON outreach_drafts TO anon, authenticated, service_role;
