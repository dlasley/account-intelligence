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

export default function Header() {
  const pathname = usePathname()
  const router = useRouter()
  const [info, setInfo] = useState<WorkspaceInfo>({ workspaceName: null, userEmail: null })
  const [signingOut, setSigningOut] = useState(false)

  const isHidden = HIDDEN_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/'))

  useEffect(() => {
    if (isHidden) return
    let cancelled = false
    ;(async () => {
      const supabase = createClient()
      const {
        data: { user },
      } = await supabase.auth.getUser()
      if (!user) return
      const { data: workspaceRows } = await supabase
        .from('workspaces')
        .select('name')
        .limit(1)
      if (cancelled) return
      setInfo({
        workspaceName: workspaceRows?.[0]?.name ?? null,
        userEmail: user.email ?? null,
      })
    })()
    return () => {
      cancelled = true
    }
  }, [pathname, isHidden])

  if (isHidden) return null

  const handleSignOut = async () => {
    setSigningOut(true)
    const supabase = createClient()
    await supabase.auth.signOut()
    router.push('/login')
  }

  return (
    <header className="border-b border-gray-200 bg-white">
      <div className="max-w-7xl mx-auto px-8 py-3 flex items-center justify-between">
        <Link href="/accounts" className="text-lg font-semibold text-gray-900 hover:text-gray-700">
          {info.workspaceName ?? 'Account Intelligence'}
        </Link>
        <div className="flex items-center gap-4 text-sm">
          {info.userEmail && <span className="text-gray-600">{info.userEmail}</span>}
          <button
            type="button"
            onClick={handleSignOut}
            disabled={signingOut}
            className="text-gray-600 hover:text-gray-900 underline disabled:opacity-50"
          >
            {signingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </div>
    </header>
  )
}
