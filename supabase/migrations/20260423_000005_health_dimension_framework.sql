-- 1a. health_dimension_configs
CREATE TABLE health_dimension_configs (
    id             uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id   uuid        NOT NULL REFERENCES workspaces(id),
    dimension_type text        NOT NULL
                               CHECK (dimension_type IN (
                                   'email', 'support_ticket', 'slack',
                                   'platform_event', 'custom_goal', 'csm_score'
                               )),
    name           text        NOT NULL,
    weight         numeric     NOT NULL CHECK (weight >= 0),
    enabled        boolean     NOT NULL DEFAULT true,
    config         jsonb       NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz
);

CREATE UNIQUE INDEX health_dimension_configs_workspace_type_uidx
    ON health_dimension_configs (workspace_id, dimension_type)
    WHERE deleted_at IS NULL;

CREATE TRIGGER set_health_dimension_configs_updated_at
    BEFORE UPDATE ON health_dimension_configs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE health_dimension_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "workspace_isolation" ON health_dimension_configs
    FOR ALL USING (workspace_id = current_user_workspace_id());

-- 1b. account_dimension_scores
CREATE TABLE account_dimension_scores (
    id            uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id  uuid        NOT NULL REFERENCES workspaces(id),
    account_id    uuid        NOT NULL REFERENCES accounts(id),
    dimension_id  uuid        NOT NULL REFERENCES health_dimension_configs(id),
    score         smallint    NOT NULL CHECK (score BETWEEN 1 AND 100),
    rationale     text,
    scored_by     text        NOT NULL CHECK (scored_by IN ('system', 'llm', 'csm')),
    metadata      jsonb,
    scored_at     timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz
);

CREATE INDEX account_dimension_scores_account_current_idx
    ON account_dimension_scores (workspace_id, account_id, scored_at DESC)
    WHERE superseded_at IS NULL;

CREATE INDEX account_dimension_scores_dimension_current_idx
    ON account_dimension_scores (account_id, dimension_id)
    WHERE superseded_at IS NULL;

ALTER TABLE account_dimension_scores ENABLE ROW LEVEL SECURITY;
CREATE POLICY "workspace_isolation" ON account_dimension_scores
    FOR ALL USING (workspace_id = current_user_workspace_id());

-- 1c. account_health_snapshots
CREATE TABLE account_health_snapshots (
    id               uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id     uuid        NOT NULL REFERENCES workspaces(id),
    account_id       uuid        NOT NULL REFERENCES accounts(id),
    overall_score    smallint    CHECK (overall_score BETWEEN 1 AND 100),
    dimension_scores jsonb       NOT NULL DEFAULT '{}',
    formula_version  text        NOT NULL DEFAULT 'weighted_average_v1',
    computed_at      timestamptz NOT NULL DEFAULT now(),
    superseded_at    timestamptz
);

CREATE INDEX account_health_snapshots_account_current_idx
    ON account_health_snapshots (workspace_id, account_id, computed_at DESC)
    WHERE superseded_at IS NULL;

ALTER TABLE account_health_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "workspace_isolation" ON account_health_snapshots
    FOR ALL USING (workspace_id = current_user_workspace_id());

-- 1d. accounts.overall_health_score
ALTER TABLE accounts ADD COLUMN overall_health_score smallint
    CHECK (overall_health_score BETWEEN 1 AND 100);

-- 1e. set_csm_score() RPC
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

    UPDATE accounts SET overall_health_score = v_overall WHERE id = p_account_id;

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

-- 1f. Recreate get_account_list()
DROP FUNCTION IF EXISTS get_account_list();
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
    overall_health_score        smallint,
    narrative_excerpt           text,
    last_signal_at              timestamptz
)
LANGUAGE sql STABLE
SET search_path = public
AS $$
    SELECT
        a.id, a.workspace_id, a.slug, a.name, a.primary_domain,
        a.additional_domains, a.vertical, a.crm_slug, a.status,
        a.last_narrative_generated_at, a.created_at, a.updated_at,
        a.overall_health_score,
        left(n.narrative, 200) AS narrative_excerpt,
        sig.last_signal_at
    FROM accounts a
    LEFT JOIN narratives n
           ON n.account_id = a.id AND n.superseded_at IS NULL
    LEFT JOIN (
        SELECT account_id, MAX(occurred_at) AS last_signal_at
        FROM   signals
        WHERE  workspace_id = current_user_workspace_id() AND deleted_at IS NULL
        GROUP BY account_id
    ) sig ON sig.account_id = a.id
    WHERE  a.workspace_id = current_user_workspace_id()
      AND  a.deleted_at   IS NULL
      AND  a.status       IN ('active', 'candidate')
      AND  a.slug         != '_unmatched'
    ORDER BY a.overall_health_score DESC NULLS LAST,
             sig.last_signal_at     DESC NULLS LAST;
$$;

GRANT EXECUTE ON FUNCTION get_account_list() TO authenticated;
REVOKE EXECUTE ON FUNCTION get_account_list() FROM anon;

-- 1g. PostgREST grants for new tables
GRANT ALL ON TABLE health_dimension_configs TO authenticated, service_role;
GRANT ALL ON TABLE account_dimension_scores TO authenticated, service_role;
GRANT ALL ON TABLE account_health_snapshots TO authenticated, service_role;
