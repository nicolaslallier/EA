import { describe, expect, it } from 'vitest'

import { API_BASE_URL, defaultApiBaseUrl } from '../src/lib/api'

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
