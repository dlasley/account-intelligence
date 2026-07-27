'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { Check, ChevronDown, Loader2 } from 'lucide-react'
import { createClient } from '@/lib/supabase/client'
import { track } from '@/lib/analytics'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'

type Signal = {
  occurred_at: string
  direction: string
  subject: string | null
  body_excerpt: string | null
}

type Template = {
  id: string
  intent: string
  name: string
  subject: string
  body: string
}

type ContextResponse = {
  draft_id: string
  workspace_id: string
  contact_id: string | null
  subject: string
  body: string
  recommended_template_id: string
  recommendation_rationale: string
  templates: Template[]
  signals: Signal[]
}

type Props = {
  accountSlug: string
  accountId: string
  contacts: { id: string; display_name: string | null; email: string }[]
  overallHealthScore: number | null
}

// Send lifecycle only. Autosave is tracked separately (`isSaving`) so a blur-save
// resolving mid-send cannot reset this back to `idle`.
type Status = 'idle' | 'loading' | 'sending' | 'sent' | 'error'

const CONTACT_NAME_SLOT = '[Contact Name]'

// Salutations use the given name only — "Hi Avery," not "Hi Avery Davis,".
// Contacts with no display name fall back to the email, which has no given name
// to extract. Both the old and new name in a greeting swap resolve through here,
// so the swap continues to match whatever form is in the text.
function nameForContact(
  c: { display_name: string | null; email: string } | undefined | null,
): string {
  if (!c) return CONTACT_NAME_SLOT
  if (!c.display_name) return c.email
  return c.display_name.trim().split(/\s+/)[0]
}

