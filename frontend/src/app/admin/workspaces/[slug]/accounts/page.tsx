import Link from 'next/link'
import { notFound } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { AccountListRow } from '@/lib/types'
import AccountTable from '@/components/AccountTable'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'

export default async function AdminWorkspaceAccountsPage({
  params,
}: {
  params: Promise<{ slug: string }>
}) {
  const { slug } = await params
  const supabase = await createClient()

  const { data, error } = await supabase.rpc('list_accounts_for_workspace', {
    p_workspace_slug: slug,
  })

  if (error) {
    // workspace not found surfaces as a Postgres RAISE EXCEPTION
    if (error.message.includes('workspace not found')) notFound()
    return (
      <main className="mx-auto max-w-5xl p-8">
        <Alert variant="destructive">
          <AlertDescription>Failed to load accounts: {error.message}</AlertDescription>
        </Alert>
      </main>
    )
  }

  const rows = (data ?? []) as AccountListRow[]
  const active = rows.filter((r) => r.status === 'active')
  const candidates = rows.filter((r) => r.status === 'candidate')

  // Derive a display name from the first active/candidate row's workspace context.
  // list_accounts_for_workspace returns workspace_id; use slug for display since name
  // isn't in the return set. We'll fetch the name separately for the header.
  const { data: wsRows } = await supabase.rpc('list_all_workspaces_with_metadata')
  const workspace = (wsRows ?? []).find((w: { slug: string; name: string }) => w.slug === slug)
  const workspaceName = workspace?.name ?? slug

  return (
    <main className="mx-auto max-w-5xl p-8">
      <nav className="mb-4 text-sm text-muted-foreground">
        <Link href="/admin" className="text-primary hover:underline">
          Admin
        </Link>
        {' / '}
        <span className="text-foreground">{workspaceName}</span>
      </nav>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold text-foreground">{workspaceName}</h1>
        <Badge variant="health-moderate">Super-user view</Badge>
      </div>
      <p className="mb-6 text-sm text-muted-foreground">
        {active.length} active account{active.length !== 1 ? 's' : ''}
        {candidates.length > 0
          ? `, ${candidates.length} candidate${candidates.length !== 1 ? 's' : ''}`
          : ''}
      </p>
      <div className="flex gap-8">
        <div className="min-w-0 flex-1">
          <AccountTable accounts={active} hrefPrefix={`/admin/workspaces/${slug}/accounts`} />
        </div>
      </div>
    </main>
  )
}
