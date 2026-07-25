'use client'

import { useMemo, useState } from 'react'
import { relativeTime } from '@/lib/utils'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'

export type Signal = {
  id: string
  direction: 'inbound' | 'outbound' | 'internal'
  channel: string
  occurred_at: string
  subject: string | null
  body: string
}

// Rows rendered per page of the capped render window below.
const PAGE_SIZE = 15

// The design system has one brand accent, neutrals, and a health ramp — no
// dedicated "direction" palette — so inbound borrows the health-strong tone,
// outbound uses the accent, and internal uses standard muted text.
function DirectionIcon({ direction }: { direction: string }) {
  if (direction === 'inbound') {
    return (
      <span className="font-bold text-health-strong-on" role="img" aria-label="Inbound">
        ↓
      </span>
    )
  }
  if (direction === 'outbound') {
    return (
      <span className="font-bold text-primary" role="img" aria-label="Outbound">
        ↑
      </span>
    )
  }
  return (
    <span className="text-muted-foreground" role="img" aria-label="Internal">
      ↔
    </span>
  )
}

export default function SignalsTimeline({ signals }: { signals: Signal[] }) {
  const [filter, setFilter] = useState<'all' | 'inbound' | 'outbound'>('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const filtered = useMemo(
    () => (filter === 'all' ? signals : signals.filter((s) => s.direction === filter)),
    [signals, filter],
  )
  const visible = filtered.slice(0, visibleCount)
  const remaining = filtered.length - visible.length

  const handleFilterChange = (next: string) => {
    setFilter(next as 'all' | 'inbound' | 'outbound')
    setVisibleCount(PAGE_SIZE) // reset the capped window on filter change
  }

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">
          Signals{' '}
          <span className="font-mono text-xs font-normal text-muted-foreground">
            · {signals.length}
          </span>
        </h2>
        <Tabs value={filter} onValueChange={handleFilterChange}>
          <TabsList aria-label="Filter signals by direction">
            {(['all', 'inbound', 'outbound'] as const).map((f) => (
              <TabsTrigger key={f} value={f} className="text-xs">
                {f}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">No signals.</p>
      ) : (
        // The timeline scrolls independently of the page inside a fixed-height
        // viewport. Only `visibleCount` rows mount at a time; "Show N more"
        // grows that window rather than every row mounting up front.
        <ScrollArea className="h-[480px] rounded-lg border border-border">
          <div className="flex flex-col gap-2 p-3">
            {visible.map((s) => {
              const isExpanded = expanded.has(s.id)
              const preview = s.body.split('\n').slice(0, 2).join(' ')
              return (
                <div
                  key={s.id}
                  className="rounded-lg border border-border p-3 hover:bg-muted/50"
                >
                  {/* min-w-0 lets the flex children shrink below their content
                      width; without it the subject's `truncate` cannot engage
                      and long subjects widen the row instead of ellipsing. */}
                  <button
                    type="button"
                    onClick={() => toggleExpand(s.id)}
                    aria-expanded={isExpanded}
                    aria-controls={`signal-body-${s.id}`}
                    className="flex w-full min-w-0 cursor-pointer items-center gap-2 text-left text-sm"
                  >
                    <DirectionIcon direction={s.direction} />
                    <span className="shrink-0 font-mono text-xs text-muted-foreground">
                      {relativeTime(s.occurred_at)}
                    </span>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                      {s.channel}
                    </span>
                    <span className="min-w-0 truncate font-medium">{s.subject ?? '(no subject)'}</span>
                  </button>
                  <p
                    id={`signal-body-${s.id}`}
                    className={`mt-1 text-xs break-words text-muted-foreground ${isExpanded ? '' : 'line-clamp-2'}`}
                  >
                    {isExpanded ? s.body : preview}
                  </p>
                </div>
              )
            })}
            {remaining > 0 && (
              <button
                onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
                className="py-1 text-center text-sm font-medium text-primary hover:underline"
              >
                Show {remaining} more signal{remaining !== 1 ? 's' : ''}
              </button>
            )}
          </div>
        </ScrollArea>
      )}
    </section>
  )
}
