import { vi } from 'vitest'

import type { components } from '../../src/api/schema'

type ElementRead = components['schemas']['ElementRead']
type RelationshipRead = components['schemas']['RelationshipRead']
type DocumentSummaryRead = components['schemas']['DocumentSummaryRead']
type DocumentRead = components['schemas']['DocumentRead']

/** A route the stubbed backend answers, matched on method and path. */
export type Route = {
  method?: string
  path: string
  status?: number
  body?: unknown
}

export type RecordedCall = { method: string; url: URL; body: unknown }

/**
 * Stand in for the backend.
 *
 * The generated client goes through `globalThis.fetch`, so stubbing it covers
 * the real request the component makes — the URL, the query string and the
 * body — rather than a mocked module that would let a wrong URL pass.
 */
/** What a recorded call carries in place of an unreadable multipart body. */
export const MULTIPART = '[multipart/form-data]'

function isMultipart(request: Request): boolean {
  return (request.headers.get('content-type') ?? '').startsWith('multipart/form-data')
}

export function stubApi(routes: Route[]): RecordedCall[] {
  const calls: RecordedCall[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      const url = new URL(request.url)
      // The body is read from a clone so the recorded call never consumes the
      // stream the client itself is about to send.
      //
      // An upload is `multipart/form-data`, and only the fact that it *is* one
      // is recorded. Reading it back is not possible here: jsdom installs its
      // own `File` and `Blob`, Node's multipart machinery refuses to serialise
      // or parse them, and the bytes this environment produces are not the
      // ones a browser sends. What goes in the form is asserted directly
      // instead, on `markdownForm` — see `useElementDocuments.spec.ts`.
      const body = request.body
        ? isMultipart(request)
          ? MULTIPART
          : await request.clone().json()
        : undefined
      calls.push({ method: request.method, url, body })

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

/** A relationship as the API returns it, defaulting to a legal serving link. */
export function aRelationship(overrides: Partial<RelationshipRead> = {}): RelationshipRead {
  return {
    id: '99999999-9999-4999-8999-999999999999',
    relationship_type: 'serving',
    source_id: 'aaaaaaaa-1111-4111-8111-111111111111',
    target_id: 'bbbbbbbb-2222-4222-8222-222222222222',
    source_type: 'application_service',
    target_type: 'business_process',
    name: '',
    access_type: null,
    directed: false,
    properties: {},
    created_at: '2026-09-07T12:00:00Z',
    ...overrides,
  }
}

/** The sub-graph shape every traversal and the relations endpoint return. */
export function aGraph(elements: ElementRead[], relationships: RelationshipRead[]) {
  return { elements, relationships }
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
  relationship_types: [
    {
      value: 'serving',
      category: 'dependency',
      strength: 6,
      impact_follows_direction: true,
    },
    {
      value: 'composition',
      category: 'structural',
      strength: 10,
      impact_follows_direction: false,
    },
  ],
  layers: ['business', 'application'],
}

/** One row of the metamodel matrix, as `/metamodel/matrix` returns it. */
export function aMatrix(
  source: string,
  rules: Record<string, string[]>,
): { source: string; rules: { target: string; relationships: string[] }[] } {
  return {
    source,
    rules: Object.entries(rules).map(([target, relationships]) => ({
      target,
      relationships,
    })),
  }
}

/** One attached markdown file, as a listing returns it — no content. */
export function aDocumentSummary(
  overrides: Partial<DocumentSummaryRead> = {},
): DocumentSummaryRead {
  return {
    id: 'dddddddd-1111-4111-8111-111111111111',
    element_id: '11111111-1111-4111-8111-111111111111',
    filename: 'runbook.md',
    byte_size: 42,
    created_at: '2026-09-07T12:00:00Z',
    updated_at: '2026-09-07T12:00:00Z',
    ...overrides,
  }
}

/** The same document with its markdown, as `GET /documents/{id}` returns it. */
export function aDocument(overrides: Partial<DocumentRead> = {}): DocumentRead {
  return { ...aDocumentSummary(), content: '# Runbook\n', ...overrides }
}

/** A picked file, as the browser hands one to a change handler. */
export function aFile(name = 'runbook.md', content = '# Runbook\n'): File {
  return new File([content], name, { type: 'text/markdown' })
}
