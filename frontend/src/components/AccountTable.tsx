'use client'

import Link from 'next/link'
import { AccountListRow } from '@/lib/types'
import { scoreBadge, relativeTime } from '@/lib/utils'
import AuditBadge from '@/components/AuditBadge'

export default function AccountTable({
  accounts,
  hrefPrefix = '/accounts',
}: {
  accounts: AccountListRow[]
  hrefPrefix?: string
}) {
  if (accounts.length === 0) {
    return <p className="text-gray-500">No active accounts.</p>
  }

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b text-left text-gray-500">
          <th className="pb-2 pr-4 font-medium">Name</th>
          <th className="pb-2 pr-4 font-medium">Domain</th>
          <th className="pb-2 pr-4 font-medium">Vertical</th>
          <th className="pb-2 pr-4 font-medium">Health</th>
          <th className="pb-2 pr-4 font-medium">Last signal</th>
          <th className="pb-2 font-medium">Summary</th>
        </tr>
      </thead>
      <tbody>
        {accounts.map((a) => {
          const health = scoreBadge(a.overall_health_score)
          return (
            <tr key={a.id} className="border-b hover:bg-gray-50">
              <td className="py-2 pr-4">
                <Link href={`${hrefPrefix}/${a.slug}`} className="text-blue-600 hover:underline font-medium">
                  {a.name}
                </Link>
              </td>
              <td className="py-2 pr-4 text-gray-600">{a.primary_domain ?? '—'}</td>
              <td className="py-2 pr-4">
                {a.vertical ? (
                  <span className="px-2 py-0.5 rounded text-xs bg-blue-50 text-blue-700">
                    {a.vertical}
                  </span>
                ) : (
                  <span className="text-gray-400">—</span>
                )}
              </td>
              <td className="py-2 pr-4">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${health.color}`}>
                    {a.overall_health_score !== null ? `${a.overall_health_score} ${health.label}` : '—'}
                  </span>
                  <AuditBadge
                    passed={a.audit_passed ?? null}
                    criteriaPassedCount={a.audit_criteria_passed ?? null}
                    criteriaTotal={a.audit_criteria_total ?? null}
                    auditedAt={a.audit_audited_at ?? null}
                    variant="pill"
                  />
                </div>
              </td>
              <td className="py-2 pr-4 text-gray-600">{relativeTime(a.last_signal_at)}</td>
              <td className="py-2 text-gray-600 truncate max-w-xs">
                {a.narrative_excerpt ?? <span className="text-gray-400">No narrative yet</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
