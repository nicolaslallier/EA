import { vi } from 'vitest'

import type { components } from '../../src/api/schema'
import { emptyQuery } from '../../src/features/search/queryState'

type ElementRead = components['schemas']['ElementRead']
type RelationshipRead = components['schemas']['RelationshipRead']
type PackageRead = components['schemas']['PackageRead']
type DocumentSummaryRead = components['schemas']['DocumentSummaryRead']
type DocumentRead = components['schemas']['DocumentRead']
type SubnetRead = components['schemas']['SubnetRead']
type SubnetDetailRead = components['schemas']['SubnetDetailRead']
type AddressRead = components['schemas']['AddressRead']
type AddressLocationRead = components['schemas']['AddressLocationRead']
type FileRead = components['schemas']['FileRead']
type FileMetadataRead = components['schemas']['FileMetadataRead']
type FileListingRead = components['schemas']['FileListingRead']
type SavedQueryRead = components['schemas']['SavedQueryRead']
type ImportReportRead = components['schemas']['ImportReportRead']
type SavedQuerySummaryRead = components['schemas']['SavedQuerySummaryRead']

/** A route the stubbed backend answers, matched on method and path. */
export type Route = {
  method?: string
  path: string
  status?: number
  body?: unknown
  /** Extra response headers (`Retry-After`, `Content-Disposition`…). */
  headers?: Record<string, string>
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
        headers: { 'content-type': 'application/json', ...route.headers },
      })
    }),
  )

  return calls
}

/** A request held open by `deferApi` until the test answers it. */
export type HeldCall = RecordedCall & {
  /** Answer this request now, as the backend would. */
  answer: (body: unknown, status?: number) => void
  /** Whether the client gave up on it before it was answered. */
  aborted: () => boolean
}

/**
 * Stand in for a backend whose answers arrive when the test says so.
 *
 * `stubApi` answers every request at once, in the order they were sent, which
 * is exactly the case a race never happens in. Here each request waits, so a
 * spec can answer the second before the first — what a slow traversal next to
 * a fast one does on a real network. A request whose signal is aborted rejects
 * with the reason, as `fetch` does, and nothing answers it afterwards.
 */
