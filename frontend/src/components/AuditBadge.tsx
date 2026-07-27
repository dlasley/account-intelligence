'use client'

import { useState } from 'react'
import type { VariantProps } from 'class-variance-authority'
import { relativeTime } from '@/lib/utils'
import { Badge, badgeVariants } from '@/components/ui/badge'

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
      <Badge variant="health-unknown" title="No audit on record">
        audit —
      </Badge>
    )
  }

  const label = passed ? `audit ✓ ${criteriaPassedCount ?? '?'}/${criteriaTotal ?? '?'}` : `audit ✗`
  const auditVariant: VariantProps<typeof badgeVariants>['variant'] = passed
    ? 'health-strong'
    : 'health-critical'
  const tooltip = passed
    ? `Audit passed ${criteriaPassedCount ?? '?'}/${criteriaTotal ?? '?'} criteria, last audited ${relativeTime(auditedAt)}`
    : `Audit failed, last audited ${relativeTime(auditedAt)}`

  if (variant === 'pill') {
    return (
      <Badge variant={auditVariant} title={tooltip}>
        {label}
      </Badge>
    )
  }

  // badge variant: clickable, with expandable panel
  return (
    <div className="relative inline-block">
      <Badge asChild variant={auditVariant}>
        <button
          title={tooltip}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="cursor-pointer text-sm hover:opacity-80"
        >
          {label}
        </button>
      </Badge>

      {open && criteria && criteria.length > 0 && (
        <div className="absolute left-0 top-full mt-1 z-10 w-96 bg-popover text-popover-foreground border border-border rounded shadow-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-semibold text-foreground">Audit criteria</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-muted-foreground hover:text-foreground text-xs"
            >
              close
            </button>
          </div>
          <p className="text-xs text-muted-foreground mb-3">
            Last audited {relativeTime(auditedAt)}
          </p>
          <ul className="space-y-3">
            {criteria.map((c) => (
              <li key={c.criterion}>
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className={`w-4 h-4 flex items-center justify-center rounded-full text-xs font-bold ${
                      c.passed
                        ? 'bg-health-strong-soft text-health-strong-on'
                        : 'bg-health-critical-soft text-health-critical-on'
                    }`}
                  >
                    {c.passed ? '✓' : '✗'}
                  </span>
                  <span className="text-xs font-medium text-foreground capitalize">
                    {c.criterion.replace(/_/g, ' ')}
                    {c.score !== null && (
                      <span className="text-muted-foreground font-normal ml-1">({c.score}/5)</span>
                    )}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground pl-6 leading-snug">{c.reasoning}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
