import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchHealth } from '../src/lib/health'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(): ReturnType<typeof vi.fn> {
  const spy = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 })))
  vi.stubGlobal('fetch', spy)
  return spy
}

describe('fetchHealth', () => {
  it('calls the backend origin, not the dev server serving the SPA', async () => {
    const spy = stubFetch()

    await fetchHealth()

    // The generated client hands `fetch` a `Request`, not a bare URL string.
    const url = new Request(spy.mock.calls[0][0] as RequestInfo).url
    expect(url).toMatch(/^https?:\/\//)
    expect(new URL(url).port).not.toBe('5173')
    expect(new URL(url).pathname).toBe('/health')
  })

  it('returns the reported status', async () => {
    stubFetch()

    await expect(fetchHealth()).resolves.toEqual({ status: 'ok', degraded: [] })
  })

  it('reads back the sections a degraded deployment will refuse', async () => {
    // docs/adr/0037: a store that serves one section no longer stops the
    // process, so `/health` says which one is missing.
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ status: 'degraded', degraded: ['files'] }), {
            status: 200,
          }),
        ),
      ),
    )

    await expect(fetchHealth()).resolves.toEqual({ status: 'degraded', degraded: ['files'] })
  })

  it('reads an answer without the field as nothing degraded', async () => {
    // The field carries a server-side default, so the schema marks it
    // optional; a caller mapping over it must never get `undefined`.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))),
    )

    await expect(fetchHealth()).resolves.toEqual({ status: 'ok', degraded: [] })
  })

  it('raises when the backend answers with a non-2xx status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('nope', { status: 503 }))),
    )

    await expect(fetchHealth()).rejects.toThrow(/503/)
  })
})
