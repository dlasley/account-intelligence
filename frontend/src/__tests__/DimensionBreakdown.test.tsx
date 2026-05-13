import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DimensionBreakdown from '@/components/DimensionBreakdown'

vi.mock('@/lib/analytics', () => ({
  track: vi.fn(),
  identify: vi.fn(),
  page: vi.fn(),
  group: vi.fn(),
  init: vi.fn(),
  reset: vi.fn(),
}))

const mockRpc = vi.fn()
const mockRefresh = vi.fn()

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ rpc: mockRpc }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mockRefresh }),
}))

vi.mock('@/lib/utils', () => ({
  scoreBadge: () => ({ color: 'bg-green-100 text-green-800', label: 'Good' }),
  relativeTime: () => '2 hours ago',
}))

const accountId = 'acct-1'

const dimScore = {
  score: 74,
  rationale: 'Good signal volume',
  scored_by: 'system',
  scored_at: '2026-04-01T00:00:00Z',
  metadata: null,
  dimension_id: 'dim-1',
}

const dimConfigs = [
  {
    id: 'dim-1',
    dimension_type: 'email',
    name: 'Email Health',
    weight: 0.7,
    enabled: true,
  },
]

beforeEach(() => {
  vi.clearAllMocks()
})

describe('DimensionBreakdown', () => {
  it('shows empty state when no scores', () => {
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={false} />,
    )
    expect(screen.getByText('No dimension scores yet.')).toBeTruthy()
  })

  it('renders dimension score row', () => {
    render(
      <DimensionBreakdown
        accountId={accountId}
        dimensionScores={[dimScore]}
        dimensionConfigs={dimConfigs}
        hasCsmConfig={false}
      />,
    )
    expect(screen.getByText('Email Health')).toBeTruthy()
    expect(screen.getByText(/74/)).toBeTruthy()
  })

  it('hides CSM form when hasCsmConfig is false', () => {
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={false} />,
    )
    expect(screen.queryByText('Update CSM Score')).toBeNull()
  })

  it('shows CSM form when hasCsmConfig is true', () => {
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    expect(screen.getByText('Update CSM Score')).toBeTruthy()
  })

  it('shows validation error for non-integer score', async () => {
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    fireEvent.change(screen.getByPlaceholderText('Score (1–100)'), {
      target: { value: '50.5' },
    })
    fireEvent.click(screen.getByText('Save'))
    expect(screen.getByText('Score must be an integer between 1 and 100.')).toBeTruthy()
    expect(mockRpc).not.toHaveBeenCalled()
  })

  it('shows validation error for out-of-range score', async () => {
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    fireEvent.change(screen.getByPlaceholderText('Score (1–100)'), {
      target: { value: '0' },
    })
    fireEvent.click(screen.getByText('Save'))
    expect(screen.getByText('Score must be an integer between 1 and 100.')).toBeTruthy()
  })

  it('calls rpc and refreshes on valid save', async () => {
    mockRpc.mockResolvedValue({ error: null })
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    fireEvent.change(screen.getByPlaceholderText('Score (1–100)'), {
      target: { value: '85' },
    })
    fireEvent.click(screen.getByText('Save'))
    await vi.waitFor(() => {
      expect(mockRpc).toHaveBeenCalledWith('set_csm_score', {
        p_account_id: accountId,
        p_score: 85,
        p_rationale: null,
      })
      expect(mockRefresh).toHaveBeenCalled()
    })
  })

  it('fires CSM Score Set after successful rpc', async () => {
    const { track } = await import('@/lib/analytics')
    vi.mocked(track).mockClear()
    mockRpc.mockResolvedValue({ error: null })
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    fireEvent.change(screen.getByPlaceholderText('Score (1–100)'), {
      target: { value: '85' },
    })
    fireEvent.click(screen.getByText('Save'))
    await vi.waitFor(() => {
      expect(track).toHaveBeenCalledWith(
        'CSM Score Set',
        expect.objectContaining({
          account_id: accountId,
          score: 85,
        }),
      )
    })
  })

  it('shows rpc error message on failure', async () => {
    mockRpc.mockResolvedValue({ error: { message: 'Permission denied' } })
    render(
      <DimensionBreakdown accountId={accountId} dimensionScores={[]} dimensionConfigs={[]} hasCsmConfig={true} />,
    )
    fireEvent.change(screen.getByPlaceholderText('Score (1–100)'), {
      target: { value: '70' },
    })
    fireEvent.click(screen.getByText('Save'))
    await vi.waitFor(() => {
      expect(screen.getByText('Permission denied')).toBeTruthy()
    })
    expect(mockRefresh).not.toHaveBeenCalled()
  })
})
