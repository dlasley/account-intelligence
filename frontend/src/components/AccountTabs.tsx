'use client'

import { useState, type ReactNode } from 'react'
import { track } from '@/lib/analytics'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

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

  const handleChange = (next: string) => {
    setTab(next as 'overview' | 'outreach')
    if (next === 'outreach') {
      track('Outreach Tab Opened', {
        account_id: accountId ?? null,
        overall_health_score: overallHealthScore ?? null,
      })
    }
  }

  return (
    <Tabs value={tab} onValueChange={handleChange}>
      <TabsList variant="line" className="mb-6 w-full justify-start gap-6 border-b border-border p-0">
        <TabsTrigger value="overview" className="px-0 pb-3 text-sm">
          Overview
        </TabsTrigger>
        <TabsTrigger value="outreach" className="px-0 pb-3 text-sm">
          Outreach
        </TabsTrigger>
      </TabsList>
      <TabsContent value="overview">{overviewContent}</TabsContent>
      <TabsContent value="outreach">{outreachContent}</TabsContent>
    </Tabs>
  )
}
