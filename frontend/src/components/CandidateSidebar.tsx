'use client'

import { useRouter } from 'next/navigation'
import { AccountListRow } from '@/lib/types'
import { createClient } from '@/lib/supabase/client'
import { relativeTime } from '@/lib/utils'

export default function CandidateSidebar({ candidates }: { candidates: AccountListRow[] }) {
  const router = useRouter()

  const handleConfirm = async (id: string) => {
    const supabase = createClient()
    await supabase.rpc('activate_candidate_account', { p_account_id: id })
    router.refresh()
  }

  const handleReject = async (id: string) => {
    const supabase = createClient()
    await supabase.rpc('dismiss_candidate_account', { p_account_id: id })
    router.refresh()
  }

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4 text-gray-700">Candidates</h2>
      <div className="space-y-3">
        {candidates.map((c) => (
          <div key={c.id} className="p-3 border rounded-lg bg-white shadow-sm">
            <div className="font-medium text-sm">{c.name}</div>
            {c.primary_domain && (
              <div className="text-xs text-gray-500 mt-0.5">{c.primary_domain}</div>
            )}
            <div className="text-xs text-gray-400 mt-0.5">
              First signal: {relativeTime(c.last_signal_at)}
            </div>
            <div className="flex gap-2 mt-2">
              <button
                onClick={() => handleConfirm(c.id)}
                className="flex-1 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700"
              >
                Confirm
              </button>
              <button
                onClick={() => handleReject(c.id)}
                className="flex-1 py-1 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200"
              >
                Reject
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
