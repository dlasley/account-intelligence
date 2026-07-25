import { createClient } from '@/lib/supabase/server'
import WorkspaceTable, { WorkspaceRow } from '@/components/WorkspaceTable'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'

export default async function AdminPage() {
  const supabase = await createClient()
  const { data, error } = await supabase.rpc('list_all_workspaces_with_metadata')

  if (error) {
    return (
      <main className="mx-auto max-w-5xl p-8">
        <Alert variant="destructive">
          <AlertDescription>Failed to load workspaces: {error.message}</AlertDescription>
        </Alert>
      </main>
    )
  }

  const workspaces = (data ?? []) as WorkspaceRow[]

  return (
    <main className="mx-auto max-w-5xl p-8">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h1 className="text-2xl font-bold text-foreground">All Workspaces</h1>
        <Badge variant="health-moderate">Super-user view</Badge>
      </div>
      <p className="mb-6 text-sm text-muted-foreground">
        {workspaces.length} workspace{workspaces.length !== 1 ? 's' : ''}
      </p>
      <WorkspaceTable workspaces={workspaces} />
    </main>
  )
}
