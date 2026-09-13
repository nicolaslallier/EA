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
      { element_id: A.id, x: 10, y: 20 },
      { element_id: GONE, x: 400, y: 400 },
    ],
    elements: [A],
    relationships: [],
    ...overrides,
  }
}

const READ: Route = { path: `/diagrams/${DIAGRAM_ID}`, body: aDiagram() }
const SAVE: Route = { method: 'PUT', path: `/diagrams/${DIAGRAM_ID}/layout`, status: 204 }

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
  it('draws the boxes of the diagram, skipping one whose element is gone', async () => {
    const { calls, editor } = await opened()

    expect(calls[0].url.pathname).toBe(`/diagrams/${DIAGRAM_ID}`)
    expect(editor.boxes.value).toEqual([{ element: A, x: 10, y: 20 }])
    expect(editor.status.value).toBe('ready')
  })

  it('saves the whole layout once the edits pause, not on every one', async () => {
    const { calls, editor } = await opened()

    editor.place(B, { x: 300, y: 100 })
    expect(saves(calls)).toHaveLength(0)
    await pause()

    expect(saves(calls)).toHaveLength(1)
    expect(saves(calls)[0].body).toEqual({
      nodes: [
        { element_id: A.id, x: 10, y: 20 },
        { element_id: GONE, x: 400, y: 400 },
        { element_id: B.id, x: 300, y: 100 },
      ],
    })
    expect(editor.placed.value.has(B.id)).toBe(true)
  })

  it('never places the same element twice', async () => {
    const { editor } = await opened()

    editor.place(A, { x: 500, y: 500 })

    expect(editor.boxes.value).toEqual([{ element: A, x: 10, y: 20 }])
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
      nodes: [
        { element_id: A.id, x: 50, y: 60 },
        { element_id: GONE, x: 400, y: 400 },
      ],
    })
  })

  it('takes a box off the diagram and saves the layout without it', async () => {
    const { calls, editor } = await opened()

    editor.remove(A.id)
    await pause()

    expect(editor.boxes.value).toEqual([])
    expect(saves(calls)[0].body).toEqual({ nodes: [{ element_id: GONE, x: 400, y: 400 }] })
  })

  it('keeps a refused save on screen', async () => {
    const { editor } = await opened([
      READ,
      { ...SAVE, status: 422, body: { error: 'unknown_element', detail: 'Élément inconnu.' } },
    ])

    editor.place(B, { x: 0, y: 0 })
    await pause()

    expect(editor.saveError.value).toBe('Élément inconnu.')
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
