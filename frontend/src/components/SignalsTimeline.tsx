'use client'

import { useState } from 'react'
import { relativeTime } from '@/lib/utils'

export type Signal = {
  id: string
  direction: 'inbound' | 'outbound' | 'internal'
  channel: string
  occurred_at: string
  subject: string | null
  body: string
}

function DirectionIcon({ direction }: { direction: string }) {
  if (direction === 'inbound') return <span className="text-green-600 font-bold">↓</span>
  if (direction === 'outbound') return <span className="text-blue-600 font-bold">↑</span>
  return <span className="text-gray-400">↔</span>
}

export default function SignalsTimeline({ signals }: { signals: Signal[] }) {
  const [filter, setFilter] = useState<'all' | 'inbound' | 'outbound'>('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const filtered =
    filter === 'all' ? signals : signals.filter((s) => s.direction === filter)

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
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Signals</h2>
        <div className="flex gap-1">
          {(['all', 'inbound', 'outbound'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2 py-1 text-xs rounded ${
                filter === f
                  ? 'bg-gray-800 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="text-gray-400 text-sm">No signals.</p>
      ) : (
        <div className="space-y-1">
          {filtered.map((s) => {
            const isExpanded = expanded.has(s.id)
            const preview = s.body.split('\n').slice(0, 2).join(' ')
            return (
              <div
                key={s.id}
                onClick={() => toggleExpand(s.id)}
                className="p-3 border rounded hover:bg-gray-50 cursor-pointer"
              >
                <div className="flex items-center gap-2 text-sm">
                  <DirectionIcon direction={s.direction} />
                  <span className="text-gray-400 text-xs">{relativeTime(s.occurred_at)}</span>
                  <span className="text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">
                    {s.channel}
                  </span>
                  <span className="font-medium truncate">{s.subject ?? '(no subject)'}</span>
                </div>
                <p className={`mt-1 text-xs text-gray-600 ${isExpanded ? '' : 'line-clamp-2'}`}>
                  {isExpanded ? s.body : preview}
                </p>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
