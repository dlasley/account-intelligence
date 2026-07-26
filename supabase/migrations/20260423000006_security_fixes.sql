-- 1. Enable RLS on tables that were missing it
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workspaces: authenticated users see own workspace"
    ON workspaces FOR SELECT TO authenticated
    USING (id = current_user_workspace_id());

CREATE POLICY "organizations: authenticated users see own org"
    ON organizations FOR SELECT TO authenticated
    USING (id = (
        SELECT organization_id FROM workspaces WHERE id = current_user_workspace_id()
    ));

-- 2. Revoke broad anon table access — anon only needs schema usage for PostgREST to function
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
GRANT USAGE ON SCHEMA public TO anon;

-- 3. Fix set_csm_score: scope the final accounts UPDATE by workspace_id
CREATE OR REPLACE FUNCTION set_csm_score(
    p_account_id uuid,
    p_score      smallint,
    p_rationale  text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_workspace_id  uuid        := current_user_workspace_id();
    v_now           timestamptz := now();
    v_dim_id        uuid;
    v_score_id      uuid        := gen_random_uuid();
    v_snapshot_id   uuid        := gen_random_uuid();
    v_overall       smallint;
BEGIN
    IF p_score < 1 OR p_score > 100 THEN
        RAISE EXCEPTION 'score must be between 1 and 100, got %', p_score;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM accounts
        WHERE  id = p_account_id AND workspace_id = v_workspace_id AND deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'account not found or access denied: %', p_account_id;
    END IF;

    SELECT id INTO v_dim_id
    FROM   health_dimension_configs
    WHERE  workspace_id = v_workspace_id
      AND  dimension_type = 'csm_score'
      AND  deleted_at IS NULL;

    IF v_dim_id IS NULL THEN
        RAISE EXCEPTION 'csm_score dimension not configured for workspace';
    END IF;

    UPDATE account_dimension_scores
    SET    superseded_at = v_now
    WHERE  account_id   = p_account_id
      AND  dimension_id = v_dim_id
      AND  superseded_at IS NULL;

    INSERT INTO account_dimension_scores (
        id, workspace_id, account_id, dimension_id,
        score, rationale, scored_by, metadata, scored_at
    ) VALUES (
        v_score_id, v_workspace_id, p_account_id, v_dim_id,
        p_score, p_rationale, 'csm', NULL, v_now
    );

    SELECT CASE WHEN SUM(hdc.weight) > 0
               THEN GREATEST(1, LEAST(100,
                        ROUND(SUM(hdc.weight * ads.score) / SUM(hdc.weight))
                    ))::smallint
               ELSE NULL
           END
    INTO   v_overall
    FROM   account_dimension_scores ads
    JOIN   health_dimension_configs  hdc ON hdc.id = ads.dimension_id
    WHERE  ads.account_id     = p_account_id
      AND  ads.superseded_at  IS NULL
      AND  hdc.workspace_id   = v_workspace_id
      AND  hdc.enabled        = true
      AND  hdc.deleted_at     IS NULL;

    UPDATE account_health_snapshots
    SET    superseded_at = v_now
    WHERE  account_id   = p_account_id
      AND  superseded_at IS NULL;

    INSERT INTO account_health_snapshots (
        id, workspace_id, account_id, overall_score,
        dimension_scores, formula_version, computed_at
    )
    SELECT
        v_snapshot_id, v_workspace_id, p_account_id, v_overall,
        jsonb_object_agg(hdc.dimension_type, ads.score),
        'weighted_average_v1', v_now
    FROM   account_dimension_scores ads
    JOIN   health_dimension_configs  hdc ON hdc.id = ads.dimension_id
    WHERE  ads.account_id    = p_account_id
      AND  ads.superseded_at IS NULL
      AND  hdc.workspace_id  = v_workspace_id
      AND  hdc.deleted_at    IS NULL;

    -- Scoped by workspace_id to prevent cross-tenant writes
    UPDATE accounts
    SET    overall_health_score = v_overall
    WHERE  id           = p_account_id
      AND  workspace_id = v_workspace_id;

    INSERT INTO audit_events (
        id, workspace_id, actor_type, actor_id, action,
        resource_type, resource_id, occurred_at, metadata
    ) VALUES (
        gen_random_uuid(), v_workspace_id, 'user', auth.uid()::text, 'csm_score_set',
        'account_dimension_score', v_score_id, v_now,
        jsonb_build_object(
            'account_id', p_account_id,
            'score', p_score,
            'dimension_type', 'csm_score'
        )
    );

    RETURN v_score_id;
END;
$$;

GRANT EXECUTE ON FUNCTION set_csm_score(uuid, smallint, text) TO authenticated;
REVOKE EXECUTE ON FUNCTION set_csm_score(uuid, smallint, text) FROM anon;
