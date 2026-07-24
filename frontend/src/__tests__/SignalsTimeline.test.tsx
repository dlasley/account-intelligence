import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SignalsTimeline from '@/components/SignalsTimeline'

// Partial mock via importOriginal: the Tabs and ScrollArea primitives this
// component renders import `cn` from this same module — a full replacement
// mock would silently drop it and break their className merging.
vi.mock('@/lib/utils', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/utils')>()
  return {
    ...actual,
    relativeTime: (s: string) => s,
  }
})

const SIGNALS = [
  {
    id: 'sig-1',
    direction: 'inbound' as const,
    channel: 'email',
    occurred_at: '2026-04-01T10:00:00Z',
    subject: 'Onboarding update',
    body: 'Line one\nLine two\nLine three',
  },
  {
    id: 'sig-2',
    direction: 'outbound' as const,
    channel: 'email',
    occurred_at: '2026-04-02T10:00:00Z',
    subject: 'Follow-up',
    body: 'Outbound body',
  },
  {
    id: 'sig-3',
    direction: 'internal' as const,
    channel: 'email',
    occurred_at: '2026-04-03T10:00:00Z',
    subject: null,
    body: 'Internal note',
  },
]

describe('SignalsTimeline', () => {
  it('renders "No signals" when list is empty', () => {
    render(<SignalsTimeline signals={[]} />)
    expect(screen.getByText('No signals.')).toBeTruthy()
  })

  it('renders signal rows with subject', () => {
    render(<SignalsTimeline signals={SIGNALS} />)
    expect(screen.getByText('Onboarding update')).toBeTruthy()
    expect(screen.getByText('Follow-up')).toBeTruthy()
  })

  // Filter pills are a Tabs segmented control; triggers select on pointerdown,
  // not click, so these use userEvent (full pointer sequence) instead of
  // fireEvent.click.
  it('INBOUND filter hides outbound signals', async () => {
    const user = userEvent.setup()
    render(<SignalsTimeline signals={SIGNALS} />)
    await user.click(screen.getByRole('tab', { name: 'inbound' }))
    expect(screen.getByText('Onboarding update')).toBeTruthy()
    expect(screen.queryByText('Follow-up')).toBeNull()
  })

  it('OUTBOUND filter hides inbound signals', async () => {
    const user = userEvent.setup()
    render(<SignalsTimeline signals={SIGNALS} />)
    await user.click(screen.getByRole('tab', { name: 'outbound' }))
    expect(screen.getByText('Follow-up')).toBeTruthy()
    expect(screen.queryByText('Onboarding update')).toBeNull()
  })

  it('expand toggle shows full body on click', () => {
    render(<SignalsTimeline signals={SIGNALS} />)
    const row = screen.getByText('Onboarding update').closest('div[class*="border"]')!
    fireEvent.click(row)
    expect(screen.getByText(/Line three/)).toBeTruthy()
  })

  describe('capped render window', () => {
    const MANY_SIGNALS = Array.from({ length: 20 }, (_, i) => ({
      id: `sig-${i}`,
      direction: 'inbound' as const,
      channel: 'email',
      occurred_at: '2026-04-01T10:00:00Z',
      subject: `Signal ${i}`,
      body: `Body ${i}`,
    }))

    it('renders only the first page and a "Show N more" control', () => {
      render(<SignalsTimeline signals={MANY_SIGNALS} />)
      expect(screen.getByText('Signal 0')).toBeTruthy()
      expect(screen.getByText('Signal 14')).toBeTruthy()
      expect(screen.queryByText('Signal 15')).toBeNull()
      expect(screen.getByText('Show 5 more signals')).toBeTruthy()
    })

    it('reveals more rows on click without a second page appearing prematurely', () => {
      render(<SignalsTimeline signals={MANY_SIGNALS} />)
      fireEvent.click(screen.getByText('Show 5 more signals'))
      expect(screen.getByText('Signal 19')).toBeTruthy()
      expect(screen.queryByText('Show 5 more signals')).toBeNull()
    })
  })
})
