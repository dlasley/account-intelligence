-- Make two implicit protections explicit. Neither changes behaviour today;
-- both remove a reliance on a default that a future reader would have to know.
--
-- Found by an introspection pass against the live database, not by reading
-- migrations. See .private/architect/2026-08-27-live-schema-audit.md.

-- 1. WITH CHECK on every workspace-isolation policy.
--
-- These are FOR ALL policies carrying only USING. Postgres applies the USING
-- expression as the row check when WITH CHECK is omitted, so the write path is
-- already constrained to the caller's workspace and this is not a fix for a
-- live hole. It is stated explicitly because the repo convention is that every
-- workspace-isolation policy carries both, and because a later edit that
-- narrows USING for visibility reasons would silently narrow the write check
-- with it.
ALTER POLICY workspace_isolation ON account_dimension_scores
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON account_health_snapshots
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON accounts
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON api_keys
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON audit_events
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON contacts
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON health_dimension_configs
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON narrative_regen_jobs
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON narratives
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY outreach_drafts_workspace_isolation ON outreach_drafts
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON raw_inbound_events
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON service_accounts
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON signals
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());
ALTER POLICY workspace_isolation ON users
    USING (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

-- 2. REVOKE EXECUTE from anon on the super-user-gated functions only.
--
-- Postgres grants EXECUTE on functions to PUBLIC by default, so these were
-- reachable by anon. Each already refuses an anon caller from inside its body
-- (the super-user gate wraps its subquery in COALESCE(..., false), so a NULL
-- auth.uid() collapses to false), which is why this is defence in depth rather
-- than a fix.
--
-- Deliberately NOT revoked: current_user_workspace_id(). It is called by 21 RLS
-- policies, and policy expressions execute with the caller's privileges — so
-- revoking it from anon would make those policies raise a permission error
-- instead of returning no rows, breaking every unauthenticated query rather
-- than hardening it.
REVOKE EXECUTE ON FUNCTION am_i_super_user() FROM anon;
REVOKE EXECUTE ON FUNCTION list_all_workspaces_with_metadata() FROM anon;
