import { createClient } from '@/lib/supabase/server'
import { AccountListRow } from '@/lib/types'
import AccountTable from '@/components/AccountTable'
import CandidateSidebar from '@/components/CandidateSidebar'

export default async function AccountsPage() {
  const supabase = await createClient()
  const { data, error } = await supabase.rpc('get_account_list')

  if (error) {
    return (
      <main className="p-8">
        <p className="text-red-600">Failed to load accounts. Please refresh the page.</p>
      </main>
    )
  }

  const rows = (data ?? []) as AccountListRow[]
  const active = rows.filter((r) => r.status === 'active')
  const candidates = rows.filter((r) => r.status === 'candidate')

  return (
    <main className="p-8">
      <h1 className="text-2xl font-bold mb-6">Accounts</h1>
      <div className="flex gap-8">
        <div className="flex-1 min-w-0">
          <AccountTable accounts={active} />
        </div>
        {candidates.length > 0 && (
          <div className="w-80 shrink-0">
            <CandidateSidebar candidates={candidates} />
          </div>
        )}
      </div>
    </main>
  )
}
