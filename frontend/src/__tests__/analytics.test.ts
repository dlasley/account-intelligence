/**
 * Tests for frontend/src/lib/analytics.ts
 *
 * posthog-js is mocked at module level so no real network calls occur.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import posthog from 'posthog-js'

// posthog-js is mocked via the factory below. vi.mock is hoisted, so mock fns
// must be created inside the factory (cannot reference outer const variables).
vi.mock('posthog-js', () => ({
  default: {
    init: vi.fn(),
    capture: vi.fn(),
    identify: vi.fn(),
    reset: vi.fn(),
  },
}))

import * as analytics from '@/lib/analytics'

// Typed accessors for the mocked methods
const mockInit = vi.mocked(posthog.init)
const mockCapture = vi.mocked(posthog.capture)
const mockIdentify = vi.mocked(posthog.identify)

beforeEach(() => {
  vi.clearAllMocks()
})

// ---------------------------------------------------------------------------
// init / POSTHOG_ENABLED guard
// ---------------------------------------------------------------------------

describe('init', () => {
  it('does not call posthog.init when POSTHOG_ENABLED is not "true"', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'false')
    analytics.init()
    expect(mockInit).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })

  it('does not call posthog.init when NEXT_PUBLIC_POSTHOG_KEY is missing', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_KEY', '')
    analytics.init()
    expect(mockInit).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })

  it('calls posthog.init with key and host when enabled', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_KEY', 'phc_testkey')
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_HOST', 'https://eu.i.posthog.com')
    analytics.init()
    expect(mockInit).toHaveBeenCalledWith(
      'phc_testkey',
      expect.objectContaining({ api_host: 'https://eu.i.posthog.com' })
    )
    vi.unstubAllEnvs()
  })
})

// ---------------------------------------------------------------------------
// track
// ---------------------------------------------------------------------------

describe('track', () => {
  it('is a no-op when POSTHOG_ENABLED is false', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'false')
    analytics.track('Account Viewed', { account_id: 'acc-1' })
    expect(mockCapture).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })

  it('calls posthog.capture with event name and properties', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    analytics.track('Account Viewed', { account_id: 'acc-1', account_slug: 'test', overall_health_score: 75 })
    expect(mockCapture).toHaveBeenCalledWith('Account Viewed', {
      account_id: 'acc-1',
      account_slug: 'test',
      overall_health_score: 75,
    })
    vi.unstubAllEnvs()
  })

  it('does not throw when posthog.capture throws', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    mockCapture.mockImplementation(() => { throw new Error('network') })
    expect(() => analytics.track('Account Viewed', {})).not.toThrow()
    vi.unstubAllEnvs()
  })
})

// ---------------------------------------------------------------------------
// identify
// ---------------------------------------------------------------------------

describe('identify', () => {
  it('is a no-op when POSTHOG_ENABLED is false', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'false')
    analytics.identify('user-1', { email: 'test@example.com' })
    expect(mockIdentify).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })

  it('calls posthog.identify with userId and traits', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    analytics.identify('user-1', { email: 'test@example.com', workspace_id: 'ws-1' })
    expect(mockIdentify).toHaveBeenCalledWith('user-1', {
      email: 'test@example.com',
      workspace_id: 'ws-1',
    })
    vi.unstubAllEnvs()
  })
})

// ---------------------------------------------------------------------------
// group (stub — no-op per ADR-014)
// ---------------------------------------------------------------------------

describe('group', () => {
  it('is always a no-op (group analytics deferred per ADR-014)', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    // group does not call any posthog methods — just ensure it does not throw
    expect(() => analytics.group('workspace', 'ws-1', { name: 'Test' })).not.toThrow()
    expect(mockCapture).not.toHaveBeenCalled()
    expect(mockIdentify).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })
})

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

describe('page', () => {
  it('fires $pageview when enabled', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'true')
    analytics.page('/accounts/test')
    expect(mockCapture).toHaveBeenCalledWith('$pageview', { $current_url: '/accounts/test' })
    vi.unstubAllEnvs()
  })

  it('is a no-op when disabled', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_ENABLED', 'false')
    analytics.page('/accounts/test')
    expect(mockCapture).not.toHaveBeenCalled()
    vi.unstubAllEnvs()
  })
})
