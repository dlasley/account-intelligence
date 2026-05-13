import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

describe('AccountTabs', () => {
  it('renders both tab buttons', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    expect(screen.getByRole('button', { name: /overview/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /outreach/i })).toBeTruthy()
  })

  it('shows overview content by default', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    expect(screen.getByText('Overview content')).toBeTruthy()
    expect(screen.queryByText('Outreach content')).toBeNull()
  })

  it('shows outreach content after clicking the Outreach tab', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    fireEvent.click(screen.getByRole('button', { name: /outreach/i }))
    expect(screen.getByText('Outreach content')).toBeTruthy()
    expect(screen.queryByText('Overview content')).toBeNull()
  })

  it('applies active style to the selected tab', () => {
    render(<AccountTabs overviewContent={OVERVIEW} outreachContent={OUTREACH} />)
    const outreachBtn = screen.getByRole('button', { name: /outreach/i })
    fireEvent.click(outreachBtn)
    expect(outreachBtn.className).toContain('border-blue-600')
  })

  it('fires Outreach Tab Opened event when the Outreach tab is clicked', () => {
    render(
      <AccountTabs
        overviewContent={OVERVIEW}
        outreachContent={OUTREACH}
        accountId="acc-123"
        overallHealthScore={67}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /outreach/i }))
    expect(track).toHaveBeenCalledWith('Outreach Tab Opened', {
      account_id: 'acc-123',
      overall_health_score: 67,
    })
  })
})
