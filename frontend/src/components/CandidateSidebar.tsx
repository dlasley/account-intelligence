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
  // Holds the id being acted on so its card's controls disable, preventing a
  // second RPC before the refreshed list arrives.
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const runAction = async (id: string, rpc: 'activate_candidate_account' | 'dismiss_candidate_account') => {
    if (pendingId) return
    setPendingId(id)
    setActionError(null)
    const supabase = createClient()
    const { error } = await supabase.rpc(rpc, { p_account_id: id })
    if (error) {
      setActionError('Could not update the candidate. Please try again.')
      setPendingId(null)
      return
    }
    router.refresh()
    setPendingId(null)
  }

  const handleConfirm = (id: string) => runAction(id, 'activate_candidate_account')
  const handleReject = (id: string) => runAction(id, 'dismiss_candidate_account')

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold text-foreground">Candidates</h2>
      {actionError && (
        <p role="alert" className="mb-3 text-sm text-destructive">
          {actionError}
        </p>
      )}
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
                <Button
                  size="sm"
                  className="flex-1"
                  disabled={pendingId !== null}
                  onClick={() => handleConfirm(c.id)}
                >
                  {pendingId === c.id ? 'Working…' : 'Confirm'}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="flex-1"
                  disabled={pendingId !== null}
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
