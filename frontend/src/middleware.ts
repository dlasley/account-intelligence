import { createServerClient } from '@supabase/ssr'
import { NextResponse, type NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  const supabaseResponse = NextResponse.next({ request })
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
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
