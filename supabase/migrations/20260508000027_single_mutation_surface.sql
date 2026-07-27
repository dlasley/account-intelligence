-- Migration 000027: Single Mutation Surface — PostgREST Read-Only Tables, Mutations via RPCs
-- ADR-019
--
-- Operations:
--   1. Create three SECURITY DEFINER RPCs replacing the last two authenticated direct-table writes.
--   2. REVOKE INSERT, UPDATE, DELETE from authenticated on every public table.
--      (users already handled by migration 000025 — not re-listed here.)
--   3. REVOKE anon convention violations on outreach_drafts.
--
-- After this migration, authenticated role's table surface is SELECT-only.
-- All mutations route through SECURITY DEFINER RPCs (this file) or service_role (Python worker).


-- ─── Step 1: New SECURITY DEFINER RPCs ──────────────────────────────────────

-- activate_candidate_account
-- Promotes a candidate account to active status.
-- Validates workspace ownership and status precondition before updating.
CREATE OR REPLACE FUNCTION activate_candidate_account(p_account_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
BEGIN
    SELECT workspace_id INTO v_workspace_id
    FROM   accounts
    WHERE  id = p_account_id AND deleted_at IS NULL;

    IF v_workspace_id IS NULL THEN
        RAISE EXCEPTION 'account not found: %', p_account_id;
    END IF;

    IF v_workspace_id IS DISTINCT FROM current_user_workspace_id() THEN
        RAISE EXCEPTION 'access denied: account belongs to a different workspace';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM accounts
        WHERE  id = p_account_id AND status = 'candidate'
    ) THEN
        RAISE EXCEPTION 'account is not in candidate status';
    END IF;

    UPDATE accounts
    SET    status = 'active'
    WHERE  id           = p_account_id
      AND  workspace_id = v_workspace_id;
END;
$$;

GRANT EXECUTE ON FUNCTION activate_candidate_account(uuid) TO authenticated;
REVOKE EXECUTE ON FUNCTION activate_candidate_account(uuid) FROM anon;


-- dismiss_candidate_account
-- Soft-deletes a candidate account by setting deleted_at.
CREATE OR REPLACE FUNCTION dismiss_candidate_account(p_account_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
BEGIN
    SELECT workspace_id INTO v_workspace_id
    FROM   accounts
    WHERE  id = p_account_id AND deleted_at IS NULL;

    IF v_workspace_id IS NULL THEN
        RAISE EXCEPTION 'account not found or already dismissed: %', p_account_id;
    END IF;

    IF v_workspace_id IS DISTINCT FROM current_user_workspace_id() THEN
        RAISE EXCEPTION 'access denied: account belongs to a different workspace';
    END IF;

    UPDATE accounts
    SET    deleted_at = now()
    WHERE  id           = p_account_id
      AND  workspace_id = v_workspace_id
      AND  deleted_at   IS NULL;
END;
$$;

GRANT EXECUTE ON FUNCTION dismiss_candidate_account(uuid) TO authenticated;
REVOKE EXECUTE ON FUNCTION dismiss_candidate_account(uuid) FROM anon;


-- update_outreach_draft
-- Updates one or more fields on an outreach draft.
-- NULL parameters mean "leave this field unchanged" (COALESCE semantics).
-- contact_id uses CASE so a future explicit NULL-clear can be added without changing
-- the signature; for now, passing NULL leaves contact_id unchanged (per ADR-019 D8).
CREATE OR REPLACE FUNCTION update_outreach_draft(
    p_draft_id    uuid,
    p_subject     text DEFAULT NULL,
    p_body        text DEFAULT NULL,
    p_intent      text DEFAULT NULL,
    p_template_id text DEFAULT NULL,
    p_contact_id  uuid DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
BEGIN
    SELECT workspace_id INTO v_workspace_id
    FROM   outreach_drafts
    WHERE  id = p_draft_id;

    IF v_workspace_id IS NULL THEN
        RAISE EXCEPTION 'draft not found: %', p_draft_id;
    END IF;

    IF v_workspace_id IS DISTINCT FROM current_user_workspace_id() THEN
        RAISE EXCEPTION 'access denied: draft belongs to a different workspace';
    END IF;

    UPDATE outreach_drafts
    SET
        subject     = COALESCE(p_subject,     subject),
        body        = COALESCE(p_body,        body),
        intent      = COALESCE(p_intent,      intent),
        template_id = COALESCE(p_template_id, template_id),
        contact_id  = CASE WHEN p_contact_id IS NOT NULL
                          THEN p_contact_id
                          ELSE contact_id
                     END
    WHERE  id           = p_draft_id
      AND  workspace_id = v_workspace_id;
END;
$$;

GRANT EXECUTE ON FUNCTION update_outreach_draft(uuid, text, text, text, text, uuid) TO authenticated;
REVOKE EXECUTE ON FUNCTION update_outreach_draft(uuid, text, text, text, text, uuid) FROM anon;


-- ─── Step 2: REVOKE all DML writes from authenticated across all public tables ──
--
-- Frontend reads (SELECT) are retained; all mutations route through
-- SECURITY DEFINER RPCs (above) or the Python worker (service_role).
-- Codebase audit 2026-05-08 confirmed zero authenticated-role INSERT/UPDATE/DELETE
-- paths in the frontend or worker outside the two tables whose writes are
-- replaced by RPCs in step 1 above.
--
-- `users` is intentionally omitted: migration 000025 (ADR-018) already issued
-- REVOKE UPDATE, INSERT, DELETE ON users FROM authenticated. Re-listing it here
-- is a no-op in Postgres but muddies intent.

REVOKE INSERT, UPDATE, DELETE ON
    account_dimension_scores,
    account_health_snapshots,
    accounts,
    api_keys,
    audit_events,
    contacts,
    health_dimension_configs,
    narrative_audit_runs,
    narrative_audits,
    narrative_regen_jobs,
    narratives,
    organizations,
    outreach_drafts,
    raw_inbound_events,
    service_accounts,
    signals,
    workspaces
FROM authenticated;


-- ─── Step 3: anon role cleanup ───────────────────────────────────────────────
--
-- outreach_drafts carried GRANT ALL to anon, violating the project convention
-- (CLAUDE.md §Migrations: no table-level access to anon). Remove it.
--
-- narrative_audit_runs + narrative_audits carry stray non-DML anon grants
-- (MAINTAIN, REFERENCES, TRIGGER, TRUNCATE) — not exploitable via PostgREST
-- but unconventional. Removed for principle-of-least-privilege consistency
-- per the audit doc (.private/security-reviewer/postgrest-exposure-audit-2026-05-08.md).

REVOKE ALL ON outreach_drafts FROM anon;
REVOKE ALL ON narrative_audit_runs, narrative_audits FROM anon;


-- ─── Rollback reference (do not apply — for emergency use only) ──────────────
--
-- To undo the REVOKE pass (step 2 and 3), run:
--
--   GRANT INSERT, UPDATE, DELETE ON
--       account_dimension_scores, account_health_snapshots, accounts, api_keys,
--       audit_events, contacts, health_dimension_configs, narrative_audit_runs,
--       narrative_audits, narrative_regen_jobs, narratives, organizations,
--       outreach_drafts, raw_inbound_events, service_accounts, signals, workspaces
--   TO authenticated;
--
--   GRANT ALL ON outreach_drafts TO anon;
--
-- The three RPCs created in step 1 are safe to retain even after rollback;
-- they do no harm alongside restored direct write access.
