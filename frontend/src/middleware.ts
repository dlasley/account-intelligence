import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'
import { SUPABASE_ANON_KEY, SUPABASE_URL, isSupabaseConfigured } from '@/lib/supabase/config'

export async function middleware(request: NextRequest) {
  const supabaseResponse = NextResponse.next({ request })

  // If Supabase isn't configured in this environment, skip auth checks
  // instead of throwing so the app can still render.
  if (!isSupabaseConfigured) {
    return supabaseResponse
  }

  const supabase = createServerClient(
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll: (cs) =>
          cs.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          ),
      },
    },
  )
  const {
    data: { user },
  } = await supabase.auth.getUser()
  const path = request.nextUrl.pathname

  if (!user && path !== '/login' && !path.startsWith('/auth')) {
    return NextResponse.redirect(new URL('/login', request.url))
  }

  if (path.startsWith('/admin')) {
    if (!user) {
      return NextResponse.redirect(new URL('/login', request.url))
    }
    const { data: profile } = await supabase
      .from('users')
      .select('is_super_user')
      .eq('id', user.id)
      .maybeSingle()
    if (!profile?.is_super_user) {
      return NextResponse.redirect(new URL('/accounts', request.url))
    }
  }

  return supabaseResponse
}

export const config = { matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'] }
