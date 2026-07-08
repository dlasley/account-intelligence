'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { createClient } from '@/lib/supabase/client'
import { track } from '@/lib/analytics'

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

type Status = 'idle' | 'loading' | 'saving' | 'sending' | 'sent' | 'error'

const CONTACT_NAME_SLOT = '[Contact Name]'

function nameForContact(
  c: { display_name: string | null; email: string } | undefined | null,
): string {
  return c ? c.display_name || c.email : CONTACT_NAME_SLOT
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

  async function handleSubjectBlur() {
    if (!draftId) return
    setStatus('saving')
    await supabase.rpc('update_outreach_draft', { p_draft_id: draftId, p_subject: subject })
    setStatus('idle')
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
    setStatus('saving')
    await supabase.rpc('update_outreach_draft', { p_draft_id: draftId, p_body: body })
    setStatus('idle')
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

  if (status === 'loading') {
    return <div className="text-sm text-gray-500 py-4">Loading outreach context…</div>
  }

  return (
    <section className="space-y-6">
      {isLowHealth && status !== 'sent' && (
        <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded text-sm text-amber-800">
          This account has a low health score ({overallHealthScore}). Consider a check-in to
          re-engage.
        </div>
      )}

      {status === 'sent' && (
        <div className="px-4 py-3 bg-green-50 border border-green-200 rounded text-sm text-green-800">
          Email sent.
        </div>
      )}

      {status === 'error' && errorMessage && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded text-sm text-red-800">
          {errorMessage}
        </div>
      )}

      {context && (
        <div className="px-3 py-2 bg-blue-50 border border-blue-100 rounded text-xs text-blue-700">
          {context.recommendation_rationale}
        </div>
      )}

      <div className="space-y-4">
        {contacts.length > 0 && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Recipient</label>
            <select
              value={contactId ?? ''}
              onChange={(e) => handleContactChange(e.target.value || null)}
              disabled={status === 'sent'}
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm disabled:bg-gray-50 disabled:text-gray-600"
            >
              <option value="">No recipient</option>
              {contacts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.display_name ? `${c.display_name} <${c.email}>` : c.email}
                </option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Intent</label>
          <div className="flex gap-2">
            {(['check_in', 'expansion', 'renewal', 'custom'] as const).map((i) => (
              <button
                key={i}
                onClick={() => setIntent(i)}
                disabled={status === 'sent'}
                className={`px-3 py-1.5 text-sm rounded border disabled:opacity-50 disabled:cursor-not-allowed ${
                  intent === i
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                }`}
              >
                {i === 'check_in'
                  ? 'Check-in'
                  : i === 'expansion'
                  ? 'Expansion'
                  : i === 'renewal'
                  ? 'Renewal'
                  : 'Custom'}
              </button>
            ))}
          </div>
        </div>

        {intentTemplates.length > 1 && (
          <div className="space-y-1">
            {intentTemplates.map((t) => (
              <label key={t.id} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="template"
                  value={t.id}
                  checked={selectedTemplateId === t.id}
                  onChange={() => handleTemplateSelect(t)}
                  className="accent-blue-600"
                />
                <span className="text-sm text-gray-700">{t.name}</span>
                {t.id === context?.recommended_template_id && (
                  <span className="text-xs text-blue-600 ml-1">Recommended</span>
                )}
              </label>
            ))}
          </div>
        )}
      </div>

      {context && (
        <div className="space-y-4 pt-2 border-t border-gray-100">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onBlur={handleSubjectBlur}
              readOnly={status === 'sent'}
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm read-only:bg-gray-50 read-only:text-gray-600"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Body</label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              onBlur={handleBodyBlur}
              readOnly={status === 'sent'}
              rows={10}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-y read-only:bg-gray-50 read-only:text-gray-600"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleSend}
              disabled={
                hasUnfilledSlots ||
                !contactId ||
                !draftId ||
                status === 'sending' ||
                status === 'sent'
              }
              className={`px-4 py-2 text-sm text-white rounded disabled:opacity-50 ${
                status === 'sent'
                  ? 'bg-gray-500'
                  : 'bg-green-600 hover:bg-green-700'
              }`}
            >
              {status === 'sending' ? 'Sending…' : status === 'sent' ? '✓ Sent' : 'Send'}
            </button>
            {status === 'sent' && (
              <span className="text-sm text-green-700 font-medium">
                Email sent successfully.
              </span>
            )}
            {status === 'saving' && <span className="text-xs text-gray-400">Saving…</span>}
            {status !== 'sent' && hasUnfilledSlots && (
              <span className="text-xs text-amber-600">
                Fill in all [placeholder] fields before sending.
              </span>
            )}
            {status !== 'sent' && !hasUnfilledSlots && !contactId && (
              <span className="text-xs text-amber-600">Select a recipient to send.</span>
            )}
          </div>
        </div>
      )}

      {context && context.signals.length > 0 && (
        <div className="space-y-2 pt-2 border-t border-gray-100">
          <h3 className="text-sm font-medium text-gray-700">Recent signals</h3>
          {context.signals.map((s, i) => (
            <div key={i} className="text-xs border border-gray-100 rounded p-2 space-y-1">
              <div className="flex gap-2 text-gray-500">
                <span>{new Date(s.occurred_at).toLocaleDateString()}</span>
                <span className="capitalize">{s.direction}</span>
                {s.subject && (
                  <span className="text-gray-700 font-medium truncate">{s.subject}</span>
                )}
              </div>
              {s.body_excerpt && <p className="text-gray-600 line-clamp-2">{s.body_excerpt}</p>}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
