import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AccountTabs from '@/components/AccountTabs'
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

vi.mock('@/components/NarrativeSection', () => ({ default: () => <div>NarrativeSection</div> }))
vi.mock('@/components/OutreachTab', () => ({ default: () => <div>OutreachTab</div> }))

const OVERVIEW = <div>Overview content</div>
const OUTREACH = <div>Outreach content</div>

// Tab triggers select on pointerdown, not click, so tests drive them with
// userEvent (which dispatches the full pointer sequence) rather than
// fireEvent.click.
describe('AccountTabs', () => {
  it('renders both tab triggers', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    expect(screen.getByRole('tab', { name: /overview/i })).toBeTruthy()
    expect(screen.getByRole('tab', { name: /outreach/i })).toBeTruthy()
  })

  it('shows overview content by default', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    expect(screen.getByText('Overview content')).toBeTruthy()
    expect(screen.queryByText('Outreach content')).toBeNull()
  })

  it('shows outreach content after clicking the Outreach tab', async () => {
    const user = userEvent.setup()
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    await user.click(screen.getByRole('tab', { name: /outreach/i }))
    expect(screen.getByText('Outreach content')).toBeTruthy()
    expect(screen.queryByText('Overview content')).toBeNull()
  })

  it('marks the selected tab active via aria-selected', async () => {
    const user = userEvent.setup()
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    const outreachTab = screen.getByRole('tab', { name: /outreach/i })
    await user.click(outreachTab)
    expect(outreachTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: /overview/i }).getAttribute('aria-selected')).toBe(
      'false',
    )
  })

  it('fires Outreach Tab Opened event when the Outreach tab is clicked', async () => {
    const user = userEvent.setup()
    render(
      <AccountTabs
        overviewContent={OVERVIEW}
        outreachContent={OUTREACH}
        accountId="acc-123"
        overallHealthScore={67}
      />,
    )
    await user.click(screen.getByRole('tab', { name: /outreach/i }))
    expect(track).toHaveBeenCalledWith('Outreach Tab Opened', {
      account_id: 'acc-123',
      overall_health_score: 67,
    })
  })
})