export function deferApi(): HeldCall[] {
  const calls: HeldCall[] = []

  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      return new Promise<Response>((resolve, reject) => {
        // `abort()` without an argument always rejects with a DOMException, and
        // a request already aborted never fires the event: `fetch` rejects it
        // at once.
        if (request.signal.aborted) {
          reject(request.signal.reason as Error)
        }
        request.signal.addEventListener('abort', () => reject(request.signal.reason as Error))
        calls.push({
          method: request.method,
          url: new URL(request.url),
          body: undefined,
          aborted: () => request.signal.aborted,
          answer: (body, status = 200) =>
            resolve(
              new Response(JSON.stringify(body), {
                status,
                headers: { 'content-type': 'application/json' },
              }),
            ),
        })
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
    package_id: null,
    created_at: '2026-09-07T12:00:00Z',
    updated_at: '2026-09-07T12:00:00Z',
    approval_state: 'draft',
    locked: false,
    approved_by: null,
    approved_at: null,
    revision: 1,
    allowed_transitions: [],
    ...overrides,
  }
}

/** A package as `GET /packages` returns it — no children, no elements, at the root. */
export function aPackage(overrides: Partial<PackageRead> = {}): PackageRead {
  return {
    id: 'aaaaaaaa-0000-4000-8000-00000000f001',
    parent_id: null,
    name: 'Finance',
    description: '',
    path: 'Finance',
    element_count: 0,
    diagram_count: 0,
    child_count: 0,
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
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

/** A declared subnet, as `/ipam/subnets` returns it — counts, never a percentage. */
export function aSubnet(overrides: Partial<SubnetRead> = {}): SubnetRead {
  return {
    element_id: 'cccccccc-1111-4111-8111-111111111111',
    name: 'DMZ',
    description: '',
    cidr: '10.0.1.0/24',
    vrf: 'default',
    version: 4,
    capacity: 254,
    reserved: 0,
    used: 0,
    free: 254,
    reservations: '',
    ...overrides,
  }
}

/** One assigned address, with the element answering on it. */
export function anAddress(overrides: Partial<AddressRead> = {}): AddressRead {
  return {
    address: '10.0.1.12',
    vrf: 'default',
    version: 4,
    element_id: '11111111-1111-4111-8111-111111111111',
    element_name: 'srv-app-01',
    element_type: 'application_component',
    subnet_id: 'cccccccc-1111-4111-8111-111111111111',
    ...overrides,
  }
}

/** A subnet with its occupants, as `/ipam/subnets/{id}` returns it. */
export function aSubnetDetail(
  subnet: SubnetRead = aSubnet(),
  addresses: AddressRead[] = [],
  nextFree: string | null = '10.0.1.1',
): SubnetDetailRead {
  return { subnet, addresses, next_free: nextFree }
}

/** What an address lookup answers with: the holder, and what it is wired to. */
export function anAddressLocation(
  address: AddressRead = anAddress(),
  elements: ElementRead[] = [],
  relationships: RelationshipRead[] = [],
): AddressLocationRead {
  return { address, graph: { elements, relationships } }
}

/** What the catalogue knows about one file — see docs/adr/0039. */
export function aFileRecord(overrides: Partial<FileMetadataRead> = {}): FileMetadataRead {
  return {
    title: '',
    description: '',
    tags: [],
    uploaded_by: 'nicolas',
    sha256: 'a'.repeat(64),
    created_at: '2026-09-15T12:00:00Z',
    updated_at: '2026-09-15T12:00:00Z',
    ...overrides,
  }
}

/**
 * One file of the bucket, as a listing or an upload describes it.
 *
 * `metadata` defaults to a record, because every file this API stored has one;
 * pass `{ metadata: null }` for a file written straight into the bucket.
 */
export function aStoredFile(overrides: Partial<FileRead> = {}): FileRead {
  return {
    key: 'inbox/notes.md',
    name: 'notes.md',
    size: 2048,
    last_modified: '2026-09-15T12:00:00Z',
    content_type: 'text/markdown',
    metadata: aFileRecord(),
    ...overrides,
  }
}

/** One folder of the bucket, as `GET /files` returns it. */
export function aListing(overrides: Partial<FileListingRead> = {}): FileListingRead {
  return { prefix: '', folders: [], files: [], truncated: false, ...overrides }
}
/** A saved query as `GET /queries` lists it — no filter in it. */
export function aSavedQuerySummary(
  overrides: Partial<SavedQuerySummaryRead> = {},
): SavedQuerySummaryRead {
  return {
    id: 'qqqqqqqq-1111-4111-8111-111111111111',
    name: 'Les noeuds',
    description: '',
    created_by: 'nicolas',
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
    ...overrides,
  }
}

/** The same query opened — with its filter. */
export function aSavedQuery(overrides: Partial<SavedQueryRead> = {}): SavedQueryRead {
  return { ...aSavedQuerySummary(), query: emptyQuery(), ...overrides }
}

type AuditEventRead = components['schemas']['AuditEventRead']

/** One line of the audit trail, as `GET /audit` returns it. */
export function anAuditEvent(overrides: Partial<AuditEventRead> = {}): AuditEventRead {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    at: '2026-10-10T12:00:00Z',
    actor_sub: 'editor-1',
    actor_name: 'editor',
    action: 'element.updated',
    entity_kind: 'element',
    entity_id: '22222222-2222-4222-8222-222222222222',
    before: { name: 'db-01' },
    after: { name: 'db-02' },
    request_id: 'req-1',
    channel: 'http',
    correlation: null,
    changes: [{ field: 'name', before: 'db-01', after: 'db-02' }],
    ...overrides,
  }
}

type BaselineRead = components['schemas']['BaselineRead']
type BaselineDiffRead = components['schemas']['BaselineDiffRead']
type RestoreRead = components['schemas']['RestoreRead']
type RestorePlanRead = components['schemas']['RestorePlanRead']

/** A baseline, as `GET /baselines` returns it. */
export function aBaseline(overrides: Partial<BaselineRead> = {}): BaselineRead {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    name: 'Avant migration',
    description: '',
    created_by: 'editor',
    created_at: '2026-10-10T12:00:00Z',
    element_count: 12,
    relationship_count: 20,
    box_count: 5,
    ...overrides,
  }
}

/** What separates a baseline from the model: nothing, unless a spec says. */
export function aDiff(overrides: Partial<BaselineDiffRead> = {}): BaselineDiffRead {
  return {
    elements_added: [],
    elements_removed: [],
    elements_modified: [],
    relationships_added: [],
    relationships_removed: [],
    relationships_modified: [],
    ...overrides,
  }
}

/** A restoration plan that changes nothing, unless a spec says. */
export function aRestorePlan(overrides: Partial<RestorePlanRead> = {}): RestorePlanRead {
  return {
    elements_to_create: 0,
    elements_to_update: 0,
    elements_to_delete: 0,
    relationships_to_create: 0,
    relationships_to_delete: 0,
    relationships_cascaded: 0,
    boxes_to_restore: 0,
    documents_lost: 0,
    boxes_lost: 0,
    created: [],
    updated: [],
    deleted: [],
    warning_count: 0,
    warnings: [],
    blocked_count: 0,
    blocked: [],
    ...overrides,
  }
}

/** The answer to `POST /baselines/{id}/restore`. */
export function aRestore(
  plan: Partial<RestorePlanRead> = {},
  overrides: Partial<RestoreRead> = {},
): RestoreRead {
  return {
    applied: false,
    digest: 'a'.repeat(64),
    plan: aRestorePlan(plan),
    ...overrides,
  }
}

/** The report of an import: three elements to create, nothing else, unless a spec says. */
export function aReport(overrides: Partial<ImportReportRead> = {}): ImportReportRead {
  const none = { created: 0, reused: 0, rejected: 0, ignored: 0 }
  return {
    dry_run: true,
    applied: false,
    format: 'archimate-xml',
    sha256: 'a.xml:1',
    duration_ms: 12,
    issue_count: 0,
    counts: {
      elements: { ...none, created: 3 },
      relationships: { ...none },
      packages: { ...none },
      views: { ...none },
    },
    issues: [],
    truncated: false,
    ...overrides,
  }
}

/** One line of that report: a refused relationship, unless a spec says. */
export function anIssue(overrides: Partial<ImportReportRead['issues'][number]> = {}) {
  return {
    severity: 'error' as const,
    code: 'relationship_not_allowed',
    message: 'not allowed',
    source_id: 'r-1',
    line: null,
    path: null,
    ...overrides,
  }
}
