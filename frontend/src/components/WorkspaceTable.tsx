import Link from 'next/link'
import { relativeTime } from '@/lib/utils'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

export type WorkspaceRow = {
  id: string
  slug: string
  name: string
  account_count: number
  active_account_count: number
  last_narrative_at: string | null
  last_signal_at: string | null
  created_at: string
}

export default function WorkspaceTable({ workspaces }: { workspaces: WorkspaceRow[] }) {
  if (workspaces.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
        <p className="text-sm font-semibold">No workspaces found</p>
      </div>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {/* Name and Last signal always render; the rest return at sm/md, matching
              AccountTable's column-priority convention for narrow viewports. */}
          <TableHead>Workspace</TableHead>
          <TableHead className="hidden sm:table-cell">Accounts</TableHead>
          <TableHead className="hidden sm:table-cell">Active</TableHead>
          <TableHead className="hidden md:table-cell">Last narrative</TableHead>
          <TableHead>Last signal</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {workspaces.map((w) => (
          <TableRow key={w.id}>
            <TableCell>
              <Link
                href={`/admin/workspaces/${w.slug}/accounts`}
                className="font-medium text-primary hover:underline"
              >
                {w.name}
              </Link>
              <div className="font-mono text-xs text-muted-foreground">{w.slug}</div>
            </TableCell>
            <TableCell className="hidden sm:table-cell">{w.account_count}</TableCell>
            <TableCell className="hidden sm:table-cell">{w.active_account_count}</TableCell>
            <TableCell className="hidden text-muted-foreground md:table-cell">
              {relativeTime(w.last_narrative_at)}
            </TableCell>
            <TableCell className="text-muted-foreground">{relativeTime(w.last_signal_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
