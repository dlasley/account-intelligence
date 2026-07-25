'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, Search } from 'lucide-react'
import { AccountListRow } from '@/lib/types'
import { scoreBadge, relativeTime, HEALTH_BAND_LABELS } from '@/lib/utils'
import AuditBadge from '@/components/AuditBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type SortKey = 'name' | 'vertical' | 'health' | 'last_signal'
type SortDir = 'asc' | 'desc'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'health', label: 'Health' },
  { value: 'name', label: 'Name' },
  { value: 'vertical', label: 'Vertical' },
  { value: 'last_signal', label: 'Last signal' },
]

// Ascending reads naturally for text (A→Z); descending reads naturally for
// health and recency (best/newest first) — this is the direction a header
// click or a fresh Select choice lands on before the user reverses it.
const DEFAULT_DIR: Record<SortKey, SortDir> = {
  name: 'asc',
  vertical: 'asc',
  health: 'desc',
  last_signal: 'desc',
}

function sortValue(row: AccountListRow, key: SortKey): string | number | null {
  switch (key) {
    case 'name':
      return row.name
    case 'vertical':
      return row.vertical
    case 'health':
      return row.overall_health_score
    case 'last_signal':
      return row.last_signal_at
  }
}

// Rows missing the sort field always sink to the bottom regardless of
// direction (an unset vertical or an ungenerated health score isn't a "low"
// value the user is asking to see first when they flip to ascending).
function sortAccounts(rows: AccountListRow[], key: SortKey, dir: SortDir): AccountListRow[] {
  const dirMult = dir === 'asc' ? 1 : -1
  const withValue = rows.filter((r) => sortValue(r, key) !== null)
  const withoutValue = rows.filter((r) => sortValue(r, key) === null)

  withValue.sort((a, b) => {
    const va = sortValue(a, key)!
    const vb = sortValue(b, key)!
    const cmp = typeof va === 'number' ? va - (vb as number) : String(va).localeCompare(String(vb))
    return cmp * dirMult
  })
  withoutValue.sort((a, b) => a.name.localeCompare(b.name))

  return [...withValue, ...withoutValue]
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="relative">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 shrink-0 appearance-none rounded-lg border border-input bg-transparent py-1 pr-7 pl-2.5 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
    </div>
  )
}

function SortIndicator({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <ArrowUpDown className="size-3 text-muted-foreground/50" />
  return dir === 'asc' ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />
}

function EmptyMessage({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
      <p className="text-sm font-semibold">{title}</p>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">{description}</p>
    </div>
  )
}

export default function AccountTable({
  accounts,
  hrefPrefix = '/accounts',
}: {
  accounts: AccountListRow[]
  hrefPrefix?: string
}) {
  const [search, setSearch] = useState('')
  const [verticalFilter, setVerticalFilter] = useState('all')
  const [healthFilter, setHealthFilter] = useState('all')
  const [sortKey, setSortKey] = useState<SortKey>('health')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const verticalOptions = useMemo(() => {
    const verticals = new Set(accounts.map((a) => a.vertical).filter((v): v is string => v !== null))
    return Array.from(verticals).sort((a, b) => a.localeCompare(b))
  }, [accounts])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return accounts.filter((a) => {
      const matchesSearch =
        q === '' || a.name.toLowerCase().includes(q) || (a.primary_domain ?? '').toLowerCase().includes(q)
      const matchesVertical = verticalFilter === 'all' || a.vertical === verticalFilter
      const matchesHealth = healthFilter === 'all' || scoreBadge(a.overall_health_score).label === healthFilter
      return matchesSearch && matchesVertical && matchesHealth
    })
  }, [accounts, search, verticalFilter, healthFilter])

  const sorted = useMemo(() => sortAccounts(filtered, sortKey, sortDir), [filtered, sortKey, sortDir])

  if (accounts.length === 0) {
    return (
      <EmptyMessage title="No accounts to display" description="Accounts will appear here once they're tracked." />
    )
  }

  const handleHeaderSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(DEFAULT_DIR[key])
    }
  }

  const handleSortKeyChange = (value: string) => {
    const key = value as SortKey
    if (key === sortKey) return
    setSortKey(key)
    setSortDir(DEFAULT_DIR[key])
  }

  const handleClearFilters = () => {
    setSearch('')
    setVerticalFilter('all')
    setHealthFilter('all')
  }

  const sortableHead = (key: SortKey, label: string, className?: string) => {
    const active = sortKey === key
    return (
      <TableHead className={className} aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button
          type="button"
          onClick={() => handleHeaderSort(key)}
          className="inline-flex items-center gap-1 font-medium hover:text-foreground"
        >
          {label}
          <SortIndicator active={active} dir={sortDir} />
        </button>
      </TableHead>
    )
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-2">
        <InputGroup className="min-w-[200px] flex-1">
          <InputGroupAddon>
            <Search className="size-3.5" />
          </InputGroupAddon>
          <InputGroupInput
            placeholder="Search accounts…"
            aria-label="Search accounts"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </InputGroup>
        <FilterSelect
          label="Filter by vertical"
          value={verticalFilter}
          onChange={setVerticalFilter}
          options={[{ value: 'all', label: 'All verticals' }, ...verticalOptions.map((v) => ({ value: v, label: v }))]}
        />
        <FilterSelect
          label="Filter by health"
          value={healthFilter}
          onChange={setHealthFilter}
          options={[
            { value: 'all', label: 'All health' },
            ...HEALTH_BAND_LABELS.map((label) => ({ value: label, label })),
          ]}
        />
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted-foreground">Sort:</span>
          <FilterSelect label="Sort by" value={sortKey} onChange={handleSortKeyChange} options={SORT_OPTIONS} />
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label={`Sort direction ${sortDir === 'asc' ? 'ascending' : 'descending'}, click to reverse`}
            onClick={() => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
          >
            {sortDir === 'asc' ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />}
          </Button>
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
          <p className="text-sm font-semibold">No accounts match your filters</p>
          <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
            Try a different search term or clear the filters below.
          </p>
          <Button size="sm" variant="outline" className="mt-4" onClick={handleClearFilters}>
            Clear filters
          </Button>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {sortableHead('name', 'Name')}
              <TableHead className="hidden md:table-cell">Domain</TableHead>
              {sortableHead('vertical', 'Vertical')}
              {sortableHead('health', 'Health')}
              {sortableHead('last_signal', 'Last signal')}
              <TableHead className="hidden md:table-cell">Summary</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((a) => {
              const health = scoreBadge(a.overall_health_score)
              return (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link href={`${hrefPrefix}/${a.slug}`} className="font-medium text-primary hover:underline">
                      {a.name}
                    </Link>
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell">
                    {a.primary_domain ?? '—'}
                  </TableCell>
                  <TableCell>
                    {a.vertical ? (
                      <Badge variant="secondary">{a.vertical}</Badge>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge className={health.color}>
                        {a.overall_health_score !== null ? `${a.overall_health_score} ${health.label}` : '—'}
                      </Badge>
                      <AuditBadge
                        passed={a.audit_passed ?? null}
                        criteriaPassedCount={a.audit_criteria_passed ?? null}
                        criteriaTotal={a.audit_criteria_total ?? null}
                        auditedAt={a.audit_audited_at ?? null}
                        variant="pill"
                      />
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{relativeTime(a.last_signal_at)}</TableCell>
                  <TableCell className="hidden max-w-xs truncate whitespace-nowrap text-muted-foreground md:table-cell">
                    {a.narrative_excerpt ?? <span className="text-muted-foreground/70 italic">No narrative yet</span>}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
