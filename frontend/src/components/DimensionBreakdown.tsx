'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import { scoreBadge, scoreBarColor, relativeTime } from '@/lib/utils'
import { track } from '@/lib/analytics'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table'

export type DimScore = {
  score: number
  rationale: string | null
  scored_by: string
  scored_at: string
  metadata: Record<string, unknown> | null
  dimension_id: string
}

export type DimConfig = {
  id: string
  dimension_type: string
  name: string
  weight: number
  enabled: boolean
}

// Two dominant-weight dimensions with a gap >= this threshold get the divergence callout.
// At 50: fires on crucible (email=90, product=36, gap=54), driftwood-labs (62), thornfield-ai (62).
// Leaves phalanx-systems (email=90, product=45, gap=45) clean — matching the spec example.
const DIVERGENCE_THRESHOLD = 50
const DIVERGENCE_MIN_WEIGHT = 0.2

function ScoreBar({ score, color }: { score: number; color: string }) {
  return (
    <div className="relative h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-muted">
      <div className={`absolute inset-y-0 left-0 rounded-full ${color}`} style={{ width: `${score}%` }} />
    </div>
  )
}

export default function DimensionBreakdown({
  accountId,
  dimensionScores,
  dimensionConfigs,
  hasCsmConfig,
}: {
  accountId: string
  dimensionScores: DimScore[]
  dimensionConfigs: DimConfig[]
  hasCsmConfig: boolean
}) {
  // Build a lookup from dimension_id -> config so we can render names + weights
  // from a separate query (the embedded relationship via PostgREST returned null
  // under the user's auth context — root cause TBD; this is the pragmatic fix).
  const configsById = new Map<string, DimConfig>(
    dimensionConfigs.map((c) => [c.id, c])
  )

  // previous_score sourced from current top score for the csm_score dimension
  const currentCsmScore =
    dimensionScores.find(
      (s) => configsById.get(s.dimension_id)?.dimension_type === 'csm_score'
    )?.score ?? null
  const router = useRouter()
  const [csmScore, setCsmScore] = useState('')
  const [csmRationale, setCsmRationale] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSaveCsmScore = async () => {
    const score = Number(csmScore)
    if (!Number.isInteger(score) || score < 1 || score > 100) {
      setError('Score must be an integer between 1 and 100.')
      return
    }
    setSaving(true)
    setError(null)
    const supabase = createClient()
    const { error: rpcError } = await supabase.rpc('set_csm_score', {
      p_account_id: accountId,
      p_score: score,
      p_rationale: csmRationale || null,
    })
    setSaving(false)
    if (rpcError) {
      setError(rpcError.message)
      return
    }
    track('CSM Score Set', {
      account_id: accountId,
      score,
      previous_score: currentCsmScore,
    })
    setCsmScore('')
    setCsmRationale('')
    router.refresh()
  }

  // Sort by weight descending so dominant dimensions are at the top
  const sorted = [...dimensionScores].sort((a, b) => {
    const wa = configsById.get(a.dimension_id)?.weight ?? 0
    const wb = configsById.get(b.dimension_id)?.weight ?? 0
    return wb - wa
  })

  const rows = sorted.map((s) => {
    const windowDays =
      s.metadata && typeof s.metadata.window_days === 'number'
        ? (s.metadata.window_days as number)
        : null
    const scoredBy = s.scored_by.charAt(0).toUpperCase() + s.scored_by.slice(1)
    return {
      key: s.dimension_id,
      name: configsById.get(s.dimension_id)?.name ?? s.dimension_id,
      score: s.score,
      badge: scoreBadge(s.score),
      barColor: scoreBarColor(s.score),
      rationale: [
        scoredBy,
        windowDays !== null ? `${windowDays}-day window` : null,
        s.rationale ?? '—',
        relativeTime(s.scored_at),
      ]
        .filter(Boolean)
        .join(' · '),
    }
  })

  // Detect divergence: find the two highest-weight dimensions that both meet the
  // minimum weight bar and check if their gap is large enough to call out
  const dominant = sorted.filter(
    (s) => (configsById.get(s.dimension_id)?.weight ?? 0) >= DIVERGENCE_MIN_WEIGHT
  )
  let divergencePair: [DimScore, DimScore] | null = null
  outer: for (let i = 0; i < dominant.length; i++) {
    for (let j = i + 1; j < dominant.length; j++) {
      const gap = Math.abs(dominant[i].score - dominant[j].score)
      if (gap >= DIVERGENCE_THRESHOLD) {
        divergencePair = [dominant[i], dominant[j]]
        break outer
      }
    }
  }

  return (
    <section>
      <h2 className="mb-3 text-base font-semibold">Health Dimensions</h2>

      {dimensionScores.length === 0 ? (
        <p className="text-sm text-muted-foreground">No dimension scores yet.</p>
      ) : (
        <>
          {divergencePair && (
            <Alert className="mb-3 border-health-moderate/40 border-l-[3px] border-l-health-moderate bg-health-moderate-soft">
              <AlertTitle className="text-health-moderate-on">Divergence detected</AlertTitle>
              <AlertDescription className="text-health-moderate-on/90">
                <span className="font-semibold">
                  {configsById.get(divergencePair[0].dimension_id)?.name ?? divergencePair[0].dimension_id}
                </span>{' '}
                {divergencePair[0].score}
                {' vs '}
                <span className="font-semibold">
                  {configsById.get(divergencePair[1].dimension_id)?.name ?? divergencePair[1].dimension_id}
                </span>{' '}
                {divergencePair[1].score}
                {' — a '}
                {Math.abs(divergencePair[0].score - divergencePair[1].score)}
                {'-point gap between dominant dimensions.'}
              </AlertDescription>
            </Alert>
          )}

          {/* The four-column table needs more width than a phone offers, so
              below sm the same rows render as stacked cards. Both layouts read
              from `rows` so their content cannot diverge. */}
          <div className="mb-4 space-y-2 sm:hidden">
            {rows.map((r) => (
              <div key={r.key} className="rounded-lg border border-border p-3">
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium">{r.name}</span>
                  <Badge className={r.badge.color}>
                    {r.score} {r.badge.label}
                  </Badge>
                </div>
                <div className="mt-2">
                  <ScoreBar score={r.score} color={r.barColor} />
                </div>
                <p className="mt-2 text-xs text-muted-foreground">{r.rationale}</p>
              </div>
            ))}
          </div>

          <div className="mb-4 hidden overflow-hidden rounded-lg border border-border sm:block">
            <Table>
              <TableHeader className="bg-muted/40">
                <TableRow>
                  <TableHead>Dimension</TableHead>
                  <TableHead>Score</TableHead>
                  <TableHead>Level</TableHead>
                  <TableHead>Rationale</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.key}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell>
                      <Badge className={r.badge.color}>
                        {r.score} {r.badge.label}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <ScoreBar score={r.score} color={r.barColor} />
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs text-muted-foreground">
                      {r.rationale}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}

      {hasCsmConfig && (
        <div className="rounded-lg border border-border bg-muted/30 p-4">
          <h3 className="mb-2 text-sm font-semibold">Update CSM Score</h3>
          <div className="flex flex-wrap items-start gap-2">
            <Input
              type="number"
              min={1}
              max={100}
              placeholder="Score (1–100)"
              value={csmScore}
              onChange={(e) => setCsmScore(e.target.value)}
              className="w-28"
            />
            <Input
              type="text"
              placeholder="Rationale (optional)"
              value={csmRationale}
              onChange={(e) => setCsmRationale(e.target.value)}
              className="min-w-40 flex-1"
            />
            <Button onClick={handleSaveCsmScore} disabled={saving || !csmScore} size="sm">
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </div>
          {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
        </div>
      )}
    </section>
  )
}
