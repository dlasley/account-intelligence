-- Migration 000026: super-user browsing refactor
-- Replaces impersonation (workspace_id swap) with direct super-user browsing
-- via nested admin routes + SECURITY DEFINER RPCs that bypass RLS.
-- ADR-018 Amendment 2026-05-08

-- Step 1: drop impersonation RPCs (no longer needed)
DROP FUNCTION IF EXISTS public.switch_workspace_id(uuid);
DROP FUNCTION IF EXISTS public.get_my_workspace();

-- Step 2: drop home_workspace_id column (impersonation concept gone)
ALTER TABLE users DROP COLUMN IF EXISTS home_workspace_id;

-- Step 3: reset super-user workspace_id back to their primary workspace.
-- This UPDATE was removed from the migration (contained a hardcoded email address).
-- Post-deploy bootstrap step: run manually via Supabase SQL editor:
--   UPDATE users
--   SET workspace_id = (SELECT id FROM workspaces WHERE slug = 'elicit' LIMIT 1)
--   WHERE email = '<super-user-email>';
-- The migration has already run on production; this comment is for re-deploy clarity.

-- Step 4: am_i_super_user() — lightweight boolean for middleware / banner checks
-- Replaces get_my_workspace() which carried is_impersonating semantics.
CREATE OR REPLACE FUNCTION public.am_i_super_user()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT COALESCE(
        (SELECT is_super_user FROM users WHERE users.id = auth.uid()),
        false
    );
$$;

GRANT EXECUTE ON FUNCTION public.am_i_super_user() TO authenticated;
REVOKE EXECUTE ON FUNCTION public.am_i_super_user() FROM anon;

