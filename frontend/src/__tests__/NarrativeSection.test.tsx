import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import NarrativeSection from '@/components/NarrativeSection'
import { track } from '@/lib/analytics'

vi.mock('@/lib/analytics', () => ({
  track: vi.fn(),
  identify: vi.fn(),
  page: vi.fn(),
  group: vi.fn(),
  init: vi.fn(),
  reset: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(track).mockClear()
})

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    rpc: vi.fn().mockResolvedValue({ data: null }),
    from: vi.fn().mockReturnValue({
      select: vi.fn().mockReturnThis(),
      eq: vi.fn().mockReturnThis(),
      is: vi.fn().mockReturnThis(),
      maybeSingle: vi.fn().mockResolvedValue({ data: null }),
    }),
  }),
}))

describe('NarrativeSection', () => {
  it('shows "No narrative yet" when narrative is null', () => {
    render(<NarrativeSection narrative={null} accountId="acc-1" workspaceId="ws-1" />)
    expect(screen.getByText(/no narrative yet/i)).toBeTruthy()
  })

  it('shows regenerate button', () => {
    render(<NarrativeSection narrative={null} accountId="acc-1" workspaceId="ws-1" />)
    expect(screen.getByText('Regenerate')).toBeTruthy()
  })

  it('renders narrative text when provided', () => {
    const narrative = {
      narrative: 'Account is healthy with strong engagement.',
      engagement: 90,
      engagement_rationale: '5 signals in the last 14 days from 2 contacts.',
      sentiment: 72,
      generated_at: '2026-04-20T12:00:00Z',
    }
    render(<NarrativeSection narrative={narrative} accountId="acc-1" workspaceId="ws-1" />)
    expect(screen.getByText(/Account is healthy/)).toBeTruthy()
  })

  it('shows pending message when sentiment is null', () => {
    const narrative = {
      narrative: 'Account is healthy.',
      engagement: 90,
      engagement_rationale: '5 signals in the last 14 days.',
      sentiment: null,
      generated_at: '2026-04-20T12:00:00Z',
    }
    render(<NarrativeSection narrative={narrative} accountId="acc-1" workspaceId="ws-1" />)
    expect(screen.getByText(/Pending next regeneration/i)).toBeTruthy()
  })

  it('fires Narrative Viewed once on mount when narrative is present', () => {
    const narrative = {
      narrative: 'Account is healthy.',
      engagement: 90,
      engagement_rationale: '5 signals in the last 14 days.',
      sentiment: 72,
      generated_at: '2026-04-20T12:00:00Z',
    }
    render(<NarrativeSection narrative={narrative} accountId="acc-42" workspaceId="ws-1" />)
    expect(track).toHaveBeenCalledWith(
      'Narrative Viewed',
      expect.objectContaining({
        account_id: 'acc-42',
        narrative_id: null,
        narrative_age_hours: expect.any(Number),
      }),
    )
  })

  it('does not fire Narrative Viewed when narrative is null', () => {
    render(<NarrativeSection narrative={null} accountId="acc-42" workspaceId="ws-1" />)
    expect(track).not.toHaveBeenCalled()
  })
})
