-- Migration 000025: super-user role and admin view RPCs
-- ADR-018

-- Step 1: add columns to users
ALTER TABLE users
    ADD COLUMN is_super_user     boolean NOT NULL DEFAULT false,
    ADD COLUMN home_workspace_id uuid    REFERENCES workspaces(id);

-- Step 2: list_all_workspaces_with_metadata()
-- SECURITY DEFINER so it bypasses RLS for the cross-workspace read.
-- Super-user gate in the function body rejects non-super-users with a clean exception.
CREATE OR REPLACE FUNCTION list_all_workspaces_with_metadata()
RETURNS TABLE (
    id                   uuid,
    slug                 text,
    name                 text,
    account_count        bigint,
    active_account_count bigint,
    last_narrative_at    timestamptz,
    last_signal_at       timestamptz,
    created_at           timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- users.id qualifier required because the RETURNS TABLE column `id`
    -- shadows users.id in PL/pgSQL scope, which makes a bare `id` ambiguous.
    -- COALESCE protects against NULL fall-through: if auth.uid() is NULL (anon
    -- caller, or transient signup state with no users row yet), the subquery
    -- returns NULL → NOT NULL = NULL → PL/pgSQL treats NULL as false → gate
    -- silently passes. COALESCE(..., false) collapses NULL to false explicitly.
    IF NOT COALESCE(
        (SELECT is_super_user FROM users WHERE users.id = auth.uid()),
        false
    ) THEN
        RAISE EXCEPTION 'access denied: super-user privilege required';
    END IF;

    RETURN QUERY
    SELECT
        w.id,
        w.slug,
        w.name,
        COUNT(a.id)                                         AS account_count,
        COUNT(a.id) FILTER (WHERE a.status = 'active')     AS active_account_count,
        MAX(a.last_narrative_generated_at)                  AS last_narrative_at,
        MAX(sig.last_signal_at)                             AS last_signal_at,
        w.created_at
    FROM workspaces w
    LEFT JOIN accounts a
           ON a.workspace_id = w.id AND a.deleted_at IS NULL
    LEFT JOIN (
        SELECT workspace_id, MAX(occurred_at) AS last_signal_at
        FROM   signals
        WHERE  deleted_at IS NULL
        GROUP BY workspace_id
    ) sig ON sig.workspace_id = w.id
    WHERE w.deleted_at IS NULL
    GROUP BY w.id, w.slug, w.name, w.created_at
    ORDER BY w.name;
END;
$$;

-- Step 3: switch_workspace_id(p_workspace_id uuid)
-- Swaps the super-user's workspace_id and writes an audit event.
-- current_user_workspace_id() is captured before the UPDATE so metadata records
-- the source workspace.
CREATE OR REPLACE FUNCTION switch_workspace_id(p_workspace_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_caller_id   uuid := auth.uid();
    v_from_ws_id  uuid := current_user_workspace_id();
BEGIN
    IF NOT (SELECT is_super_user FROM users WHERE id = v_caller_id) THEN
        RAISE EXCEPTION 'access denied: super-user privilege required';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM workspaces
        WHERE id = p_workspace_id AND deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'workspace not found: %', p_workspace_id;
    END IF;

    UPDATE users
    SET    workspace_id = p_workspace_id
    WHERE  id = v_caller_id;

    INSERT INTO audit_events (
        id, workspace_id, actor_type, actor_id, action,
        resource_type, resource_id, occurred_at, metadata
    ) VALUES (
        gen_random_uuid(),
        p_workspace_id,
        'user',
        v_caller_id::text,
        'super_user.impersonate',
        'workspace',
        p_workspace_id,
        now(),
        jsonb_build_object('switched_from', v_from_ws_id)
    );
END;
$$;

-- Step 4: get_my_workspace()
-- Returns current workspace info plus is_impersonating flag for the banner.
CREATE OR REPLACE FUNCTION get_my_workspace()
RETURNS TABLE (
    workspace_id      uuid,
    workspace_slug    text,
    workspace_name    text,
    is_super_user     boolean,
    home_workspace_id uuid,
    is_impersonating  boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        w.id                                                               AS workspace_id,
        w.slug                                                             AS workspace_slug,
        w.name                                                             AS workspace_name,
        u.is_super_user,
        u.home_workspace_id,
        (u.is_super_user AND u.workspace_id IS DISTINCT FROM u.home_workspace_id)
                                                                           AS is_impersonating
    FROM users u
    JOIN workspaces w ON w.id = u.workspace_id
    WHERE u.id = auth.uid();
$$;

-- Step 5: grants
GRANT EXECUTE ON FUNCTION list_all_workspaces_with_metadata() TO authenticated;
REVOKE EXECUTE ON FUNCTION list_all_workspaces_with_metadata() FROM anon;

GRANT EXECUTE ON FUNCTION switch_workspace_id(uuid) TO authenticated;
REVOKE EXECUTE ON FUNCTION switch_workspace_id(uuid) FROM anon;

GRANT EXECUTE ON FUNCTION get_my_workspace() TO authenticated;
REVOKE EXECUTE ON FUNCTION get_my_workspace() FROM anon;

-- Step 6: close the entire write surface on users to the authenticated role.
-- Codebase audit (2026-05-08) confirmed zero authenticated-role mutations to
-- this table; all writes route through SECURITY DEFINER RPCs or service_role.
-- SELECT is retained (PostHogProvider.tsx, middleware).
REVOKE UPDATE, INSERT, DELETE ON users FROM authenticated;
