import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

// mockRpc is mutable per-test (mockResolvedValueOnce) so an individual test
// can override the default response without a second vi.mock block.
const mockRpc = vi.fn()

beforeEach(() => {
  vi.mocked(track).mockClear()
  mockRpc.mockReset()
  mockRpc.mockResolvedValue({ data: null, error: null })
})

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    rpc: mockRpc,
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

  it('shows the loading skeleton state on first-ever generation (no existing narrative)', () => {
    render(<NarrativeSection narrative={null} accountId="acc-1" workspaceId="ws-1" />)
    fireEvent.click(screen.getByText('Regenerate'))
    expect(screen.getByText('Regenerating…')).toBeTruthy()
    expect(screen.queryByText(/no narrative yet/i)).toBeNull()
  })

  it('shows the destructive error state with Retry when the enqueue rpc fails', async () => {
    mockRpc.mockResolvedValueOnce({ data: null, error: { message: 'Permission denied' } })
    render(<NarrativeSection narrative={null} accountId="acc-1" workspaceId="ws-1" />)
    fireEvent.click(screen.getByText('Regenerate'))
    expect(await screen.findByText("Couldn't generate the narrative")).toBeTruthy()
    expect(screen.getByText('Permission denied')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('shows the audit-fail banner when the narrative is flagged unverified', () => {
    const narrative = {
      narrative: 'Account is healthy.',
      engagement: 90,
      engagement_rationale: '5 signals in the last 14 days.',
      sentiment: 72,
      generated_at: '2026-04-20T12:00:00Z',
      auditPassed: false,
      auditCriteriaPassed: 3,
      auditCriteriaTotal: 5,
      auditAuditedAt: '2026-04-20T12:00:00Z',
    }
    render(<NarrativeSection narrative={narrative} accountId="acc-1" workspaceId="ws-1" />)
    expect(screen.getByText('Unverified', { exact: false })).toBeTruthy()
  })
})
