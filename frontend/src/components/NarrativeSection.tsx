'use client'

import { useState, useEffect } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { createClient } from '@/lib/supabase/client'
import { scoreBadge, relativeTime } from '@/lib/utils'
import { track } from '@/lib/analytics'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'

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
    <Badge className={b.color}>
      {score} {b.label}
    </Badge>
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
  const [genError, setGenError] = useState<string | null>(null)
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
      setGenError('Generation is taking longer than expected. Your signals are unaffected — try again.')
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
    setGenError(null)

    const supabase = createClient()
    const { data: jobResult, error } = await supabase.rpc('enqueue_narrative_regen', {
      p_account_id: accountId,
    })

    if (error) {
      setGenError(error.message)
      setRegenerating(false)
      return
    }

    if (jobResult === null) {
      setToast('A regeneration is already scheduled.')
      setRegenerating(false)
      return
    }

    setPollingState({ clickedAt })
  }

  // Loading (full skeleton) only applies to a first-ever generation — a
  // regenerate of an existing narrative keeps the current content on screen
  // (button label + spinner communicate progress) rather than wiping it.
  const isInitialGenerating = regenerating && !narrative

  return (
    <Card className="border-l-4 border-l-primary bg-primary/[0.03] ring-0 shadow-sm">
      <CardContent className="space-y-0">
        <div className="mb-3.5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-primary" />
            <span className="font-mono text-[11px] font-medium tracking-wider text-primary uppercase">
              Narrative
            </span>
          </div>
          <Button variant="outline" size="sm" onClick={handleRegenerate} disabled={regenerating}>
            {regenerating && <Loader2 className="size-3.5 animate-spin" />}
            {regenerating ? 'Regenerating…' : 'Regenerate'}
          </Button>
        </div>

        {toast && (
          <div className="mb-3.5 rounded-md border border-health-moderate/40 bg-health-moderate-soft px-3 py-2 text-sm text-health-moderate-on">
            {toast}
          </div>
        )}

        {genError ? (
          <div>
            <Alert variant="destructive" className="border-destructive/30 bg-destructive/5">
              <AlertTitle>Couldn&apos;t generate the narrative</AlertTitle>
              <AlertDescription>{genError}</AlertDescription>
            </Alert>
            <Button size="sm" variant="destructive" className="mt-3" onClick={handleRegenerate}>
              Retry
            </Button>
          </div>
        ) : isInitialGenerating ? (
          <div className="space-y-2.5">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-2.5 w-full" />
            <Skeleton className="h-2.5 w-11/12" />
            <Skeleton className="h-2.5 w-4/5" />
            <div className="flex gap-2 pt-1">
              <Skeleton className="h-6 w-24 rounded-md" />
              <Skeleton className="h-6 w-24 rounded-md" />
            </div>
          </div>
        ) : narrative ? (
          <div>
            {narrative.auditPassed === false && (
              <div className="mb-3.5 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                <span className="font-semibold">Unverified</span> — this narrative failed provenance
                checks. Scores shown but flagged; regenerate to re-audit.
              </div>
            )}
            <p className="text-base leading-relaxed text-pretty text-foreground whitespace-pre-wrap">
              {narrative.narrative}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-4 border-t border-primary/15 pt-3.5 text-xs">
              <div className="flex items-center gap-1.5">
                <span className="text-muted-foreground">Engagement</span>
                <ScorePill score={narrative.engagement} />
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-muted-foreground">Sentiment</span>
                {narrative.sentiment !== null ? (
                  <ScorePill score={narrative.sentiment} />
                ) : (
                  <span className="text-muted-foreground italic">Pending next regeneration</span>
                )}
              </div>
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
              <span>{narrative.engagement_rationale}</span>
              <span>Generated {relativeTime(narrative.generated_at)}</span>
              {narrative.auditPassed !== undefined && narrative.auditPassed !== null && (
                <Badge
                  variant={narrative.auditPassed ? 'health-strong' : 'health-critical'}
                  title={`Audit passed ${narrative.auditCriteriaPassed ?? '?'}/${narrative.auditCriteriaTotal ?? '?'} criteria, last audited ${relativeTime(narrative.auditAuditedAt ?? null)}`}
                >
                  {narrative.auditPassed
                    ? `audit ✓ ${narrative.auditCriteriaPassed ?? '?'}/${narrative.auditCriteriaTotal ?? '?'} · ${relativeTime(narrative.auditAuditedAt ?? null)}`
                    : `audit ✗ · ${relativeTime(narrative.auditAuditedAt ?? null)}`}
                </Badge>
              )}
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-8 text-center">
            <div className="mx-auto mb-3 flex size-8 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Sparkles className="size-4" />
            </div>
            <p className="text-sm font-semibold">No narrative yet</p>
            <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
              We&apos;ll generate one as soon as this account has enough signal.
            </p>
            <Button size="sm" className="mt-4" onClick={handleRegenerate} disabled={regenerating}>
              Generate now
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
