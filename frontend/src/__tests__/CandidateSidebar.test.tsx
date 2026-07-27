import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CandidateSidebar from '@/components/CandidateSidebar'
import type { AccountListRow } from '@/lib/types'

const mockRpc = vi.fn().mockResolvedValue({ error: null })
const mockRefresh = vi.fn()

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({ rpc: mockRpc }),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: mockRefresh }),
}))

const candidate: AccountListRow = {
  id: 'abc-123',
  workspace_id: 'ws-1',
  slug: 'acme',
  name: 'Acme Corp',
  primary_domain: 'acme.com',
  additional_domains: [],
  vertical: null,
  crm_record_id: null,
  status: 'candidate',
  last_narrative_generated_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  overall_health_score: null,
  narrative_excerpt: null,
  last_signal_at: null,
  audit_passed: null,
  audit_criteria_passed: null,
  audit_criteria_total: null,
  audit_audited_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CandidateSidebar', () => {
  it('renders confirm and reject buttons', () => {
    render(<CandidateSidebar candidates={[candidate]} />)
    expect(screen.getByText('Confirm')).toBeTruthy()
    expect(screen.getByText('Reject')).toBeTruthy()
  })

  it('calls activate_candidate_account RPC on confirm', async () => {
    render(<CandidateSidebar candidates={[candidate]} />)
    fireEvent.click(screen.getByText('Confirm'))

    await vi.waitFor(() => {
      expect(mockRpc).toHaveBeenCalledWith('activate_candidate_account', { p_account_id: 'abc-123' })
    })
  })

  it('reject opens a confirmation dialog rather than calling the RPC immediately', async () => {
    render(<CandidateSidebar candidates={[candidate]} />)
    fireEvent.click(screen.getByText('Reject'))

    expect(await screen.findByRole('alertdialog')).toBeTruthy()
    expect(mockRpc).not.toHaveBeenCalled()
  })

  it('calls dismiss_candidate_account RPC after confirming the reject dialog', async () => {
    render(<CandidateSidebar candidates={[candidate]} />)
    fireEvent.click(screen.getByText('Reject'))
    await screen.findByRole('alertdialog')
    fireEvent.click(screen.getByText('Yes, reject'))

    await vi.waitFor(() => {
      expect(mockRpc).toHaveBeenCalledWith('dismiss_candidate_account', { p_account_id: 'abc-123' })
    })
  })

  it('cancelling the reject dialog does not call the RPC', async () => {
    render(<CandidateSidebar candidates={[candidate]} />)
    fireEvent.click(screen.getByText('Reject'))
    await screen.findByRole('alertdialog')
    fireEvent.click(screen.getByText('Cancel'))

    await vi.waitFor(() => {
      expect(screen.queryByRole('alertdialog')).toBeNull()
    })
    expect(mockRpc).not.toHaveBeenCalled()
  })
})
