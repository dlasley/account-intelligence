'use client'

import { useState } from 'react'
import { relativeTime } from '@/lib/utils'

export type AuditCriterion = {
  criterion: string
  passed: boolean
  score: number | null
  reasoning: string
}

type Props = {
  passed: boolean | null
  criteriaPassedCount: number | null
  criteriaTotal: number | null
  auditedAt: string | null
  /** Detail rows shown in the expanded panel. Omit on the list page (no expand). */
  criteria?: AuditCriterion[]
  /** 'pill' renders the compact version used in AccountTable rows */
  variant?: 'pill' | 'badge'
}

export default function AuditBadge({
  passed,
  criteriaPassedCount,
  criteriaTotal,
  auditedAt,
  criteria,
  variant = 'badge',
}: Props) {
  const [open, setOpen] = useState(false)

  if (passed === null || passed === undefined) {
    return (
      <span
        title="No audit on record"
        className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-500"
      >
        audit —
      </span>
    )
  }

  const label = passed ? `audit ✓ ${criteriaPassedCount ?? '?'}/${criteriaTotal ?? '?'}` : `audit ✗`
  const color = passed
    ? 'bg-green-100 text-green-800'
    : 'bg-red-100 text-red-800'
  const tooltip = passed
    ? `Audit passed ${criteriaPassedCount ?? '?'}/${criteriaTotal ?? '?'} criteria, last audited ${relativeTime(auditedAt)}`
    : `Audit failed, last audited ${relativeTime(auditedAt)}`

  if (variant === 'pill') {
    return (
      <span
        title={tooltip}
        className={`px-2 py-0.5 rounded text-xs font-medium ${color}`}
      >
        {label}
      </span>
    )
  }

  // badge variant: clickable, with expandable panel
  return (
    <div className="relative inline-block">
      <button
        title={tooltip}
        onClick={() => setOpen((v) => !v)}
        className={`px-2 py-0.5 rounded text-sm font-medium cursor-pointer hover:opacity-80 ${color}`}
      >
        {label}
      </button>

      {open && criteria && criteria.length > 0 && (
        <div className="absolute left-0 top-full mt-1 z-10 w-96 bg-white border border-gray-200 rounded shadow-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-gray-800">Audit criteria</span>
            <button
              onClick={() => setOpen(false)}
              className="text-gray-400 hover:text-gray-600 text-xs"
            >
              close
            </button>
          </div>
          <p className="text-xs text-gray-500 mb-3">Last audited {relativeTime(auditedAt)}</p>
          <ul className="space-y-3">
            {criteria.map((c) => (
              <li key={c.criterion}>
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className={`w-4 h-4 flex items-center justify-center rounded-full text-xs font-bold ${
                      c.passed ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {c.passed ? '✓' : '✗'}
                  </span>
                  <span className="text-xs font-medium text-gray-700 capitalize">
                    {c.criterion.replace(/_/g, ' ')}
                    {c.score !== null && (
                      <span className="text-gray-400 font-normal ml-1">({c.score}/5)</span>
                    )}
                  </span>
                </div>
                <p className="text-xs text-gray-500 pl-6 leading-snug">{c.reasoning}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
