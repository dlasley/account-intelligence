// Centralized Supabase connection config.
//
// The real values come from the project's environment variables
// (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY) and are present in
// deployed environments. When they are absent (e.g. a local/preview sandbox
// that hasn't received the vars yet) we fall back to a valid placeholder URL
// so the Supabase SDK can still be constructed without throwing at import/render
// time. Requests made against the placeholder simply fail and surface through
// each caller's normal error handling instead of crashing the whole app.

const envUrl = process.env.NEXT_PUBLIC_SUPABASE_URL
const envAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

export const isSupabaseConfigured = Boolean(envUrl && envAnonKey)

if (!isSupabaseConfigured && typeof window === 'undefined') {
  console.log(
    '[v0] Supabase env vars are not set (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY); using placeholder config.',
  )
}

export const SUPABASE_URL = envUrl || 'https://placeholder.supabase.co'
export const SUPABASE_ANON_KEY = envAnonKey || 'placeholder-anon-key'
