/**
 * Tests for src/server/static/event.js — the embeddable JS event client.
 *
 * The script is an IIFE meant to run in a browser. We load the source
 * file, install a fake currentScript with data-key, and execute the IIFE
 * inside jsdom. fetch and sendBeacon are mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const EVENT_JS_PATH = resolve(__dirname, '../../../src/server/static/event.js')
const EVENT_JS_SRC = readFileSync(EVENT_JS_PATH, 'utf-8')

function mountEventJs(opts: { dataKey?: string; preload?: unknown[] } = {}): {
  fetchMock: ReturnType<typeof vi.fn>
  beaconMock: ReturnType<typeof vi.fn>
} {
  const fetchMock = vi.fn(() =>
    Promise.resolve({ status: 200 } as Response),
  )
  const beaconMock = vi.fn(() => true)

  globalThis.fetch = fetchMock
  navigator.sendBeacon = beaconMock

  if (opts.preload) {
    // @ts-expect-error — pre-load queue
    window.signal = opts.preload
  } else {
    // @ts-expect-error — clear
    window.signal = undefined
  }

  // Install a fake currentScript so the IIFE picks up data-key.
  const script = document.createElement('script')
  script.setAttribute('data-key', opts.dataKey ?? 'pk_live_test')
  Object.defineProperty(document, 'currentScript', {
    configurable: true,
    get: () => script,
  })

  // Execute the IIFE.
  new Function(EVENT_JS_SRC)()

  return { fetchMock, beaconMock }
}

describe('event.js', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('drains the pre-load queue on init', async () => {
    const { fetchMock } = mountEventJs({
      preload: [
        ['identify', { email: 'priya@example.com' }],
        ['track', 'page_viewed', { path: '/' }],
      ],
    })
    // Single track event, scheduled flush
    await vi.advanceTimersByTimeAsync(2100)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const opts = fetchMock.mock.calls[0][1] as RequestInit
    const body = JSON.parse(opts.body as string)
    expect(body.events.length).toBe(1)
    expect(body.events[0].event).toBe('page_viewed')
    expect(body.events[0].contact_email).toBe('priya@example.com')
  })

  it('batches at 10 events into a single fetch', async () => {
    const { fetchMock } = mountEventJs()
    // @ts-expect-error — push 10 track calls
    for (let i = 0; i < 10; i++) window.signal.push(['track', `evt_${i}`, {}])
    // 10th event triggers immediate flush (no timer needed)
    await Promise.resolve()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const opts = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(opts.body as string).events.length).toBe(10)
  })

  it('identify updates the email applied to subsequent track calls', async () => {
    const { fetchMock } = mountEventJs()
    // @ts-expect-error — runtime API
    window.signal.push(['identify', { email: 'first@x.com' }])
    // @ts-expect-error — runtime API on window
    window.signal.push(['track', 'a', {}])
    // @ts-expect-error — runtime API on window
    window.signal.push(['identify', { email: 'second@x.com' }])
    // @ts-expect-error — runtime API on window
    window.signal.push(['track', 'b', {}])
    await vi.advanceTimersByTimeAsync(2100)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.events[0].contact_email).toBe('first@x.com')
    expect(body.events[1].contact_email).toBe('second@x.com')
  })

  it('flushes via sendBeacon on beforeunload', () => {
    const { beaconMock } = mountEventJs()
    // @ts-expect-error — runtime API on window
    window.signal.push(['track', 'unload_evt', {}])
    window.dispatchEvent(new Event('beforeunload'))
    expect(beaconMock).toHaveBeenCalledTimes(1)
    const [url] = beaconMock.mock.calls[0]
    expect(url).toContain('key=pk_live_test')
  })

  it('does not fetch when no data-key is set', async () => {
    const { fetchMock } = mountEventJs({ dataKey: '' })
    // @ts-expect-error — runtime API on window
    window.signal.push(['track', 'a', {}])
    await vi.advanceTimersByTimeAsync(2100)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
