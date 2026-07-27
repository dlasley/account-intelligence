-- Rename confidence_tier → health and confidence_rationale → health_rationale on narratives.
-- "Tier" has a specific meaning in CS (customer segmentation by ARR/strategic value)
-- and must not be used for account health scoring.

ALTER TABLE narratives RENAME COLUMN confidence_tier    TO health;
ALTER TABLE narratives RENAME COLUMN confidence_rationale TO health_rationale;

-- Recreate get_account_list() with updated column references.
DROP FUNCTION IF EXISTS get_account_list();
CREATE FUNCTION get_account_list()
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
    health                      text,
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
        n.health,
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
        CASE n.health
            WHEN 'high'   THEN 0
            WHEN 'medium' THEN 1
            WHEN 'low'    THEN 2
            ELSE               3   -- no narrative yet
        END,
        sig.last_signal_at DESC NULLS LAST;
$$;

GRANT EXECUTE ON FUNCTION get_account_list() TO authenticated;
REVOKE EXECUTE ON FUNCTION get_account_list() FROM anon;
