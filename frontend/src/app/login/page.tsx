'use client'

import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { createClient } from '@/lib/supabase/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Alert, AlertDescription } from '@/components/ui/alert'

type Mode = 'magic' | 'password'

function GoogleIcon() {
  return (
    <svg className="size-4" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"
      />
    </svg>
  )
}

function Wordmark({ subtitle }: { subtitle: string }) {
  return (
    <div className="mb-5 text-center">
      <div className="text-base font-bold tracking-tight text-foreground">Account Intelligence</div>
      <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
    </div>
  )
}

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [mode, setMode] = useState<Mode>('magic')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleGoogleSignIn = async () => {
    setLoading(true)
    setError(null)
    const supabase = createClient()
    const origin = window.location.origin
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${origin}/auth/callback` },
    })
    // signInWithOAuth navigates the browser away; control returns only on error
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const supabase = createClient()
    if (mode === 'password') {
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      })
      if (signInError) {
        setError(signInError.message)
        setLoading(false)
        return
      }
      // Session is set in cookies by the Supabase client; navigate to the app.
      window.location.href = '/accounts'
      return
    }
    const origin = window.location.origin
    await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${origin}/auth/callback` },
    })
    setSubmitted(true)
    setLoading(false)
  }

  if (submitted) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
        <Card className="w-full max-w-sm">
          <CardContent>
            <Wordmark subtitle="Magic link sent" />
            <div className="flex items-center gap-3 rounded-lg border border-health-strong-soft bg-health-strong-soft px-3 py-2.5">
              <span
                aria-hidden="true"
                className="flex size-7 shrink-0 items-center justify-center rounded-md bg-health-strong font-bold text-health-strong-on"
              >
                ✓
              </span>
              <p className="text-sm text-foreground">
                Check your inbox — link sent to{' '}
                <span className="font-mono text-xs">{email}</span>
              </p>
            </div>
          </CardContent>
        </Card>
      </main>
    )
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 px-4">
      <Card className="w-full max-w-sm">
        <CardContent>
          <Wordmark
            subtitle={mode === 'password' ? 'Sign in with password' : 'Sign in to your workspace'}
          />

          {mode === 'magic' && (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={handleGoogleSignIn}
                disabled={loading}
                className="w-full"
              >
                {loading ? <Loader2 className="animate-spin" /> : <GoogleIcon />}
                {loading ? 'Redirecting…' : 'Continue with Google'}
              </Button>

              <div className="my-4 flex items-center gap-2.5">
                <Separator className="flex-1" />
                <span className="font-mono text-[10px] tracking-wide text-muted-foreground uppercase">
                  Or
                </span>
                <Separator className="flex-1" />
              </div>
            </>
          )}

          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="email">{mode === 'password' ? 'Email' : 'Work email'}</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                aria-invalid={error ? true : undefined}
                placeholder="you@company.com"
              />
            </div>

            {mode === 'password' && (
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  aria-invalid={error ? true : undefined}
                />
              </div>
            )}

            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <Button type="submit" disabled={loading} className="w-full">
              {loading && <Loader2 className="animate-spin" />}
              {loading
                ? mode === 'password'
                  ? 'Signing in…'
                  : 'Sending…'
                : mode === 'password'
                  ? 'Sign in'
                  : 'Send sign-in link'}
            </Button>

            <button
              type="button"
              onClick={() => {
                setMode(mode === 'magic' ? 'password' : 'magic')
                setError(null)
                setPassword('')
              }}
              className="w-full text-center text-xs text-primary hover:underline"
            >
              {mode === 'magic' ? 'Use password instead' : 'Use magic link instead'}
            </button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}
