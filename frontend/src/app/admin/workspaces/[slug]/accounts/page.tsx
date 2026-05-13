import Link from 'next/link'
import { notFound } from 'next/navigation'
import { createClient } from '@/lib/supabase/server'
import { AccountListRow } from '@/lib/types'
import AccountTable from '@/components/AccountTable'

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
      <main className="p-8">
        <p className="text-red-600">Failed to load accounts: {error.message}</p>
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
    <main className="p-8 max-w-5xl">
      <nav className="text-sm text-gray-500 mb-4">
        <Link href="/admin" className="hover:underline text-blue-600">
          Admin
        </Link>
        {' / '}
        <span>{workspaceName}</span>
      </nav>
      <div className="mb-1 flex items-center gap-2">
        <h1 className="text-2xl font-bold">{workspaceName}</h1>
        <span className="px-2 py-0.5 rounded text-xs bg-amber-50 text-amber-700 font-medium border border-amber-200">
          Super-user view
        </span>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        {active.length} active account{active.length !== 1 ? 's' : ''}
        {candidates.length > 0 ? `, ${candidates.length} candidate${candidates.length !== 1 ? 's' : ''}` : ''}
      </p>
      <div className="flex gap-8">
        <div className="flex-1 min-w-0">
          <AccountTable
            accounts={active}
            hrefPrefix={`/admin/workspaces/${slug}/accounts`}
          />
        </div>
      </div>
    </main>
  )
}
