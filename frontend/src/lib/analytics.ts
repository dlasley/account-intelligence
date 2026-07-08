/**
 * Analytics wrapper module (PostHog).
 *
 * All call sites import only from here — never from posthog-js directly.
 * See ADR-014 for the wrapper-module and workspace-property decisions.
 */

import posthog from 'posthog-js'

function isEnabled(): boolean {
  return process.env.NEXT_PUBLIC_POSTHOG_ENABLED === 'true'
}

/** Initialize PostHog. Called once from PostHogProvider on mount. */
export function init(): void {
  if (!isEnabled()) return
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY ?? ''
  const host = process.env.NEXT_PUBLIC_POSTHOG_HOST ?? 'https://us.i.posthog.com'
  if (!key) return
  posthog.init(key, {
    api_host: host,
    capture_pageview: false, // manual page tracking only
    autocapture: false, // explicit per-event tracking only — URLs contain customer-identifiable account slugs (e.g. /accounts/quantas-labs), and capturing every DOM click is broader than the brief intends
    persistence: 'localStorage+cookie',
  })
}

/**
 * Capture a user-facing analytics event.
 *
 * Fire-and-log: exceptions are caught and logged at console.warn; never re-thrown.
 */
export function track(event: string, properties: Record<string, unknown> = {}): void {
  if (!isEnabled()) return
  try {
    posthog.capture(event, properties)
  } catch (err) {
    console.warn('[analytics] track failed for event', event, err)
  }
}

/**
 * Identify the current user after Supabase session is confirmed.
 *
 * Passes workspace context as properties so PostHog can segment without group analytics
 * (workspace_id is not passed as $groups — ADR-014 §2).
 */
export function identify(userId: string, traits: Record<string, unknown> = {}): void {
  if (!isEnabled()) return
  try {
    posthog.identify(userId, traits)
  } catch (err) {
    console.warn('[analytics] identify failed', err)
  }
}

/**
 * Track a page view.
 * Called explicitly (capture_pageview is disabled in init) to avoid double-counting
 * in Next.js App Router where the provider renders once but routes change client-side.
 */
export function page(url?: string): void {
  if (!isEnabled()) return
  try {
    posthog.capture('$pageview', url ? { $current_url: url } : {})
  } catch (err) {
    console.warn('[analytics] page failed', err)
  }
}

/**
 * Group stub — deferred per ADR-014 §2.
 * Preserved in the interface so call sites don't need to change when groups are enabled.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function group(_groupType: string, _groupKey: string, _traits?: Record<string, unknown>): void {
  // No-op until group analytics is enabled (ADR-014 §Conditions for enabling groups).
}

export function reset(): void {
  if (!isEnabled()) return
  try {
    posthog.reset()
  } catch (err) {
    console.warn('[analytics] reset failed', err)
  }
}
