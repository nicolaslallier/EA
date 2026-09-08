import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, API_BASE_URL, defaultApiBaseUrl } from '../src/lib/api'

// The Vite dev server binds every interface (docs/adr/0019), so the SPA is
// loaded as often from `http://192.168.1.x:5173` as from localhost. A base URL
// hard-wired to `localhost:8000` would then name the *viewer's* machine, and
// every call would fail on a machine that runs no backend at all.
describe('defaultApiBaseUrl', () => {
  it('keeps the loopback address when the SPA is served from localhost', () => {
    expect(defaultApiBaseUrl({ protocol: 'http:', hostname: 'localhost' })).toBe(
      'http://localhost:8000',
    )
  })

  it('follows the host the SPA was loaded from', () => {
    expect(defaultApiBaseUrl({ protocol: 'http:', hostname: '192.168.1.50' })).toBe(
      'http://192.168.1.50:8000',
    )
  })

  it('keeps the scheme, so a page served over TLS never calls plain HTTP', () => {
    expect(defaultApiBaseUrl({ protocol: 'https:', hostname: 'ea.lan' })).toBe(
      'https://ea.lan:8000',
    )
  })

  it('falls back to loopback where there is no document to read a host from', () => {
    expect(defaultApiBaseUrl(undefined)).toBe('http://localhost:8000')
  })
})

describe('API_BASE_URL', () => {
  it('carries no trailing slash, so a generated path never doubles it', () => {
    expect(API_BASE_URL.endsWith('/')).toBe(false)
  })
})

// One line per call, in the browser, because that is the only place this half
// of the stack leaves a trace — and it carries the id the backend put on the
// answer, so a line here and a line in the server log are the same request.
// See docs/adr/0021.
describe('what a call leaves in the console', () => {
  function answering(status: number, body: unknown, requestId = 'abc123'): typeof fetch {
    return vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json', 'x-request-id': requestId },
      }),
    ) as unknown as typeof fetch
  }

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('names the method, the path and the status', async () => {
    vi.stubGlobal('fetch', answering(200, { status: 'ok' }))
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})

    await api.GET('/health', {})

    expect(debug.mock.calls[0]).toEqual(['[api] → GET /health'])
    const [message, fields] = debug.mock.calls[1] as [string, Record<string, unknown>]
    expect(message).toContain('GET /health')
    expect(fields.status).toBe(200)
    expect(fields.duration_ms).toBeGreaterThanOrEqual(0)
  })

  it('carries the backend request id, which is what joins the two logs', async () => {
    vi.stubGlobal('fetch', answering(200, { status: 'ok' }, 'server-42'))
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})

    await api.GET('/health', {})

    const [, fields] = debug.mock.calls[1] as [string, Record<string, unknown>]
    expect(fields.request_id).toBe('server-42')
  })

  it('a refused call is a warning and not a line lost among the rest', async () => {
    vi.stubGlobal('fetch', answering(404, { error: 'not_found', detail: 'nope' }))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await api.GET('/elements/{element_id}', {
      params: { path: { element_id: '00000000-0000-0000-0000-000000000000' } },
    })

    const [message, fields] = warn.mock.calls[0] as [string, Record<string, unknown>]
    expect(message).toContain('/elements/')
    expect(fields.status).toBe(404)
  })

  it('a backend that never answered is an error, with the reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    await expect(api.GET('/health', {})).rejects.toThrow()

    const [message] = error.mock.calls[0] as [string]
    expect(message).toContain('GET /health')
  })
})