-- Step 5: list_accounts_for_workspace(p_workspace_slug text)
-- Returns the same shape as get_account_list() but for any workspace.
-- SECURITY DEFINER bypasses RLS; super-user gate in function body.
-- COALESCE on the super-user check: auth.uid() NULL (anon caller) → subquery returns
-- no rows → NULL → COALESCE to false → raise. Without COALESCE, NOT NULL = NULL
-- which is falsy in PL/pgSQL IF — the guard falls through instead of raising.
CREATE OR REPLACE FUNCTION public.list_accounts_for_workspace(p_workspace_slug text)
RETURNS TABLE(
    id                          uuid,
    workspace_id                uuid,
    slug                        text,
    name                        text,
    primary_domain              text,
    additional_domains          text[],
    vertical                    text,
    crm_record_id               text,
    status                      text,
    last_narrative_generated_at timestamptz,
    created_at                  timestamptz,
    updated_at                  timestamptz,
    overall_health_score        smallint,
    narrative_excerpt           text,
    last_signal_at              timestamptz,
    audit_passed                boolean,
    audit_criteria_passed       integer,
    audit_criteria_total        integer,
    audit_audited_at            timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
BEGIN
    IF NOT COALESCE(
        (SELECT u.is_super_user FROM users u WHERE u.id = auth.uid()),
        false
    ) THEN
        RAISE EXCEPTION 'access denied: super-user privilege required';
    END IF;

    SELECT w.id INTO v_workspace_id
    FROM workspaces w
    WHERE w.slug = p_workspace_slug AND w.deleted_at IS NULL;

    IF v_workspace_id IS NULL THEN
        RAISE EXCEPTION 'workspace not found: %', p_workspace_slug;
    END IF;

    RETURN QUERY
    SELECT
        a.id, a.workspace_id, a.slug, a.name, a.primary_domain,
        a.additional_domains, a.vertical, a.crm_record_id, a.status,
        a.last_narrative_generated_at, a.created_at, a.updated_at,
        a.overall_health_score,
        left(n.narrative, 200)                                  AS narrative_excerpt,
        sig.last_signal_at,
        aru.overall_passed                                       AS audit_passed,
        (SELECT count(*)::int
           FROM jsonb_each(aru.score_summary) kv
          WHERE (kv.value->>'passed')::boolean = true)           AS audit_criteria_passed,
        (SELECT count(*)::int
           FROM jsonb_each(aru.score_summary) kv)                AS audit_criteria_total,
        aru.audited_at                                           AS audit_audited_at
    FROM accounts a
    LEFT JOIN narratives n
           ON n.account_id = a.id AND n.superseded_at IS NULL
    LEFT JOIN LATERAL (
        SELECT aru2.overall_passed, aru2.score_summary, aru2.audited_at
        FROM narrative_audit_runs aru2
        WHERE aru2.narrative_id = n.id
        ORDER BY aru2.audited_at DESC
        LIMIT 1
    ) aru ON true
    LEFT JOIN (
        SELECT s.account_id, MAX(s.occurred_at) AS last_signal_at
        FROM   signals s
        WHERE  s.workspace_id = v_workspace_id AND s.deleted_at IS NULL
        GROUP BY s.account_id
    ) sig ON sig.account_id = a.id
    WHERE  a.workspace_id = v_workspace_id
      AND  a.deleted_at   IS NULL
      AND  a.status       IN ('active', 'candidate')
      AND  a.slug         != '_unmatched'
    ORDER BY a.overall_health_score DESC NULLS LAST,
             sig.last_signal_at     DESC NULLS LAST;
END;
$$;

GRANT EXECUTE ON FUNCTION public.list_accounts_for_workspace(text) TO authenticated;
REVOKE EXECUTE ON FUNCTION public.list_accounts_for_workspace(text) FROM anon;

-- Step 6: get_account_detail_for_workspace(p_workspace_slug text, p_account_slug text)
-- Returns full account detail as a JSON blob (account row + narrative + audit run +
-- signals + contacts + dimension scores + dimension configs).
-- One round-trip from the server component; avoids n+1 supabase calls that would each
-- need an RLS bypass.
CREATE OR REPLACE FUNCTION public.get_account_detail_for_workspace(
    p_workspace_slug text,
    p_account_slug   text
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_workspace_id uuid;
    v_account_id   uuid;
    v_result       jsonb;
BEGIN
    -- Same COALESCE pattern: handles anon (NULL uid) cleanly.
    IF NOT COALESCE(
        (SELECT u.is_super_user FROM users u WHERE u.id = auth.uid()),
        false
    ) THEN
        RAISE EXCEPTION 'access denied: super-user privilege required';
    END IF;

    SELECT w.id INTO v_workspace_id
    FROM workspaces w
    WHERE w.slug = p_workspace_slug AND w.deleted_at IS NULL;

    IF v_workspace_id IS NULL THEN
        RAISE EXCEPTION 'workspace not found: %', p_workspace_slug;
    END IF;

    SELECT a.id INTO v_account_id
    FROM accounts a
    WHERE a.workspace_id = v_workspace_id
      AND a.slug = p_account_slug
      AND a.deleted_at IS NULL;

    IF v_account_id IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT jsonb_build_object(
        'account', row_to_json(a.*),
        'narrative', (
            SELECT row_to_json(n.*)
            FROM narratives n
            WHERE n.workspace_id = v_workspace_id
              AND n.account_id = v_account_id
              AND n.superseded_at IS NULL
            LIMIT 1
        ),
        'audit_run', (
            SELECT row_to_json(aru.*)
            FROM narrative_audit_runs aru
            WHERE aru.narrative_id = (
                SELECT n2.id FROM narratives n2
                WHERE n2.workspace_id = v_workspace_id
                  AND n2.account_id = v_account_id
                  AND n2.superseded_at IS NULL
                LIMIT 1
            )
            ORDER BY aru.audited_at DESC
            LIMIT 1
        ),
        'audit_criteria', (
            SELECT jsonb_agg(row_to_json(na.*))
            FROM narrative_audits na
            WHERE na.narrative_id = (
                SELECT n3.id FROM narratives n3
                WHERE n3.workspace_id = v_workspace_id
                  AND n3.account_id = v_account_id
                  AND n3.superseded_at IS NULL
                LIMIT 1
            )
            AND na.audit_run_id = (
                SELECT aru2.audit_run_id
                FROM narrative_audit_runs aru2
                WHERE aru2.narrative_id = (
                    SELECT n4.id FROM narratives n4
                    WHERE n4.workspace_id = v_workspace_id
                      AND n4.account_id = v_account_id
                      AND n4.superseded_at IS NULL
                    LIMIT 1
                )
                ORDER BY aru2.audited_at DESC
                LIMIT 1
            )
        ),
        'signals', (
            SELECT jsonb_agg(row_to_json(s.*) ORDER BY s.occurred_at DESC)
            FROM signals s
            WHERE s.workspace_id = v_workspace_id
              AND s.account_id = v_account_id
              AND s.deleted_at IS NULL
        ),
        'contacts', (
            SELECT jsonb_agg(row_to_json(c.*))
            FROM contacts c
            WHERE c.workspace_id = v_workspace_id
              AND c.account_id = v_account_id
        ),
        'dimension_scores', (
            SELECT jsonb_agg(row_to_json(ds.*))
            FROM account_dimension_scores ds
            WHERE ds.workspace_id = v_workspace_id
              AND ds.account_id = v_account_id
              AND ds.superseded_at IS NULL
        ),
        'dimension_configs', (
            SELECT jsonb_agg(row_to_json(dc.*))
            FROM health_dimension_configs dc
            WHERE dc.workspace_id = v_workspace_id
              AND dc.deleted_at IS NULL
        )
    )
    INTO v_result
    FROM accounts a
    WHERE a.id = v_account_id;

    RETURN v_result;
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_account_detail_for_workspace(text, text) TO authenticated;
REVOKE EXECUTE ON FUNCTION public.get_account_detail_for_workspace(text, text) FROM anon;
