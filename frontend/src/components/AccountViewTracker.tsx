'use client'

import { useEffect } from 'react'
import { track } from '@/lib/analytics'

type Props = {
  accountId: string
  accountSlug: string
  overallHealthScore: number | null
}

/**
 * Fires the "Account Viewed" analytics event on mount.
 *
 * Exists as a separate Client Component because the account detail page is a
 * Server Component and cannot call analytics.track() directly.
 */
export default function AccountViewTracker({ accountId, accountSlug, overallHealthScore }: Props) {
  useEffect(() => {
    track('Account Viewed', {
      account_id: accountId,
      account_slug: accountSlug,
      overall_health_score: overallHealthScore,
    })
    // Fire once on mount — dependencies are stable for the page lifetime
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return null
}
