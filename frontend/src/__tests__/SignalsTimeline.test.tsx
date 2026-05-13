import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SignalsTimeline from '@/components/SignalsTimeline'

vi.mock('@/lib/utils', () => ({
  relativeTime: (s: string) => s,
}))

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

  it('INBOUND filter hides outbound signals', () => {
    render(<SignalsTimeline signals={SIGNALS} />)
    fireEvent.click(screen.getByRole('button', { name: 'inbound' }))
    expect(screen.getByText('Onboarding update')).toBeTruthy()
    expect(screen.queryByText('Follow-up')).toBeNull()
  })

  it('OUTBOUND filter hides inbound signals', () => {
    render(<SignalsTimeline signals={SIGNALS} />)
    fireEvent.click(screen.getByRole('button', { name: 'outbound' }))
    expect(screen.getByText('Follow-up')).toBeTruthy()
    expect(screen.queryByText('Onboarding update')).toBeNull()
  })

  it('expand toggle shows full body on click', () => {
    render(<SignalsTimeline signals={SIGNALS} />)
    const row = screen.getByText('Onboarding update').closest('div[class*="border"]')!
    fireEvent.click(row)
    expect(screen.getByText(/Line three/)).toBeTruthy()
  })
})
