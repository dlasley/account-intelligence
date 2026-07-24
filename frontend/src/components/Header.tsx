'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'

type WorkspaceInfo = {
  workspaceName: string | null
  userEmail: string | null
}

const HIDDEN_PREFIXES = ['/login', '/auth']

// Matches /admin/workspaces/:slug and its sub-routes. A super-user browsing
// another workspace's admin pages sees THAT workspace as secondary context,
// not their own — super-user browsing never mutates the signed-in user's
// workspace_id, so the route is the only source for which one is in view.
const ADMIN_WORKSPACE_SLUG_RE = /^\/admin\/workspaces\/([^/]+)/

export default function Header() {
  const pathname = usePathname()
  const router = useRouter()
  const [info, setInfo] = useState<WorkspaceInfo>({ workspaceName: null, userEmail: null })
  const [signingOut, setSigningOut] = useState(false)

  const isHidden = HIDDEN_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/'))
  const viewedWorkspaceSlug = pathname.match(ADMIN_WORKSPACE_SLUG_RE)?.[1] ?? null

  useEffect(() => {
    if (isHidden) return
    let cancelled = false
    ;(async () => {
      const supabase = createClient()
      const {
        data: { user },
      } = await supabase.auth.getUser()
      if (!user) return

      let workspaceName: string | null = null
      if (viewedWorkspaceSlug) {
        // Super-user admin route: show the workspace being browsed. Only
        // super-users can reach /admin/* (middleware-gated), so this RPC
        // (which itself gates on is_super_user) is safe to call here.
        const { data: workspaces } = await supabase.rpc('list_all_workspaces_with_metadata')
        workspaceName =
          (workspaces as { slug: string; name: string }[] | null)?.find(
            (w) => w.slug === viewedWorkspaceSlug,
          )?.name ?? null
      } else {
        const { data: userRow } = await supabase
          .from('users')
          .select('workspace_id')
          .eq('id', user.id)
          .maybeSingle()
        if (userRow?.workspace_id) {
          const { data: workspace } = await supabase
            .from('workspaces')
            .select('name')
            .eq('id', userRow.workspace_id)
            .maybeSingle()
          workspaceName = workspace?.name ?? null
        }
      }

      if (cancelled) return
      setInfo({ workspaceName, userEmail: user.email ?? null })
    })()
    return () => {
      cancelled = true
    }
  }, [isHidden, viewedWorkspaceSlug])

  if (isHidden) return null

  const handleSignOut = async () => {
    setSigningOut(true)
    const supabase = createClient()
    await supabase.auth.signOut()
    router.push('/login')
  }

  return (
    <header className="border-b border-border bg-background">
      <div className="max-w-7xl mx-auto px-4 sm:px-8 py-3 flex items-center justify-between gap-3">
        <Link href="/accounts" className="flex min-w-0 items-baseline gap-2 hover:opacity-80">
          <span className="text-lg font-semibold whitespace-nowrap text-foreground">
            Account Intelligence
          </span>
          {info.workspaceName && (
            <span className="truncate text-sm text-muted-foreground">{info.workspaceName}</span>
          )}
        </Link>
        <div className="flex shrink-0 items-center gap-4 text-sm">
          {/* The signed-in address is secondary to Sign out; it is dropped
              below sm rather than competing for the narrow header row. */}
          {info.userEmail && (
            <span className="hidden text-muted-foreground sm:inline">{info.userEmail}</span>
          )}
          <button
            type="button"
            onClick={handleSignOut}
            disabled={signingOut}
            className="text-muted-foreground hover:text-foreground underline disabled:opacity-50"
          >
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </div>
    </header>
  )
}
