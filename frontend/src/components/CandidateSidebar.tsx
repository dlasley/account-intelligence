'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { AccountListRow } from '@/lib/types'
import { createClient } from '@/lib/supabase/client'
import { relativeTime } from '@/lib/utils'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

export default function CandidateSidebar({ candidates }: { candidates: AccountListRow[] }) {
  const router = useRouter()
  const [rejectTarget, setRejectTarget] = useState<AccountListRow | null>(null)

  const handleConfirm = async (id: string) => {
    const supabase = createClient()
    await supabase.rpc('activate_candidate_account', { p_account_id: id })
    router.refresh()
  }

  const handleReject = async (id: string) => {
    const supabase = createClient()
    await supabase.rpc('dismiss_candidate_account', { p_account_id: id })
    router.refresh()
  }

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold text-foreground">Candidates</h2>
      <div className="space-y-3">
        {candidates.map((c) => (
          <Card key={c.id}>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-foreground">{c.name}</div>
                  <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                    {c.primary_domain && `${c.primary_domain} · `}
                    first signal {relativeTime(c.last_signal_at)}
                  </div>
                </div>
                {c.vertical && (
                  <Badge variant="secondary" className="shrink-0">
                    {c.vertical}
                  </Badge>
                )}
              </div>
              <div className="flex gap-2">
                <Button size="sm" className="flex-1" onClick={() => handleConfirm(c.id)}>
                  Confirm
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="flex-1"
                  onClick={() => setRejectTarget(c)}
                >
                  Reject
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <AlertDialog
        open={rejectTarget !== null}
        onOpenChange={(open) => !open && setRejectTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reject {rejectTarget?.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This dismisses the candidate. It won&apos;t be tracked again unless it&apos;s
              detected in a future signal.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (rejectTarget) handleReject(rejectTarget.id)
                setRejectTarget(null)
              }}
            >
              Yes, reject
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
