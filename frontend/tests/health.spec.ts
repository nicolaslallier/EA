import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchHealth } from '../src/lib/health'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(): ReturnType<typeof vi.fn> {
  const spy = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
  vi.stubGlobal('fetch', spy)
  return spy
}

describe('fetchHealth', () => {
  it('calls the backend origin, not the dev server serving the SPA', async () => {
    const spy = stubFetch()

    await fetchHealth()

    const url = String(spy.mock.calls[0][0])
    expect(url).toMatch(/^https?:\/\//)
    expect(new URL(url).port).not.toBe('5173')
    expect(new URL(url).pathname).toBe('/health')
  })

  it('returns the reported status', async () => {
    stubFetch()

    await expect(fetchHealth()).resolves.toBe('ok')
  })

  it('raises when the backend answers with a non-2xx status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 503 })),
    )

    await expect(fetchHealth()).rejects.toThrow(/503/)
  })
})
