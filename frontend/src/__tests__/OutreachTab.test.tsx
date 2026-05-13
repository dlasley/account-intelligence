import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import OutreachTab from '@/components/OutreachTab'

vi.mock('@/lib/analytics', () => ({
  track: vi.fn(),
  identify: vi.fn(),
  page: vi.fn(),
  group: vi.fn(),
  init: vi.fn(),
  reset: vi.fn(),
}))

const mockGetSession = vi.fn()
const mockRpc = vi.fn().mockResolvedValue({ error: null })

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: { getSession: mockGetSession },
    rpc: mockRpc,
  }),
}))

const CONTACTS = [
  { id: 'contact-1', display_name: 'Priya Sharma', email: 'priya@formationbio.com' },
  { id: 'contact-2', display_name: null, email: 'bob@formationbio.com' },
]

const CONTEXT_RESPONSE = {
  draft_id: 'draft-1',
  subject: 'Checking in — Formation Bio',
  body: 'Hi Priya Sharma,\n\n[Reference something specific.]',
  recommended_template_id: 'check_in.casual',
  recommendation_rationale: 'No specific signal detected — default check-in suggested.',
  templates: [
    {
      id: 'check_in.casual',
      intent: 'check_in',
      name: 'Casual Check-in',
      subject: 'Checking in — Formation Bio',
      body: 'Hi Priya Sharma,\n\n[Reference something specific.]',
    },
    {
      id: 'check_in.reengagement',
      intent: 'check_in',
      name: 'Re-engagement',
      subject: 'Following up — Formation Bio',
      body: 'Hi Priya Sharma,\n\n[Add context.]',
    },
  ],
  signals: [
    {
      occurred_at: '2026-04-01T10:00:00Z',
      direction: 'inbound',
      subject: 'Re: Renewal discussion',
      body_excerpt: 'We are concerned about pricing.',
    },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetSession.mockResolvedValue({ data: { session: { access_token: 'test-token' } } })
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => CONTEXT_RESPONSE,
    })
  )
})

describe('OutreachTab', () => {
  it('shows loading state on mount', () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    expect(screen.getByText(/loading outreach context/i)).toBeTruthy()
  })

  it('renders intent buttons after context loads', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Check-in')).toBeTruthy())
    expect(screen.getByText('Expansion')).toBeTruthy()
    expect(screen.getByText('Renewal')).toBeTruthy()
    expect(screen.getByText('Custom')).toBeTruthy()
  })

  it('renders contact selector when contacts are provided', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Priya Sharma <priya@formationbio.com>')).toBeTruthy())
  })

  it('renders template picker with radio buttons for check_in intent (2 templates)', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Casual Check-in')).toBeTruthy())
    expect(screen.getByText('Re-engagement')).toBeTruthy()
    expect(screen.getAllByRole('radio')).toHaveLength(2)
  })

  it('selecting a template updates subject and body', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Re-engagement')).toBeTruthy())
    const reengageRadio = screen.getByDisplayValue('check_in.reengagement')
    fireEvent.click(reengageRadio)
    await waitFor(() =>
      expect(screen.getByDisplayValue('Following up — Formation Bio')).toBeTruthy()
    )
  })

  it('shows recommendation banner after context loads', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() =>
      expect(screen.getByText(/no specific signal detected/i)).toBeTruthy()
    )
  })

  it('send button disabled when subject contains placeholder', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Send')).toBeTruthy())
    const sendBtn = screen.getByText('Send').closest('button')!
    expect(sendBtn.disabled).toBe(true)
    expect(screen.getByText(/fill in all \[placeholder\]/i)).toBeTruthy()
  })

  it('shows signals panel when signals are present', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(screen.getByText('Recent signals')).toBeTruthy())
    expect(screen.getByText(/re: renewal discussion/i)).toBeTruthy()
    expect(screen.getByText(/concerned about pricing/i)).toBeTruthy()
  })

  it('shows low health banner when overall score < 40', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={25} />
    )
    await waitFor(() => expect(screen.getByText(/low health score/i)).toBeTruthy())
  })

  it('does not show low health banner when score >= 40', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={55} />
    )
    await waitFor(() => expect(screen.queryByText(/low health score/i)).toBeNull())
  })
})
