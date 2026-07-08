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
  { id: 'contact-3', display_name: null, email: 'bob+outreach@formationbio.com' },
]

const CONTEXT_RESPONSE = {
  draft_id: 'draft-1',
  contact_id: 'contact-1',
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

// The display-value normalizer (testing-library) collapses newlines, so reading the
// greeting/body text back for assertions must go through the raw DOM `.value`, not
// `getByDisplayValue`.
function bodyTextarea(): HTMLTextAreaElement {
  return document.querySelector('textarea') as HTMLTextAreaElement
}

function contactSelect(): HTMLSelectElement {
  return screen.getByRole('combobox') as HTMLSelectElement
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

describe('OutreachTab - greeting name sync', () => {
  it('greeting follows the selected recipient and persists the re-filled name via RPC', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Reference something specific.]')
    )
    await waitFor(() =>
      expect(mockRpc).toHaveBeenCalledWith('update_outreach_draft', {
        p_draft_id: 'draft-1',
        p_contact_id: 'contact-2',
        p_body: 'Hi bob@formationbio.com,\n\n[Reference something specific.]',
      })
    )
  })

  it('recipient change preserves a user-edited greeting (no seeded name present → no-op swap)', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(bodyTextarea(), { target: { value: 'Hey there,\n\nJust checking in.' } })
    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(mockRpc).toHaveBeenCalledWith(
        'update_outreach_draft',
        expect.objectContaining({ p_contact_id: 'contact-2' })
      )
    )
    expect(bodyTextarea().value).toBe('Hey there,\n\nJust checking in.')
  })

  it('recipient change preserves an edited body paragraph while still tracking the name', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(bodyTextarea(), {
      target: {
        value: 'Hi Priya Sharma,\n\nI wanted to follow up on your custom onboarding needs.',
      },
    })
    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe(
        'Hi bob@formationbio.com,\n\nI wanted to follow up on your custom onboarding needs.'
      )
    )
  })

  it('selecting a template after a recipient change fills the current recipient, not the load-time one', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })
    await waitFor(() => expect(bodyTextarea().value).toContain('bob@formationbio.com'))

    const reengageRadio = screen.getByDisplayValue('check_in.reengagement')
    fireEvent.click(reengageRadio)

    await waitFor(() => expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Add context.]'))
    expect(bodyTextarea().value).not.toContain('Priya Sharma')
  })

  it('null-hop regression: A → No recipient → C shows C name in the greeting (surface 4)', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: '' } })
    await waitFor(() => expect(contactSelect().value).toBe(''))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Reference something specific.]')
    )

    const contactIdCalls = mockRpc.mock.calls.filter((call: unknown[]) => {
      const args = call[1] as Record<string, unknown>
      return 'p_contact_id' in args
    })
    expect(contactIdCalls).toHaveLength(1)
    expect(contactIdCalls[0][1]).toMatchObject({
      p_contact_id: 'contact-2',
      p_body: 'Hi bob@formationbio.com,\n\n[Reference something specific.]',
    })
  })

  it('substring-collision: only the greeting line updates, a later mention of the name is untouched', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...CONTEXT_RESPONSE,
          body: 'Hi Priya Sharma,\n\nAs Priya Sharma mentioned last week, we should follow up.',
        }),
      })
    )
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe(
        'Hi bob@formationbio.com,\n\nAs Priya Sharma mentioned last week, we should follow up.'
      )
    )
  })

  it('regex-escaping: a name containing a regex metacharacter swaps out cleanly on the next change', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    // First hop bakes the '+'-bearing email into the greeting as the "current" name.
    fireEvent.change(contactSelect(), { target: { value: 'contact-3' } })
    await waitFor(() =>
      expect(bodyTextarea().value).toBe(
        'Hi bob+outreach@formationbio.com,\n\n[Reference something specific.]'
      )
    )

    // Second hop swaps AWAY from the '+'-bearing name — this is what actually exercises
    // escapeRegExp on oldName; an unescaped '+' in the pattern would fail to match and
    // silently leave the old name in place.
    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Reference something specific.]')
    )
    expect(bodyTextarea().value).not.toContain('bob+outreach@formationbio.com')
  })

  it('deleted-contact dropdown fallback: seeded contact_id absent from contacts falls back to contacts[0]', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ...CONTEXT_RESPONSE, contact_id: 'contact-deleted-999' }),
      })
    )
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    expect(contactSelect().value).toBe('contact-1')
  })

  it('compound repro: A → No recipient → C shows C in the body greeting (currently RED pre-fix)', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: '' } })
    await waitFor(() => expect(contactSelect().value).toBe(''))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Reference something specific.]')
    )
  })

  it('compound: A → No recipient → template-select → C shows C name, not the placeholder', async () => {
    render(
      <OutreachTab accountSlug="formation-bio" accountId="acc-1" contacts={CONTACTS} overallHealthScore={75} />
    )
    await waitFor(() => expect(bodyTextarea().value).toContain('Priya Sharma'))

    fireEvent.change(contactSelect(), { target: { value: '' } })
    await waitFor(() => expect(contactSelect().value).toBe(''))

    // Radio click on a 2-template intent (check_in is the default intent, 2 templates).
    const reengageRadio = screen.getByDisplayValue('check_in.reengagement')
    fireEvent.click(reengageRadio)
    await waitFor(() => expect(bodyTextarea().value).toContain('[Contact Name]'))

    fireEvent.change(contactSelect(), { target: { value: 'contact-2' } })

    await waitFor(() =>
      expect(bodyTextarea().value).toBe('Hi bob@formationbio.com,\n\n[Add context.]')
    )
    expect(bodyTextarea().value).not.toContain('[Contact Name]')
  })
})
