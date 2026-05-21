'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import { scoreBadge, relativeTime } from '@/lib/utils'
import { track } from '@/lib/analytics'

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
    <div className="flex items-center gap-2 min-w-0">
      <div className="relative w-24 h-2 rounded bg-gray-100 overflow-hidden shrink-0">
        <div
          className={`absolute inset-y-0 left-0 rounded ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
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
      <h2 className="text-lg font-semibold mb-3">Health Dimensions</h2>

      {dimensionScores.length === 0 ? (
        <p className="text-gray-400 italic text-sm">No dimension scores yet.</p>
      ) : (
        <>
          {divergencePair && (
            <div className="mb-3 px-3 py-2 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
              <span className="font-semibold">Divergence detected</span>
              {' — '}
              {configsById.get(divergencePair[0].dimension_id)?.name ?? divergencePair[0].dimension_id}
              {' '}({divergencePair[0].score})
              {' vs '}
              {configsById.get(divergencePair[1].dimension_id)?.name ?? divergencePair[1].dimension_id}
              {' '}({divergencePair[1].score})
              {': a '}
              {Math.abs(divergencePair[0].score - divergencePair[1].score)}
              {'-point gap between dominant dimensions.'}
            </div>
          )}

          <table className="w-full text-sm border-collapse mb-4">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="pb-2 pr-4 font-medium">Dimension</th>
                <th className="pb-2 pr-4 font-medium">Score</th>
                <th className="pb-2 pr-4 font-medium w-32">Bar</th>
                <th className="pb-2 pr-4 font-medium">Scored by</th>
                <th className="pb-2 font-medium">Rationale / notes</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((s) => {
                const badge = scoreBadge(s.score)
                const name = configsById.get(s.dimension_id)?.name ?? s.dimension_id
                const windowDays =
                  s.metadata && typeof s.metadata.window_days === 'number'
                    ? (s.metadata.window_days as number)
                    : null
                // Use a solid fill color derived from the badge color for the bar.
                // Badge colors are bg-{color}-100 text-{color}-800; bar uses bg-{color}-400.
                const barColorMap: Record<string, string> = {
                  'bg-green-100 text-green-800': 'bg-green-400',
                  'bg-emerald-100 text-emerald-800': 'bg-emerald-400',
                  'bg-yellow-100 text-yellow-800': 'bg-yellow-400',
                  'bg-orange-100 text-orange-800': 'bg-orange-400',
                  'bg-red-100 text-red-800': 'bg-red-400',
                  'bg-gray-100 text-gray-500': 'bg-gray-400',
                }
                const barColor = barColorMap[badge.color] ?? 'bg-blue-400'
                return (
                  <tr key={s.dimension_id} className="border-b">
                    <td className="py-2 pr-4 font-medium">{name}</td>
                    <td className="py-2 pr-4">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${badge.color}`}>
                        {s.score} {badge.label}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      <ScoreBar score={s.score} color={barColor} />
                    </td>
                    <td className="py-2 pr-4 text-gray-600 capitalize">{s.scored_by}</td>
                    <td className="py-2 text-gray-500 text-xs">
                      {windowDays !== null && (
                        <span className="mr-1 text-gray-400 italic">
                          scored from {windowDays}-day window ·{' '}
                        </span>
                      )}
                      {s.rationale ?? <span className="text-gray-400">—</span>}
                      {!windowDays && !s.rationale && null}
                      {' '}
                      <span className="text-gray-400">{relativeTime(s.scored_at)}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

      {hasCsmConfig && (
        <div className="border rounded p-4 bg-gray-50">
          <h3 className="text-sm font-semibold mb-2">Update CSM Score</h3>
          <div className="flex gap-2 items-start flex-wrap">
            <input
              type="number"
              min={1}
              max={100}
              placeholder="Score (1–100)"
              value={csmScore}
              onChange={(e) => setCsmScore(e.target.value)}
              className="border rounded px-2 py-1 text-sm w-28"
            />
            <input
              type="text"
              placeholder="Rationale (optional)"
              value={csmRationale}
              onChange={(e) => setCsmRationale(e.target.value)}
              className="border rounded px-2 py-1 text-sm flex-1 min-w-40"
            />
            <button
              onClick={handleSaveCsmScore}
              disabled={saving || !csmScore}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {error && <p className="text-red-600 text-xs mt-2">{error}</p>}
        </div>
      )}
    </section>
  )
}
