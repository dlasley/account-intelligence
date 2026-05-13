import { notFound } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { scoreBadge } from '@/lib/utils'
import AccountViewTracker from '@/components/AccountViewTracker'
import NarrativeSection from '@/components/NarrativeSection'
import SignalsTimeline from '@/components/SignalsTimeline'
import ContactsList from '@/components/ContactsList'
import DimensionBreakdown from '@/components/DimensionBreakdown'
import AccountTabs from '@/components/AccountTabs'
import OutreachTab from '@/components/OutreachTab'
import AuditBadge from '@/components/AuditBadge'
import type { AuditCriterion } from '@/components/AuditBadge'

export default async function AccountDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const { slug } = await params
  const supabase = await createClient()

  const { data: account } = await supabase
    .from('accounts')
    .select('*')
    .eq('slug', slug)
    .is('deleted_at', null)
    .single()

  if (!account) notFound()

  const [
    { data: narrative },
    { data: signals },
    { data: contacts },
    { data: dimScores },
    { data: dimConfigs },
  ] = await Promise.all([
    supabase
      .from('narratives')
      .select('id, narrative, engagement, engagement_rationale, sentiment, generated_at')
      .eq('workspace_id', account.workspace_id)
      .eq('account_id', account.id)
      .is('superseded_at', null)
      .maybeSingle(),
    supabase
      .from('signals')
      .select('*')
      .eq('workspace_id', account.workspace_id)
      .eq('account_id', account.id)
      .is('deleted_at', null)
      .order('occurred_at', { ascending: false }),
    supabase
      .from('contacts')
      .select('*')
      .eq('workspace_id', account.workspace_id)
      .eq('account_id', account.id),
    supabase
      .from('account_dimension_scores')
      .select(`
        score, rationale, scored_by, scored_at, metadata,
        dimension_id,
        health_dimension_configs(name, dimension_type, enabled, weight)
      `)
      .eq('workspace_id', account.workspace_id)
      .eq('account_id', account.id)
      .is('superseded_at', null)
      .order('scored_at', { ascending: false }),
    supabase
      .from('health_dimension_configs')
      .select('id, dimension_type, name, weight, enabled')
      .eq('workspace_id', account.workspace_id)
      .is('deleted_at', null),
  ])

  // Fetch the most-recent audit run for the current narrative, plus the 5 criteria rows
  let auditRun: {
    audit_run_id: string
    overall_passed: boolean
    hard_gate_failures: number
    advisory_failures: number
    score_summary: Record<string, { passed: boolean; score?: number }>
    audited_at: string
  } | null = null
  let auditCriteria: AuditCriterion[] = []

  if (narrative?.id) {
    const { data: runRow } = await supabase
      .from('narrative_audit_runs')
      .select('audit_run_id, overall_passed, hard_gate_failures, advisory_failures, score_summary, audited_at')
      .eq('narrative_id', narrative.id)
      .order('audited_at', { ascending: false })
      .limit(1)
      .maybeSingle()

    auditRun = runRow ?? null

    if (runRow) {
      const { data: criteriaRows } = await supabase
        .from('narrative_audits')
        .select('criterion, passed, score, reasoning')
        .eq('narrative_id', narrative.id)
        .eq('audit_run_id', runRow.audit_run_id)
        .order('criterion', { ascending: true })

      auditCriteria = (criteriaRows ?? []).map((r) => ({
        criterion: r.criterion as string,
        passed: r.passed as boolean,
        score: r.score as number | null,
        reasoning: r.reasoning as string,
      }))
    }
  }

  const hasCsmConfig = (dimConfigs ?? []).some(
    (d: { dimension_type: string; enabled: boolean }) =>
      d.dimension_type === 'csm_score' && d.enabled
  )

  const healthBadge = scoreBadge(account.overall_health_score ?? null)

  const externalContacts = (contacts ?? []).filter(
    (c: { is_internal: boolean }) => !c.is_internal
  )

  // Derive criteria pass count from score_summary for the header badge
  const auditCriteriaTotal = auditRun
    ? Object.keys(auditRun.score_summary).length
    : null
  const auditCriteriaPassed = auditRun
    ? Object.values(auditRun.score_summary).filter((v) => v.passed).length
    : null

  // Pass narrative-level audit info to NarrativeSection
  const narrativeWithAudit = narrative
    ? {
        ...narrative,
        auditPassed: auditRun?.overall_passed ?? null,
        auditCriteriaPassed,
        auditCriteriaTotal,
        auditAuditedAt: auditRun?.audited_at ?? null,
      }
    : null

  const overviewContent = (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-8">
        <NarrativeSection narrative={narrativeWithAudit} accountId={account.id} workspaceId={account.workspace_id} />
        <DimensionBreakdown
          accountId={account.id}
          dimensionScores={dimScores ?? []}
          dimensionConfigs={dimConfigs ?? []}
          hasCsmConfig={hasCsmConfig}
        />
        <SignalsTimeline signals={signals ?? []} />
      </div>
      <div>
        <ContactsList contacts={contacts ?? []} />
      </div>
    </div>
  )

  const outreachContent = (
    <OutreachTab
      accountSlug={account.slug}
      accountId={account.id}
      contacts={externalContacts}
      overallHealthScore={account.overall_health_score ?? null}
    />
  )

  return (
    <main className="p-8 max-w-5xl">
      <AccountViewTracker
        accountId={account.id}
        accountSlug={account.slug}
        overallHealthScore={account.overall_health_score ?? null}
      />
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1 flex-wrap">
          <h1 className="text-2xl font-bold">{account.name}</h1>
          {account.vertical && (
            <span className="px-2 py-0.5 rounded text-sm bg-blue-50 text-blue-700">
              {account.vertical}
            </span>
          )}
          {account.overall_health_score != null && (
            <span className={`px-2 py-0.5 rounded text-sm font-medium ${healthBadge.color}`}>
              Health {account.overall_health_score} {healthBadge.label}
            </span>
          )}
          <AuditBadge
            passed={auditRun?.overall_passed ?? null}
            criteriaPassedCount={auditCriteriaPassed}
            criteriaTotal={auditCriteriaTotal}
            auditedAt={auditRun?.audited_at ?? null}
            criteria={auditCriteria}
            variant="badge"
          />
        </div>
        {account.crm_record_id && (
          <p className="text-sm text-gray-500">
            CRM:{' '}
            <span className="font-mono text-gray-700">{account.crm_record_id}</span>
          </p>
        )}
      </div>

      <AccountTabs
        overviewContent={overviewContent}
        outreachContent={outreachContent}
        accountId={account.id}
        overallHealthScore={account.overall_health_score ?? null}
      />
    </main>
  )
}
