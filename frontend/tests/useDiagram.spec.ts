import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  PALETTE_LIMIT,
  SAVE_PAUSE_MS,
  useDiagram,
  useElementPalette,
} from '../src/features/diagrams/useDiagram'
import {
  anElement,
  aPage,
  aRelationship,
  deferApi,
  stubApi,
  type RecordedCall,
  type Route,
} from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const DIAGRAM_ID = 'dddddddd-0000-4000-8000-000000000000'
const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})
const GONE = 'eeeeeeee-9999-4999-8999-999999999999'

function aDiagram(overrides: Record<string, unknown> = {}) {
  return {
    id: DIAGRAM_ID,
    name: 'Vue applicative',
    description: '',
    created_at: '2026-09-13T12:00:00Z',
    updated_at: '2026-09-13T12:00:00Z',
    nodes: [
      { element_id: A.id, x: 10, y: 20, width: 132, height: 46, parent_object_id: null, z: 0 },
      { element_id: GONE, x: 400, y: 400, width: 132, height: 46, parent_object_id: null, z: 0 },
    ],
    elements: [A],
    relationships: [],
    revision: 0,
    objects: [],
    edges: [],
    ...overrides,
  }
}

const READ: Route = { path: `/diagrams/${DIAGRAM_ID}`, body: aDiagram() }
const SAVE: Route = { method: 'PUT', path: `/diagrams/${DIAGRAM_ID}/layout`, status: 204,
  headers: { 'X-Diagram-Revision': '1' },
}

const saves = (calls: RecordedCall[]) => calls.filter((call) => call.method === 'PUT')

/** Let the autosave pause elapse, then the request it starts settle. */
async function pause(): Promise<void> {
  await vi.advanceTimersByTimeAsync(SAVE_PAUSE_MS)
  await vi.waitFor(() => undefined)
}

async function opened(routes: Route[] = [READ, SAVE]) {
  const calls = stubApi(routes)
  const editor = useDiagram()
  await editor.open(DIAGRAM_ID)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  return { calls, editor }
}

