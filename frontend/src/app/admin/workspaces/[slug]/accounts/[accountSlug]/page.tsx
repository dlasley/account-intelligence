import Link from 'next/link'
import { notFound } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { scoreBadge } from '@/lib/utils'
import NarrativeSection from '@/components/NarrativeSection'
import SignalsTimeline from '@/components/SignalsTimeline'
import ContactsList from '@/components/ContactsList'
import DimensionBreakdown from '@/components/DimensionBreakdown'
import AuditBadge from '@/components/AuditBadge'
import type { AuditCriterion } from '@/components/AuditBadge'

// OutreachTab is intentionally omitted: drafting outreach for an account in another
// workspace from the admin view is out-of-scope. The overview (narrative, signals,
// contacts, dimensions) is the relevant read-only content.

export default async function AdminAccountDetailPage({
  params,
}: {
  params: Promise<{ slug: string; accountSlug: string }>
}) {
  const { slug, accountSlug } = await params
  const supabase = await createClient()

  const { data: blob, error } = await supabase.rpc('get_account_detail_for_workspace', {
    p_workspace_slug: slug,
    p_account_slug: accountSlug,
  })

  if (error) {
    if (error.message.includes('workspace not found')) notFound()
    return (
      <main className="p-8">
        <p className="text-red-600">Failed to load account: {error.message}</p>
      </main>
    )
  }

  if (!blob) notFound()

  const account = blob.account as {
    id: string
    workspace_id: string
    slug: string
    name: string
    vertical: string | null
    overall_health_score: number | null
    crm_record_id: string | null
  }

  const narrative = blob.narrative as {
    id: string
    narrative: string
    engagement: number
    engagement_rationale: string
    sentiment: number | null
    generated_at: string
  } | null

  const auditRun = blob.audit_run as {
    audit_run_id: string
    overall_passed: boolean
    hard_gate_failures: number
    advisory_failures: number
    score_summary: Record<string, { passed: boolean; score?: number }>
    audited_at: string
  } | null

  const auditCriteria: AuditCriterion[] = ((blob.audit_criteria ?? []) as Array<{
    criterion: string
    passed: boolean
    score: number | null
    reasoning: string
  }>).map((r) => ({
    criterion: r.criterion,
    passed: r.passed,
    score: r.score,
    reasoning: r.reasoning,
  }))

  const signals = (blob.signals ?? []) as unknown[]
  const contacts = (blob.contacts ?? []) as Array<{ is_internal: boolean }>
  const dimScores = (blob.dimension_scores ?? []) as unknown[]
  const dimConfigs = (blob.dimension_configs ?? []) as Array<{
    id: string
    dimension_type: string
    name: string
    weight: number
    enabled: boolean
  }>

  const healthBadge = scoreBadge(account.overall_health_score ?? null)

  const auditCriteriaTotal = auditRun ? Object.keys(auditRun.score_summary).length : null
  const auditCriteriaPassed = auditRun
    ? Object.values(auditRun.score_summary).filter((v) => v.passed).length
    : null

  const narrativeWithAudit = narrative
    ? {
        ...narrative,
        auditPassed: auditRun?.overall_passed ?? null,
        auditCriteriaPassed,
        auditCriteriaTotal,
        auditAuditedAt: auditRun?.audited_at ?? null,
      }
    : null

  const hasCsmConfig = dimConfigs.some(
    (d) => d.dimension_type === 'csm_score' && d.enabled
  )

  // Derive workspace display name
  const { data: wsRows } = await supabase.rpc('list_all_workspaces_with_metadata')
  const workspace = (wsRows ?? []).find((w: { slug: string; name: string }) => w.slug === slug)
  const workspaceName = workspace?.name ?? slug

  return (
    <main className="p-8 max-w-5xl">
      <nav className="text-sm text-gray-500 mb-4">
        <Link href="/admin" className="hover:underline text-blue-600">
          Admin
        </Link>
        {' / '}
        <Link href={`/admin/workspaces/${slug}/accounts`} className="hover:underline text-blue-600">
          {workspaceName}
        </Link>
        {' / '}
        <span>{account.name}</span>
      </nav>

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
          <span className="px-2 py-0.5 rounded text-xs bg-amber-50 text-amber-700 font-medium border border-amber-200">
            Super-user view
          </span>
        </div>
        {account.crm_record_id && (
          <p className="text-sm text-gray-500">
            CRM:{' '}
            <span className="font-mono text-gray-700">{account.crm_record_id}</span>
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-8">
          <NarrativeSection
            narrative={narrativeWithAudit}
            accountId={account.id}
            workspaceId={account.workspace_id}
          />
          <DimensionBreakdown
            accountId={account.id}
            dimensionScores={dimScores}
            dimensionConfigs={dimConfigs}
            hasCsmConfig={hasCsmConfig}
          />
          <SignalsTimeline signals={signals} />
        </div>
        <div>
          <ContactsList contacts={contacts} />
        </div>
      </div>
    </main>
  )
}
