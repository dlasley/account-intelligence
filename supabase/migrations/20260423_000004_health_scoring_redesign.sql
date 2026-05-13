-- ADR-004: Health Scoring Redesign
-- Splits health into engagement (deterministic, numeric) + sentiment (LLM, numeric).
-- Stores 1-100 integers; labels are a display-time concern derived from config bands.

-- 1. engagement column: rename + retype (text → smallint) + backfill
ALTER TABLE narratives RENAME COLUMN health TO engagement_text;
ALTER TABLE narratives ADD COLUMN engagement smallint;
UPDATE narratives SET engagement = CASE engagement_text
    WHEN 'high'   THEN 90
    WHEN 'medium' THEN 50
    ELSE               10   -- 'low' or any legacy value
END;
ALTER TABLE narratives ALTER COLUMN engagement SET NOT NULL;
ALTER TABLE narratives ADD CONSTRAINT narratives_engagement_check
    CHECK (engagement BETWEEN 1 AND 100);
ALTER TABLE narratives DROP COLUMN engagement_text;

-- 2. engagement_rationale: rename
ALTER TABLE narratives RENAME COLUMN health_rationale TO engagement_rationale;

-- 3. sentiment: add nullable
ALTER TABLE narratives ADD COLUMN sentiment smallint
    CHECK (sentiment BETWEEN 1 AND 100);

-- 4. frequency_multiplier on accounts
ALTER TABLE accounts ADD COLUMN frequency_multiplier numeric NOT NULL DEFAULT 1.0
    CHECK (frequency_multiplier > 0);

-- 5. Recreate get_account_list() with new columns + composite sort
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
    engagement                  smallint,
    sentiment                   smallint,
    narrative_excerpt           text,
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
        n.engagement,
        n.sentiment,
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
        (
            -- engagement null = no narrative yet → 0 sorts below even LOW (10).
            -- sentiment null = not yet assessed → 50 (neutral, same as MEDIUM).
            COALESCE(n.engagement, 0) * 0.6
            + COALESCE(n.sentiment, 50) * 0.4
        ) DESC,
        sig.last_signal_at DESC NULLS LAST;
$$;

GRANT EXECUTE ON FUNCTION get_account_list() TO authenticated;
REVOKE EXECUTE ON FUNCTION get_account_list() FROM anon;