describe('useDiagram', () => {
  it('asks the diagram on a day, keeps every box and flags the ones out of period', async () => {
    const calls = stubApi([
      {
        path: `/diagrams/${DIAGRAM_ID}`,
        body: aDiagram({
          nodes: [
            { element_id: A.id, x: 0, y: 0, width: 132, height: 46, parent_object_id: null, z: 0 },
            { element_id: B.id, x: 200, y: 0, width: 132, height: 46, parent_object_id: null, z: 0 },
          ],
          elements: [A, B],
          inactive_element_ids: [B.id],
          inactive_relationship_ids: [],
        }),
      },
    ])
    const editor = useDiagram()

    await editor.open(DIAGRAM_ID, '2028-01-01')

    expect(calls.at(-1)?.url.searchParams.get('as_of')).toBe('2028-01-01')
    expect(editor.boxes.value).toHaveLength(2)
    expect(editor.inactiveIds.value).toEqual([B.id])

    await editor.open(DIAGRAM_ID)
    expect(calls.at(-1)?.url.searchParams.has('as_of')).toBe(false)
  })

  it('draws the boxes of the diagram, skipping one whose element is gone', async () => {
    const { calls, editor } = await opened()

    expect(calls[0].url.pathname).toBe(`/diagrams/${DIAGRAM_ID}`)
    expect(editor.boxes.value).toEqual([{ element: A, x: 10, y: 20, width: 132, height: 46 }])
    expect(editor.status.value).toBe('ready')
  })

  it('saves the whole layout once the edits pause, not on every one', async () => {
    const { calls, editor } = await opened()

    editor.place(B, { x: 300, y: 100 })
    expect(saves(calls)).toHaveLength(0)
    await pause()

    expect(saves(calls)).toHaveLength(1)
    expect(saves(calls)[0].body).toEqual({
      revision: 0,
      objects: [],
      edges: [],
      nodes: [
        { element_id: A.id, x: 10, y: 20, width: 132, height: 46, parent_object_id: null, z: 0 },
        { element_id: GONE, x: 400, y: 400, width: 132, height: 46, parent_object_id: null, z: 0 },
        { element_id: B.id, x: 300, y: 100, width: 132, height: 46, parent_object_id: null, z: 0 },
      ],
    })
    expect(editor.placed.value.has(B.id)).toBe(true)
  })

  it('never places the same element twice', async () => {
    const { editor } = await opened()

    editor.place(A, { x: 500, y: 500 })

    expect(editor.boxes.value).toEqual([{ element: A, x: 10, y: 20, width: 132, height: 46 }])
  })

  it('moves a box on screen at once, and saves only when the move is over', async () => {
    const { calls, editor } = await opened()

    editor.move(A.id, { x: 50, y: 60 })
    expect(editor.boxes.value[0]).toMatchObject({ x: 50, y: 60 })
    await pause()
    expect(saves(calls)).toHaveLength(0)

    editor.persist()
    await pause()

    expect(saves(calls)[0].body).toEqual({
      revision: 0,
      objects: [],
      edges: [],
      nodes: [
        { element_id: A.id, x: 50, y: 60, width: 132, height: 46, parent_object_id: null, z: 0 },
        { element_id: GONE, x: 400, y: 400, width: 132, height: 46, parent_object_id: null, z: 0 },
      ],
    })
  })

  it('resizes a box on screen at once, and saves the size only when the gesture is over', async () => {
    const { calls, editor } = await opened()

    editor.resize(A.id, { width: 200, height: 80 })
    expect(editor.boxes.value[0]).toMatchObject({ x: 10, y: 20, width: 200, height: 80 })
    await pause()
    expect(saves(calls)).toHaveLength(0)

    editor.persist()
    await pause()

    expect(saves(calls)[0].body).toEqual({
      revision: 0,
      objects: [],
      edges: [],
      nodes: [
        { element_id: A.id, x: 10, y: 20, width: 200, height: 80, parent_object_id: null, z: 0 },
        { element_id: GONE, x: 400, y: 400, width: 132, height: 46, parent_object_id: null, z: 0 },
      ],
    })
  })

  it('takes a box off the diagram and saves the layout without it', async () => {
    const { calls, editor } = await opened()

    editor.remove(A.id)
    await pause()

    expect(editor.boxes.value).toEqual([])
    expect(saves(calls)[0].body).toEqual({
      revision: 0,
      objects: [],
      edges: [],
      nodes: [{ element_id: GONE, x: 400, y: 400, width: 132, height: 46, parent_object_id: null, z: 0 }],
    })
  })

  it('sends the revision it read, and keeps the one the server answers', async () => {
    const { calls, editor } = await opened()

    editor.place(B, { x: 300, y: 100 })
    await pause()
    editor.place(anElement({ id: 'cccccccc-3333-4333-8333-333333333333', name: 'C' }), {
      x: 1,
      y: 1,
    })
    await pause()

    expect(saves(calls).map((call) => (call.body as { revision: number }).revision)).toEqual([
      0, 1,
    ])
    expect(editor.revision.value).toBe(1)
  })

  it('falls back to counting when the answer carries no revision header', async () => {
    const { editor } = await opened([READ, { ...SAVE, headers: {} }])

    editor.place(B, { x: 300, y: 100 })
    await pause()

    expect(editor.revision.value).toBe(1)
  })

  it('never presents a revision read after the edit: a reload that lands mid-edit does not lend its token', async () => {
    let release: (response: Response) => void = () => undefined
    const puts: { revision: number }[] = []
    let gets = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(input, init)
        if (request.method === 'PUT') {
          puts.push((await request.clone().json()) as { revision: number })
          return new Response(null, { status: 409, headers: {} })
        }
        gets += 1
        if (gets === 1) {
          return new Response(JSON.stringify(aDiagram()), {
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Promise<Response>((resolve) => {
          release = resolve
        })
      }),
    )
    const editor = useDiagram()
    await editor.open(DIAGRAM_ID)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

    // A second read is in flight (an `as_of` change); the user edits meanwhile, on revision 0.
    const reading = editor.open(DIAGRAM_ID, '2028-01-01')
    await vi.waitFor(() => expect(gets).toBe(2))
    editor.place(B, { x: 300, y: 100 })
    // A colleague saved in the meantime: the answer is revision 5.
    release(
      new Response(JSON.stringify(aDiagram({ revision: 5 })), {
        headers: { 'content-type': 'application/json' },
      }),
    )
    await reading
    await vi.waitFor(() => expect(puts).toHaveLength(1))

    // The edit was made on revision 0 and must say so: the server then answers 409.
    expect(puts[0].revision).toBe(0)
  })

  it('keeps a refused save on screen', async () => {
    const { editor } = await opened([
      READ,
      { ...SAVE, status: 422, body: { error: 'unknown_element', detail: 'Élément inconnu.' } },
    ])

    editor.place(B, { x: 0, y: 0 })
    await pause()

    expect(editor.saveError.value).toContain('Élément inconnu.')
    expect(editor.saveError.value).toContain('ne sont plus enregistrées')
  })

  it('sends a layout whose save failed again when the diagram is closed', async () => {
    const { calls, editor } = await opened([
      READ,
      { ...SAVE, status: 503, body: { error: 'internal_error', detail: 'Indisponible.' } },
    ])

    editor.place(B, { x: 0, y: 0 })
    await pause()
    expect(saves(calls)).toHaveLength(1)

    editor.close()

    await vi.waitFor(() => expect(saves(calls)).toHaveLength(2))
  })

  it('saves what is pending when the diagram is closed, rather than dropping it', async () => {
    const { calls, editor } = await opened()

    editor.place(B, { x: 0, y: 0 })
    editor.close()
    await vi.waitFor(() => expect(saves(calls)).toHaveLength(1))
  })

  it('saves an edit made while the next diagram loads onto the diagram it was made on', async () => {
    const OTHER_ID = 'ffffffff-0000-4000-8000-000000000000'
    const calls = deferApi()
    const editor = useDiagram()
    const first = editor.open(DIAGRAM_ID)
    // `open` saves what is pending before it asks, so the read is not sent synchronously.
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0].answer(aDiagram())
    await first
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

    const second = editor.open(OTHER_ID)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    editor.move(A.id, { x: 50, y: 60 })
    editor.persist()
    calls[1].answer(aDiagram({ id: OTHER_ID, nodes: [], elements: [] }))
    await second
    await pause()

    const layouts = calls.filter((call) => call.method === 'PUT').map((call) => call.url.pathname)
    expect(layouts).toEqual([`/diagrams/${DIAGRAM_ID}/layout`])
  })

  it('draws a link it created without reading the diagram again', async () => {
    const link = aRelationship({ source_id: A.id, target_id: B.id })
    const { calls, editor } = await opened([
      READ,
      SAVE,
      { method: 'POST', path: '/relationships', status: 201, body: link },
    ])

    await editor.connect({ relationship_type: 'serving', source_id: A.id, target_id: B.id })

    expect(calls.at(-1)?.body).toEqual({
      relationship_type: 'serving',
      source_id: A.id,
      target_id: B.id,
    })
    expect(editor.relationships.value).toEqual([link])
  })

  it('reports a diagram that cannot be read', async () => {
    stubApi([{ ...READ, status: 404, body: { error: 'not_found', detail: 'Diagramme introuvable.' } }])
    const editor = useDiagram()

    await editor.open(DIAGRAM_ID)

    expect(editor.error.value).toBe('Diagramme introuvable.')
  })
})

describe('useElementPalette', () => {
  it('asks for as many elements as a page holds, narrowed by the search', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([A, B]) }])
    const palette = useElementPalette()

    await palette.search('fact')

    expect(calls[0].url.searchParams.get('limit')).toBe(String(PALETTE_LIMIT))
    expect(calls[0].url.searchParams.get('search')).toBe('fact')
    expect(palette.find(B.id)).toEqual(B)
    expect(palette.find(GONE)).toBeUndefined()
  })
})