function nameForContactId(contacts: Props['contacts'], id: string | null): string {
  return nameForContact(contacts.find((c) => c.id === id))
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function swapGreetingName(body: string, oldName: string, newName: string): string {
  const greetingLine = new RegExp(`^Hi ${escapeRegExp(oldName)},`, 'm')
  return body.replace(greetingLine, `Hi ${newName},`)
}

export default function OutreachTab({ accountSlug, accountId, contacts, overallHealthScore }: Props) {
  const [intent, setIntent] = useState<'check_in' | 'expansion' | 'renewal' | 'custom'>('check_in')
  const [context, setContext] = useState<ContextResponse | null>(null)
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null)
  const [draftId, setDraftId] = useState<string | null>(null)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [contactId, setContactId] = useState<string | null>(contacts[0]?.id ?? null)
  const [status, setStatus] = useState<Status>('idle')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Tracks the contact whose name is actually baked into subject/body text right now,
  // independent of the dropdown's live `contactId` — required because "No recipient"
  // (contactId = null) leaves the text untouched (see handleContactChange), so the next
  // real recipient change must still know what name is currently in the text to swap.
  const lastAppliedNameContactIdRef = useRef<string | null>(null)

  const supabase = createClient()

  async function getAuthHeader(): Promise<string> {
    const { data } = await supabase.auth.getSession()
    const token = data.session?.access_token
    if (!token) throw new Error('Not authenticated')
    return `Bearer ${token}`
  }

  const intentTemplates = useMemo(
    () => context?.templates.filter((t) => t.intent === intent) ?? [],
    [context, intent],
  )

  const loadedRecipientName = nameForContact(contacts[0])

  const handleTemplateSelect = useCallback(
    async (t: Template) => {
      const recipientName = nameForContactId(contacts, contactId)
      const filledSubject = t.subject.replaceAll(loadedRecipientName, recipientName)
      const filledBody = t.body.replaceAll(loadedRecipientName, recipientName)
      setSelectedTemplateId(t.id)
      setSubject(filledSubject)
      setBody(filledBody)
      // The ref must reflect the name actually baked into the text, including the
      // placeholder-null case during a "No recipient" hop (contactId === null here
      // means recipientName resolved to CONTACT_NAME_SLOT) — see code-review revision
      // #1 in outreach-greeting-sync-spec-2026-07-08.md. Without this, a template
      // select during a null hop leaves the ref pointing at a stale contact while the
      // text now shows the placeholder, breaking the next real recipient swap.
      lastAppliedNameContactIdRef.current = contactId
      track('Outreach Template Selected', {
        account_id: accountId,
        intent,
        template_id: t.id,
      })
      if (draftId) {
        await supabase.rpc('update_outreach_draft', {
          p_draft_id: draftId,
          p_subject: filledSubject,
          p_body: filledBody,
          p_intent: intent,
          p_template_id: t.id,
        })
      }
    },
    [accountId, contactId, contacts, draftId, intent, loadedRecipientName, supabase],
  )

  useEffect(() => {
    async function loadContext() {
      setStatus('loading')
      try {
        const authHeader = await getAuthHeader()
        const workerUrl = process.env.NEXT_PUBLIC_WORKER_URL ?? ''
        const resp = await fetch(`${workerUrl}/outreach/${accountSlug}/context`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: authHeader },
          body: JSON.stringify({ contact_id: contactId }),
        })
        if (!resp.ok) throw new Error(`Error ${resp.status}`)
        const data: ContextResponse = await resp.json()
        setContext(data)
        setDraftId(data.draft_id)
        setSubject(data.subject)
        setBody(data.body)
        setSelectedTemplateId(data.recommended_template_id)
        // Validate against the live contacts list as defense-in-depth (e.g. a
        // persisted draft's contact_id no longer resolving in `contacts`); fall back
        // to contacts[0] rather than seeding an unselectable dropdown value.
        const seededContactId =
          data.contact_id && contacts.some((c) => c.id === data.contact_id)
            ? data.contact_id
            : contacts[0]?.id ?? null
        setContactId(seededContactId)
        lastAppliedNameContactIdRef.current = seededContactId
        setStatus('idle')
      } catch (err) {
        setErrorMessage(err instanceof Error ? err.message : 'Unknown error')
        setStatus('error')
      }
    }
    loadContext()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // mount only — intent switching is client-side from this point

  useEffect(() => {
    if (intentTemplates.length === 1 && intentTemplates[0].id !== selectedTemplateId) {
      handleTemplateSelect(intentTemplates[0])
    }
  }, [intentTemplates, selectedTemplateId, handleTemplateSelect])

  // Autosave tracks its own flag rather than `status`. A blur fires on mousedown
  // and the Send click on mouseup, so a save that resolved into `status` would
  // overwrite `sending` and re-enable the Send button mid-flight.
  async function handleSubjectBlur() {
    if (!draftId) return
    setIsSaving(true)
    const { error } = await supabase.rpc('update_outreach_draft', {
      p_draft_id: draftId,
      p_subject: subject,
    })
    setIsSaving(false)
    if (error) {
      setSaveError('Could not save the subject. Your last edit may be lost.')
      return
    }
    setSaveError(null)
    track('Outreach Template Edited', {
      account_id: accountId,
      intent,
      template_id: selectedTemplateId,
      edit_made: true,
      field: 'subject',
    })
  }

  async function handleBodyBlur() {
    if (!draftId) return
    setIsSaving(true)
    const { error } = await supabase.rpc('update_outreach_draft', {
      p_draft_id: draftId,
      p_body: body,
    })
    setIsSaving(false)
    if (error) {
      setSaveError('Could not save the body. Your last edit may be lost.')
      return
    }
    setSaveError(null)
    track('Outreach Template Edited', {
      account_id: accountId,
      intent,
      template_id: selectedTemplateId,
      edit_made: true,
      field: 'body',
    })
  }

  async function handleContactChange(newContactId: string | null) {
    setContactId(newContactId)
    if (newContactId === null) {
      // ADR-019 D8: update_outreach_draft cannot clear contact_id (NULL param means
      // "leave unchanged"), so "No recipient" is UI-only — leave the text as-is and
      // don't call the RPC. Deliberately do NOT advance lastAppliedNameContactIdRef:
      // the text still bears the previous recipient's name, so the next real
      // selection must still swap against that name, not the placeholder.
      return
    }
    if (!draftId) return

    const oldName = nameForContactId(contacts, lastAppliedNameContactIdRef.current)
    const newName = nameForContactId(contacts, newContactId)
    // Body-only swap (revision #4): no template carries [Contact Name] in its subject
    // (only [Account Name]), so a subject swap could only ever mutate user-edited
    // subject text — harm, never help.
    const nextBody = oldName === newName ? body : swapGreetingName(body, oldName, newName)

    setBody(nextBody)
    lastAppliedNameContactIdRef.current = newContactId

    await supabase.rpc('update_outreach_draft', {
      p_draft_id: draftId,
      p_contact_id: newContactId,
      p_body: nextBody,
    })
  }

  async function handleSend() {
    if (!draftId) return
    setStatus('sending')
    setErrorMessage(null)
    try {
      const authHeader = await getAuthHeader()
      const workerUrl = process.env.NEXT_PUBLIC_WORKER_URL ?? ''
      const resp = await fetch(`${workerUrl}/outreach/send/${draftId}`, {
        method: 'POST',
        headers: { Authorization: authHeader },
      })
      if (!resp.ok) {
        const messages: Record<number, string> = {
          400: 'Draft cannot be sent — missing required fields.',
          409: 'Draft already sent.',
          502: 'Email delivery failed — please retry.',
        }
        throw new Error(messages[resp.status] ?? 'Something went wrong. Please try again.')
      }
      setStatus('sent')
      track('Outreach Sent', {
        account_id: accountId,
        intent,
        template_id: selectedTemplateId,
        contact_id: contactId,
      })
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Unknown error')
      setStatus('error')
    }
  }

  const hasUnfilledSlots = subject.includes('[') || body.includes('[')
  const isLowHealth = overallHealthScore !== null && overallHealthScore < 40
  const sendDisabled =
    hasUnfilledSlots || !contactId || !draftId || status === 'sending' || status === 'sent'

  if (status === 'loading') {
    return (
      <section className="space-y-4" aria-busy="true">
        <span className="sr-only">Loading outreach context…</span>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </section>
    )
  }

  return (
    <section className="space-y-6">
      {isLowHealth && status !== 'sent' && (
        <div className="rounded-md border border-health-risk/40 bg-health-risk-soft px-3 py-2 text-sm text-health-risk-on">
          This account has a low health score ({overallHealthScore}). Consider a check-in to
          re-engage.
        </div>
      )}

      {status === 'sent' && (
        <div className="flex items-center gap-3 rounded-md border border-health-strong/40 bg-health-strong-soft px-3 py-2.5 text-sm text-health-strong-on">
          <Check className="size-4 shrink-0" />
          Email sent.
        </div>
      )}

      {status === 'error' && errorMessage && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage}</AlertDescription>
        </Alert>
      )}

      {context && (
        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {context.recommendation_rationale}
        </div>
      )}

      <div className="space-y-4">
        {contacts.length > 0 && (
          <div className="space-y-1.5">
            <Label htmlFor="outreach-recipient">Recipient</Label>
            <div className="relative">
              <select
                id="outreach-recipient"
                value={contactId ?? ''}
                onChange={(e) => handleContactChange(e.target.value || null)}
                disabled={status === 'sent'}
                className="h-8 w-full appearance-none rounded-lg border border-input bg-transparent px-2.5 py-1 pr-7 text-sm text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:bg-input/50 disabled:text-muted-foreground"
              >
                <option value="">No recipient</option>
                {contacts.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.display_name ? `${c.display_name} <${c.email}>` : c.email}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute top-1/2 right-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            </div>
          </div>
        )}

        <div className="space-y-1.5">
          <Label>Intent</Label>
          <div className="flex flex-wrap gap-2">
            {(['check_in', 'expansion', 'renewal', 'custom'] as const).map((i) => (
              <Button
                key={i}
                type="button"
                size="sm"
                variant={intent === i ? 'default' : 'outline'}
                disabled={status === 'sent'}
                onClick={() => setIntent(i)}
              >
                {i === 'check_in'
                  ? 'Check-in'
                  : i === 'expansion'
                    ? 'Expansion'
                    : i === 'renewal'
                      ? 'Renewal'
                      : 'Custom'}
              </Button>
            ))}
          </div>
        </div>

        {intentTemplates.length > 1 && (
          <div className="space-y-1.5">
            {intentTemplates.map((t) => (
              <label key={t.id} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="template"
                  value={t.id}
                  checked={selectedTemplateId === t.id}
                  onChange={() => handleTemplateSelect(t)}
                  className="accent-primary"
                />
                <span className="text-foreground">{t.name}</span>
                {t.id === context?.recommended_template_id && (
                  <span className="text-xs text-primary">Recommended</span>
                )}
              </label>
            ))}
          </div>
        )}
      </div>

      {context && (
        <div className="space-y-4 border-t border-border pt-4">
          <div className="space-y-1.5">
            <Label htmlFor="outreach-subject">Subject</Label>
            <Input
              id="outreach-subject"
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onBlur={handleSubjectBlur}
              readOnly={status === 'sent'}
              className="read-only:bg-muted/50 read-only:text-muted-foreground"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="outreach-body">Body</Label>
            <Textarea
              id="outreach-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onBlur={handleBodyBlur}
              readOnly={status === 'sent'}
              rows={10}
              className="min-h-48 resize-y read-only:bg-muted/50 read-only:text-muted-foreground"
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={handleSend}
              disabled={sendDisabled}
              variant={status === 'error' ? 'destructive' : 'default'}
              className={cn(
                status === 'sent' &&
                  'bg-health-strong text-health-strong-on hover:bg-health-strong',
              )}
            >
              {status === 'sending' && <Loader2 className="animate-spin" />}
              {status === 'sent' && <Check />}
              {status === 'sending'
                ? 'Sending…'
                : status === 'sent'
                  ? 'Sent'
                  : status === 'error'
                    ? 'Failed · Retry'
                    : 'Send'}
            </Button>
            {status === 'sent' && (
              <span role="status" className="text-sm font-medium text-health-strong-on">
                Email sent successfully.
              </span>
            )}
            {isSaving && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" /> Saving…
              </span>
            )}
            {saveError && (
              <span role="alert" className="text-xs text-destructive">
                {saveError}
              </span>
            )}
            {status !== 'sent' && hasUnfilledSlots && (
              <span className="text-xs text-health-moderate-on">
                Fill in all [placeholder] fields before sending.
              </span>
            )}
            {status !== 'sent' && !hasUnfilledSlots && !contactId && (
              <span className="text-xs text-health-moderate-on">Select a recipient to send.</span>
            )}
          </div>
        </div>
      )}

      {context && context.signals.length > 0 && (
        <div className="space-y-2 border-t border-border pt-4">
          <h3 className="text-sm font-medium text-foreground">Recent signals</h3>
          {context.signals.map((s, i) => (
            <div key={i} className="space-y-1 rounded-md border border-border p-2 text-xs">
              <div className="flex gap-2 text-muted-foreground">
                <span>{new Date(s.occurred_at).toLocaleDateString()}</span>
                <span className="capitalize">{s.direction}</span>
                {s.subject && (
                  <span className="truncate font-medium text-foreground">{s.subject}</span>
                )}
              </div>
              {s.body_excerpt && (
                <p className="line-clamp-2 text-muted-foreground">{s.body_excerpt}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
