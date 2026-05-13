import Link from 'next/link'
import { relativeTime } from '@/lib/utils'

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
    return <p className="text-gray-500">No workspaces found.</p>
  }

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b text-left text-gray-500">
          <th className="pb-2 pr-4 font-medium">Workspace</th>
          <th className="pb-2 pr-4 font-medium">Accounts</th>
          <th className="pb-2 pr-4 font-medium">Active</th>
          <th className="pb-2 pr-4 font-medium">Last narrative</th>
          <th className="pb-2 font-medium">Last signal</th>
        </tr>
      </thead>
      <tbody>
        {workspaces.map((w) => (
          <tr key={w.id} className="border-b hover:bg-gray-50">
            <td className="py-2 pr-4">
              <Link
                href={`/admin/workspaces/${w.slug}/accounts`}
                className="text-blue-600 hover:underline font-medium"
              >
                {w.name}
              </Link>
              <div className="text-xs text-gray-400">{w.slug}</div>
            </td>
            <td className="py-2 pr-4">{w.account_count}</td>
            <td className="py-2 pr-4">{w.active_account_count}</td>
            <td className="py-2 pr-4">{relativeTime(w.last_narrative_at)}</td>
            <td className="py-2">{relativeTime(w.last_signal_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
