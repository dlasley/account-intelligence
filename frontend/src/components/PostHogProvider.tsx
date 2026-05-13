'use client'

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { createClient } from '@/lib/supabase/client'
import { identify, init, page, reset } from '@/lib/analytics'

/**
 * Initializes PostHog on mount, wires auth-state identify/reset,
 * and fires page() on route changes.
 *
 * Rendered as a Client Component child of RootLayout so it can use
 * hooks while the layout itself stays a Server Component.
 */
export default function PostHogProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  useEffect(() => {
    init()
  }, [])

  // Identify after Supabase auth state is confirmed
  useEffect(() => {
    const supabase = createClient()

    async function syncUser() {
      const {
        data: { user },
      } = await supabase.auth.getUser()
      if (user) {
        // Fetch workspace_id from users table for identity context
        const { data: profile } = await supabase
          .from('users')
          .select('workspace_id')
          .eq('id', user.id)
          .maybeSingle()
        identify(user.id, {
          email: user.email,
          workspace_id: profile?.workspace_id ?? null,
        })
      }
    }

    syncUser()

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' && session?.user) {
        identify(session.user.id, { email: session.user.email })
      } else if (event === 'SIGNED_OUT') {
        reset()
      }
    })

    return () => subscription.unsubscribe()
  }, [])

  // Manual page tracking on route change (capture_pageview disabled in init)
  useEffect(() => {
    page(pathname ?? undefined)
  }, [pathname])

  return <>{children}</>
}
