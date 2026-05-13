'use client'

import { useState, type ReactNode } from 'react'
import { track } from '@/lib/analytics'

type Props = {
  overviewContent: ReactNode
  outreachContent: ReactNode
  accountId?: string
  overallHealthScore?: number | null
}

export default function AccountTabs({
  overviewContent,
  outreachContent,
  accountId,
  overallHealthScore,
}: Props) {
  const [tab, setTab] = useState<'overview' | 'outreach'>('overview')

  return (
    <div>
      <div className="flex gap-1 mb-6 border-b border-gray-200">
        <button
          onClick={() => setTab('overview')}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
            tab === 'overview'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          Overview
        </button>
        <button
          onClick={() => {
            setTab('outreach')
            track('Outreach Tab Opened', {
              account_id: accountId ?? null,
              overall_health_score: overallHealthScore ?? null,
            })
          }}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
            tab === 'outreach'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          Outreach
        </button>
      </div>

      <div key={tab}>
        {tab === 'overview' ? overviewContent : outreachContent}
      </div>
    </div>
  )
}
