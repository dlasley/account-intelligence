import { createClient } from '@/lib/supabase/server'
import { AccountListRow } from '@/lib/types'
import AccountTable from '@/components/AccountTable'
import CandidateSidebar from '@/components/CandidateSidebar'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import RetryButton from '@/components/RetryButton'

function AccountsEmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
      <p className="text-sm font-semibold">No accounts yet</p>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
        Accounts appear as we detect customer companies in your comms.
      </p>
      {/* No source-management UI exists yet — signal sources are configured
       * outside the app — so this stays a disabled affordance rather than a
       * dead link. */}
      <Button
        size="sm"
        className="mt-4"
        disabled
        title="Signal sources are configured by your workspace admin"
      >
        Connect a source
      </Button>
    </div>
  )
}

export default async function AccountsPage() {
  const supabase = await createClient()
  const { data, error } = await supabase.rpc('get_account_list')

  if (error) {
    return (
      <main className="p-8">
        <h1 className="mb-6 text-2xl font-bold tracking-tight">Accounts</h1>
        <Alert variant="destructive" className="max-w-md">
          <AlertTitle>Couldn&apos;t load accounts</AlertTitle>
          <AlertDescription>Something went wrong on our end.</AlertDescription>
        </Alert>
        <RetryButton size="sm" variant="destructive" className="mt-3" />
      </main>
    )
  }

  const rows = (data ?? []) as AccountListRow[]
  const active = rows.filter((r) => r.status === 'active')
  const candidates = rows.filter((r) => r.status === 'candidate')

  return (
    <main className="p-8">
      <div className="mb-6 flex items-baseline gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Accounts</h1>
        <span className="font-mono text-sm text-muted-foreground">
          {active.length} tracked
        </span>
      </div>
      <div className="flex flex-col gap-8 lg:flex-row">
        <div className="min-w-0 flex-1">
          {active.length === 0 ? <AccountsEmptyState /> : <AccountTable accounts={active} />}
        </div>
        {candidates.length > 0 && (
          <div className="w-full shrink-0 lg:w-80">
            <CandidateSidebar candidates={candidates} />
          </div>
        )}
      </div>
    </main>
  )
}
