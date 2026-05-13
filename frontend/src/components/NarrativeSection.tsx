'use client'

import { useState, useEffect } from 'react'
import { createClient } from '@/lib/supabase/client'
import { scoreBadge, relativeTime } from '@/lib/utils'
import { track } from '@/lib/analytics'

type NarrativeData = {
  id?: string
  narrative: string
  engagement: number
  engagement_rationale: string
  sentiment: number | null
  generated_at: string
  // audit info injected by the server page
  auditPassed?: boolean | null
  auditCriteriaPassed?: number | null
  auditCriteriaTotal?: number | null
  auditAuditedAt?: string | null
} | null

function ScorePill({ score }: { score: number }) {
  const b = scoreBadge(score)
  return (
    <span className={`px-2 py-0.5 rounded font-medium ${b.color}`}>
      {score} {b.label}
    </span>
  )
}

export default function NarrativeSection({
  narrative: initialNarrative,
  accountId,
  workspaceId,
}: {
  narrative: NarrativeData
  accountId: string
  workspaceId: string
}) {
  const [narrative, setNarrative] = useState(initialNarrative)
  const [regenerating, setRegenerating] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [pollingState, setPollingState] = useState<{ clickedAt: string } | null>(null)

  // Fire Narrative Viewed once on mount when a narrative is present
  useEffect(() => {
    if (!narrative) return
    const ageHours = (Date.now() - new Date(narrative.generated_at).getTime()) / 3_600_000
    track('Narrative Viewed', {
      account_id: accountId,
      narrative_id: null, // id not included in the current select — acceptable null
      narrative_age_hours: Math.round(ageHours * 10) / 10,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!pollingState) return
    const { clickedAt } = pollingState
    const supabase = createClient()

    const intervalId = setInterval(async () => {
      const { data } = await supabase
        .from('narratives')
        .select('id, narrative, engagement, engagement_rationale, sentiment, generated_at')
        .eq('workspace_id', workspaceId)
        .eq('account_id', accountId)
        .is('superseded_at', null)
        .maybeSingle()
      if (data && data.generated_at > clickedAt) {
        setNarrative(data as NarrativeData)
        setRegenerating(false)
        setPollingState(null)
      }
    }, 3000)

    const timeoutId = setTimeout(() => {
      setPollingState(null)
      setRegenerating(false)
    }, 5 * 60 * 1000)

    return () => {
      clearInterval(intervalId)
      clearTimeout(timeoutId)
    }
  }, [pollingState, accountId, workspaceId])

  const handleRegenerate = async () => {
    const clickedAt = new Date().toISOString()
    setRegenerating(true)
    setToast(null)

    const supabase = createClient()
    const { data: jobResult } = await supabase.rpc('enqueue_narrative_regen', {
      p_account_id: accountId,
    })

    if (jobResult === null) {
      setToast('A regeneration is already scheduled.')
      setRegenerating(false)
      return
    }

    setPollingState({ clickedAt })
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Narrative</h2>
        <button
          onClick={handleRegenerate}
          disabled={regenerating}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
        >
          {regenerating && (
            <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          {regenerating ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>

      {toast && (
        <div className="mb-3 px-3 py-2 bg-yellow-50 border border-yellow-200 text-yellow-800 text-sm rounded">
          {toast}
        </div>
      )}

      {narrative ? (
        <div>
          <p className="text-gray-800 leading-relaxed whitespace-pre-wrap">{narrative.narrative}</p>
          <div className="mt-3 flex gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <span className="text-gray-500">Engagement</span>
              <ScorePill score={narrative.engagement} />
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-gray-500">Sentiment</span>
              {narrative.sentiment !== null ? (
                <ScorePill score={narrative.sentiment} />
              ) : (
                <span className="text-gray-400 italic">Pending next regeneration</span>
              )}
            </div>
          </div>
          <div className="mt-2 text-xs text-gray-500 space-y-0.5">
            <div>{narrative.engagement_rationale}</div>
            <div className="flex items-center gap-2 flex-wrap">
              <span>Generated {relativeTime(narrative.generated_at)}</span>
              {narrative.auditPassed !== undefined && narrative.auditPassed !== null && (
                <span
                  title={`Audit passed ${narrative.auditCriteriaPassed ?? '?'}/${narrative.auditCriteriaTotal ?? '?'} criteria, last audited ${relativeTime(narrative.auditAuditedAt ?? null)}`}
                  className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    narrative.auditPassed
                      ? 'bg-green-100 text-green-700'
                      : 'bg-red-100 text-red-700'
                  }`}
                >
                  {narrative.auditPassed
                    ? `audit ✓ ${narrative.auditCriteriaPassed ?? '?'}/${narrative.auditCriteriaTotal ?? '?'} · ${relativeTime(narrative.auditAuditedAt ?? null)}`
                    : `audit ✗ · ${relativeTime(narrative.auditAuditedAt ?? null)}`}
                </span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <p className="text-gray-400 italic">No narrative yet.</p>
      )}
    </section>
  )
}
