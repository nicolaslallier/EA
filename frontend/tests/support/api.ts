import { vi } from 'vitest'

import type { components } from '../../src/api/schema'

type ElementRead = components['schemas']['ElementRead']

/** A route the stubbed backend answers, matched on method and path. */
export type Route = {
  method?: string
  path: string
  status?: number
  body?: unknown
}

export type RecordedCall = { method: string; url: URL }

/**
 * Stand in for the backend.
 *
 * The generated client goes through `globalThis.fetch`, so stubbing it covers
 * the real request the component makes — the URL, the query string and the
 * body — rather than a mocked module that would let a wrong URL pass.
 */
export function stubApi(routes: Route[]): RecordedCall[] {
  const calls: RecordedCall[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const url = new URL(request.url)
      calls.push({ method: request.method, url })

      const route = routes.find(
        (candidate) =>
          (candidate.method ?? 'GET') === request.method && candidate.path === url.pathname,
      )
      if (!route) {
        throw new TypeError(`no stub for ${request.method} ${url.pathname}`)
      }
      const status = route.status ?? 200
      if (status === 204) {
        return new Response(null, { status })
      }
      return new Response(JSON.stringify(route.body ?? {}), {
        status,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )

  return calls
}

/** An element as the API returns it, with only the interesting fields spelled out. */
export function anElement(overrides: Partial<ElementRead> = {}): ElementRead {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    element_type: 'application_component',
    layer: 'application',
    aspect: 'active_structure',
    name: 'Facturation',
    description: '',
    documentation: '',
    properties: {},
    created_at: '2026-09-07T12:00:00Z',
    updated_at: '2026-09-07T12:00:00Z',
    ...overrides,
  }
}

export function aPage(items: ElementRead[], total = items.length) {
  return { items, total, limit: 25, offset: 0 }
}

export const METAMODEL = {
  element_types: [
    {
      value: 'application_component',
      label: 'Application Component',
      layer: 'application',
      aspect: 'active_structure',
    },
    {
      value: 'business_process',
      label: 'Business Process',
      layer: 'business',
      aspect: 'behavior',
    },
  ],
  relationship_types: ['serving'],
  layers: ['business', 'application'],
}
