'use client'

import { useState } from 'react'
import { ScrollArea } from '@/components/ui/scroll-area'

export type Contact = {
  id: string
  display_name: string | null
  email: string
  is_internal: boolean
}

// Rows shown before the list is capped behind "See all N".
const VISIBLE_CAP = 7

export default function ContactsList({ contacts }: { contacts: Contact[] }) {
  const external = contacts.filter((c) => !c.is_internal)
  const [showAll, setShowAll] = useState(false)

  const hasOverflow = external.length > VISIBLE_CAP
  const shown = showAll ? external : external.slice(0, VISIBLE_CAP)

  const rows = (
    <ul className="flex flex-col">
      {shown.map((c) => (
        <li key={c.id} className="border-b border-border px-3.5 py-2.5 text-sm last:border-b-0">
          <div className="font-medium">{c.display_name ?? c.email}</div>
          {c.display_name && (
            <div className="font-mono text-xs text-muted-foreground">{c.email}</div>
          )}
        </li>
      ))}
    </ul>
  )

  return (
    <section className="overflow-hidden rounded-xl border border-border">
      <div className="flex items-center justify-between border-b border-border px-3.5 py-3">
        <h2 className="text-sm font-semibold">Contacts</h2>
        <span className="font-mono text-xs text-muted-foreground">{external.length}</span>
      </div>

      {external.length === 0 ? (
        <p className="px-3.5 py-3 text-sm text-muted-foreground">No contacts identified.</p>
      ) : showAll && hasOverflow ? (
        // The capped view never needs to scroll; only the expanded "See all"
        // list gets its own scrolling viewport.
        <ScrollArea className="h-[420px]">{rows}</ScrollArea>
      ) : (
        rows
      )}

      {hasOverflow && (
        <div className="border-t border-border px-3.5 py-2.5 text-center">
          <button
            onClick={() => setShowAll((v) => !v)}
            className="text-xs font-medium text-primary hover:underline"
          >
            {showAll ? 'Show fewer' : `See all ${external.length}`}
          </button>
        </div>
      )}
    </section>
  )
}
