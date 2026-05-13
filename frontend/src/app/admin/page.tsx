import { createClient } from '@/lib/supabase/server'
import WorkspaceTable, { WorkspaceRow } from '@/components/WorkspaceTable'

export default async function AdminPage() {
  const supabase = await createClient()
  const { data, error } = await supabase.rpc('list_all_workspaces_with_metadata')

  if (error) {
    return (
      <main className="p-8">
        <p className="text-red-600">Failed to load workspaces: {error.message}</p>
      </main>
    )
  }

  const workspaces = (data ?? []) as WorkspaceRow[]

  return (
    <main className="p-8">
      <h1 className="text-2xl font-bold mb-2">Admin — All Workspaces</h1>
      <p className="text-sm text-gray-500 mb-6">
        {workspaces.length} workspace{workspaces.length !== 1 ? 's' : ''}
      </p>
      <WorkspaceTable workspaces={workspaces} />
    </main>
  )
}
