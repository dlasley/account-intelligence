-- Fix crm_slug → crm_record_id rename in get_account_list RPC.
-- Also extend the return set with latest audit verdict columns per account
-- so the AccountTable can render audit pills without a second round-trip.

DROP FUNCTION IF EXISTS public.get_account_list();

CREATE FUNCTION public.get_account_list()
RETURNS TABLE(
    id uuid,
    workspace_id uuid,
    slug text,
    name text,
    primary_domain text,
    additional_domains text[],
    vertical text,
    crm_record_id text,
    status text,
    last_narrative_generated_at timestamptz,
    created_at timestamptz,
    updated_at timestamptz,
    overall_health_score smallint,
    narrative_excerpt text,
    last_signal_at timestamptz,
    audit_passed boolean,
    audit_criteria_passed integer,
    audit_criteria_total integer,
    audit_audited_at timestamptz
)
LANGUAGE sql
STABLE
SET search_path TO 'public'
AS $$
    SELECT
        a.id, a.workspace_id, a.slug, a.name, a.primary_domain,
        a.additional_domains, a.vertical, a.crm_record_id, a.status,
        a.last_narrative_generated_at, a.created_at, a.updated_at,
        a.overall_health_score,
        left(n.narrative, 200) AS narrative_excerpt,
        sig.last_signal_at,
        aru.overall_passed                              AS audit_passed,
        (SELECT count(*)::int
           FROM jsonb_each(aru.score_summary) kv
          WHERE (kv.value->>'passed')::boolean = true) AS audit_criteria_passed,
        (SELECT count(*)::int
           FROM jsonb_each(aru.score_summary) kv)      AS audit_criteria_total,
        aru.audited_at                                  AS audit_audited_at
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

GRANT EXECUTE ON FUNCTION public.get_account_list() TO authenticated, service_role;
