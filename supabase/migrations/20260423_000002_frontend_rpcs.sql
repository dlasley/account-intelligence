-- Frontend RPC functions for Phase 4.
-- Both run as SECURITY INVOKER (default) so the caller's RLS context applies.
-- current_user_workspace_id() is SECURITY DEFINER and resolves the caller's workspace.

-- ---------------------------------------------------------------------------
-- get_account_list()
--
-- Returns all active + candidate accounts for the authenticated user's workspace,
-- joined with the current narrative (confidence_tier, excerpt) and last signal time.
-- Excludes the _unmatched pseudo-account and archived / deleted accounts.
-- Sort: confidence tier (high → medium → low → null), then last_signal_at DESC.
--
-- Call from frontend: supabase.rpc('get_account_list')
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_account_list()
RETURNS TABLE (
    id                          uuid,
    workspace_id                uuid,
    slug                        text,
    name                        text,
    primary_domain              text,
    additional_domains          text[],
    vertical                    text,
    crm_slug                    text,
    status                      text,
    last_narrative_generated_at timestamptz,
    created_at                  timestamptz,
    updated_at                  timestamptz,
    -- joined from narratives (NULL if no narrative yet)
    confidence_tier             text,
    narrative_excerpt           text,   -- first 200 chars; truncate in UI
    -- derived from signals (NULL if no signals yet)
    last_signal_at              timestamptz
)
LANGUAGE sql STABLE
SET search_path = public
AS $$
    SELECT
        a.id,
        a.workspace_id,
        a.slug,
        a.name,
        a.primary_domain,
        a.additional_domains,
        a.vertical,
        a.crm_slug,
        a.status,
        a.last_narrative_generated_at,
        a.created_at,
        a.updated_at,
        n.confidence_tier,
        left(n.narrative, 200)  AS narrative_excerpt,
        sig.last_signal_at
    FROM accounts a
    LEFT JOIN narratives n
           ON n.account_id = a.id
          AND n.superseded_at IS NULL
    LEFT JOIN (
        SELECT   account_id, MAX(occurred_at) AS last_signal_at
        FROM     signals
        WHERE    workspace_id = current_user_workspace_id()
          AND    deleted_at   IS NULL
        GROUP BY account_id
    ) sig ON sig.account_id = a.id
    WHERE  a.workspace_id = current_user_workspace_id()
      AND  a.deleted_at   IS NULL
      AND  a.status       IN ('active', 'candidate')
      AND  a.slug         != '_unmatched'
    ORDER BY
        CASE n.confidence_tier
            WHEN 'high'   THEN 0
            WHEN 'medium' THEN 1
            WHEN 'low'    THEN 2
            ELSE               3   -- no narrative yet
        END,
        sig.last_signal_at DESC NULLS LAST;
$$;

-- ---------------------------------------------------------------------------
-- enqueue_narrative_regen(p_account_id uuid)
--
-- Inserts a narrative_regen_jobs row for the given account, enforcing the
-- same debounce (60 s) and rate cap (10 min, DONE-only) as the Python worker.
-- Returns the new job id, or NULL if the request was debounced.
--
-- Verifies the account belongs to the caller's workspace before inserting.
-- Call from frontend: supabase.rpc('enqueue_narrative_regen', { p_account_id: '...' })
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION enqueue_narrative_regen(p_account_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_workspace_id  uuid        := current_user_workspace_id();
    v_now           timestamptz := now();
    v_scheduled_for timestamptz;
    v_last_done     timestamptz;
    v_job_id        uuid;
BEGIN
    -- Verify the account belongs to the caller's workspace.
    IF NOT EXISTS (
        SELECT 1 FROM accounts
        WHERE  id           = p_account_id
          AND  workspace_id = v_workspace_id
          AND  deleted_at   IS NULL
    ) THEN
        RAISE EXCEPTION 'account not found or access denied: %', p_account_id;
    END IF;

    -- 1. Debounce: a pending job is already scheduled in the future.
    IF EXISTS (
        SELECT 1 FROM narrative_regen_jobs
        WHERE  workspace_id   = v_workspace_id
          AND  account_id     = p_account_id
          AND  status         = 'pending'
          AND  scheduled_for  > v_now
    ) THEN
        RETURN NULL;
    END IF;

    -- 2. Rate cap: a DONE job completed within the last 10 minutes.
    SELECT last_updated_at INTO v_last_done
    FROM   narrative_regen_jobs
    WHERE  workspace_id   = v_workspace_id
      AND  account_id     = p_account_id
      AND  status         = 'done'
      AND  last_updated_at > v_now - interval '10 minutes'
    ORDER  BY last_updated_at DESC
    LIMIT  1;

    IF FOUND THEN
        v_scheduled_for := v_last_done + interval '10 minutes';
    ELSE
        v_scheduled_for := v_now + interval '60 seconds';
    END IF;

    -- 3. Insert the job.
    v_job_id := gen_random_uuid();

    INSERT INTO narrative_regen_jobs (
        id, workspace_id, account_id,
        requested_at, scheduled_for,
        status, triggered_by, triggered_by_user_id,
        last_updated_at
    ) VALUES (
        v_job_id, v_workspace_id, p_account_id,
        v_now, v_scheduled_for,
        'pending', 'manual', auth.uid(),
        v_now
    );

    RETURN v_job_id;
END;
$$;

-- Grant execute to authenticated users only.
-- anon (unauthenticated) must not be able to call either function.
GRANT EXECUTE ON FUNCTION get_account_list()                    TO authenticated;
GRANT EXECUTE ON FUNCTION enqueue_narrative_regen(uuid)         TO authenticated;
REVOKE EXECUTE ON FUNCTION get_account_list()                   FROM anon;
REVOKE EXECUTE ON FUNCTION enqueue_narrative_regen(uuid)        FROM anon;
