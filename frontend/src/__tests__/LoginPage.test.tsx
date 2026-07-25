import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LoginPage from '@/app/login/page'

const mockSignInWithOAuth = vi.fn().mockResolvedValue({ error: null })
const mockSignInWithPassword = vi.fn()
const mockSignInWithOtp = vi.fn().mockResolvedValue({ error: null })

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: {
      signInWithOAuth: mockSignInWithOAuth,
      signInWithPassword: mockSignInWithPassword,
      signInWithOtp: mockSignInWithOtp,
    },
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  mockSignInWithPassword.mockResolvedValue({ error: null })
})

describe('LoginPage', () => {
  it('renders the wordmark and magic-link mode by default', () => {
    render(<LoginPage />)
    expect(screen.getByText('Account Intelligence')).toBeTruthy()
    expect(screen.getByText('Continue with Google')).toBeTruthy()
    expect(screen.getByText('Send sign-in link')).toBeTruthy()
    expect(screen.queryByLabelText('Password')).toBeNull()
  })

  it('toggling to password mode swaps the form and hides Google/magic-link chrome', () => {
    render(<LoginPage />)
    fireEvent.click(screen.getByText('Use password instead'))

    expect(screen.getByLabelText('Password')).toBeTruthy()
    expect(screen.getByText('Sign in')).toBeTruthy()
    expect(screen.queryByText('Continue with Google')).toBeNull()
    expect(screen.getByText('Use magic link instead')).toBeTruthy()
  })

  it('calls signInWithOAuth when Google is clicked', () => {
    render(<LoginPage />)
    fireEvent.click(screen.getByText('Continue with Google'))
    expect(mockSignInWithOAuth).toHaveBeenCalledWith({
      provider: 'google',
      options: { redirectTo: expect.stringContaining('/auth/callback') },
    })
  })

  it('shows a destructive alert on password sign-in failure', async () => {
    mockSignInWithPassword.mockResolvedValue({ error: { message: 'Incorrect email or password.' } })
    render(<LoginPage />)
    fireEvent.click(screen.getByText('Use password instead'))
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'avery@latticebuild.com' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByText('Sign in'))

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByText('Incorrect email or password.')).toBeTruthy()
    expect(mockSignInWithPassword).toHaveBeenCalledWith({
      email: 'avery@latticebuild.com',
      password: 'wrong',
    })
  })

  it('shows the magic-link-sent confirmation after a successful OTP request', async () => {
    render(<LoginPage />)
    fireEvent.change(screen.getByLabelText('Work email'), { target: { value: 'avery@latticebuild.com' } })
    fireEvent.click(screen.getByText('Send sign-in link'))

    await waitFor(() => expect(screen.getByText(/check your inbox/i)).toBeTruthy())
    expect(screen.getByText('avery@latticebuild.com')).toBeTruthy()
    expect(mockSignInWithOtp).toHaveBeenCalledWith({
      email: 'avery@latticebuild.com',
      options: { emailRedirectTo: expect.stringContaining('/auth/callback') },
    })
  })
})
