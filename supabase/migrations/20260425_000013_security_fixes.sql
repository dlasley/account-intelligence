-- Fix outreach_drafts RLS: add WITH CHECK so Supabase cannot accept cross-workspace writes
-- even if the client omits a workspace filter.
DROP POLICY IF EXISTS "workspace_isolation" ON outreach_drafts;
CREATE POLICY "workspace_isolation" ON outreach_drafts
    FOR ALL
    USING  (workspace_id = current_user_workspace_id())
    WITH CHECK (workspace_id = current_user_workspace_id());

-- Revoke table-level access from anon on api_keys and service_accounts.
-- RLS already protects these tables, but consistent with the grant pattern established
-- in migration 000006: anon gets only USAGE on the schema, not table-level access.
REVOKE ALL ON api_keys       FROM anon;
REVOKE ALL ON service_accounts FROM anon;
