import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import AccountTable from '@/components/AccountTable'
import { AccountListRow } from '@/lib/types'

function makeAccount(overrides: Partial<AccountListRow>): AccountListRow {
  return {
    id: overrides.id ?? 'acct-1',
    workspace_id: 'ws-1',
    slug: (overrides.name ?? 'account').toLowerCase().replace(/\s+/g, '-'),
    name: 'Account',
    primary_domain: null,
    additional_domains: [],
    vertical: null,
    crm_record_id: null,
    status: 'active',
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
    ...overrides,
  }
}

const accounts: AccountListRow[] = [
  makeAccount({
    id: 'acct-1',
    name: 'Formation Bio',
    primary_domain: 'formationbio.com',
    vertical: 'life_sciences',
    overall_health_score: 86,
    last_signal_at: '2026-05-01T00:00:00Z',
  }),
  makeAccount({
    id: 'acct-2',
    name: 'Thornfield AI',
    primary_domain: 'thornfield.ai',
    vertical: 'professional_services',
    overall_health_score: 52,
    last_signal_at: '2026-07-01T00:00:00Z',
  }),
  makeAccount({
    id: 'acct-3',
    name: 'Crucible',
    primary_domain: 'crucible.dev',
    vertical: 'software',
    overall_health_score: 34,
    last_signal_at: '2026-06-01T00:00:00Z',
  }),
  makeAccount({
    id: 'acct-4',
    name: 'Novagen Bio',
    primary_domain: 'novagenb.io',
    vertical: 'life_sciences',
    overall_health_score: null,
    last_signal_at: null,
  }),
]

function rowNames() {
  return screen.getAllByRole('link').map((el) => el.textContent)
}

beforeEach(() => {
  // jsdom doesn't implement layout, but the InputGroup addon click-to-focus
  // handler calls querySelector('input')?.focus() — harmless no-op here.
})

describe('AccountTable', () => {
  it('renders every account row', () => {
    render(<AccountTable accounts={accounts} />)
    expect(rowNames()).toEqual(['Formation Bio', 'Thornfield AI', 'Crucible', 'Novagen Bio'])
  })

  it('links to the account detail page under the given hrefPrefix', () => {
    render(<AccountTable accounts={accounts} hrefPrefix="/admin/workspaces/acme/accounts" />)
    const link = screen.getByText('Formation Bio').closest('a')
    expect(link?.getAttribute('href')).toBe('/admin/workspaces/acme/accounts/formation-bio')
  })

  it('shows a designed empty state when no accounts are passed in at all', () => {
    render(<AccountTable accounts={[]} />)
    expect(screen.getByText('No accounts to display')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('filters rows by search text against name and domain', () => {
    render(<AccountTable accounts={accounts} />)
    fireEvent.change(screen.getByLabelText('Search accounts'), { target: { value: 'thornfield' } })
    expect(rowNames()).toEqual(['Thornfield AI'])
  })

  it('filters rows by vertical', () => {
    render(<AccountTable accounts={accounts} />)
    fireEvent.change(screen.getByLabelText('Filter by vertical'), { target: { value: 'life_sciences' } })
    expect(rowNames()).toEqual(['Formation Bio', 'Novagen Bio'])
  })

  it('filters rows by health band', () => {
    render(<AccountTable accounts={accounts} />)
    fireEvent.change(screen.getByLabelText('Filter by health'), { target: { value: 'Unknown' } })
    expect(rowNames()).toEqual(['Novagen Bio'])
  })

  it('defaults to sorting by health descending, with unscored accounts last', () => {
    render(<AccountTable accounts={accounts} />)
    expect(rowNames()).toEqual(['Formation Bio', 'Thornfield AI', 'Crucible', 'Novagen Bio'])
  })

  it('clicking the Name header sorts ascending, clicking again reverses to descending', () => {
    render(<AccountTable accounts={accounts} />)
    fireEvent.click(screen.getByRole('button', { name: /^Name/ }))
    expect(rowNames()).toEqual(['Crucible', 'Formation Bio', 'Novagen Bio', 'Thornfield AI'])

    fireEvent.click(screen.getByRole('button', { name: /^Name/ }))
    expect(rowNames()).toEqual(['Thornfield AI', 'Novagen Bio', 'Formation Bio', 'Crucible'])
  })

  it('shows the no-results state when filters exclude every row, with a working clear action', () => {
    render(<AccountTable accounts={accounts} />)
    fireEvent.change(screen.getByLabelText('Search accounts'), { target: { value: 'nonexistent-co' } })
    expect(screen.getByText('No accounts match your filters')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()

    fireEvent.click(screen.getByText('Clear filters'))
    expect(rowNames()).toEqual(['Formation Bio', 'Thornfield AI', 'Crucible', 'Novagen Bio'])
  })
})
