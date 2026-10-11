import { afterEach, describe, expect, it, vi } from 'vitest'

import { SAVE_PAUSE_MS, useDiagram } from '../src/features/diagrams/useDiagram'
import { anElement, aRelationship, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const ID = 'dddddddd-0000-4000-8000-000000000000'
const QUERY = 'qqqqqqqq-0000-4000-8000-000000000000'
const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation', layer: 'application' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})
const C = anElement({ id: 'cccccccc-3333-4333-8333-333333333333', name: 'Paiement', layer: 'application' })
const LINK = aRelationship({ source_id: B.id, target_id: A.id })
const SOURCE = { query_id: QUERY, package_id: null, recursive: false }

const base = {
  id: ID,
  name: 'Périmètre',
  description: '',
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
  revision: 0,
  objects: [],
  edges: [],
  source: SOURCE,
}
const node = (element_id: string, x: number, y: number) => ({
  element_id, x, y, width: 132, height: 46, parent_object_id: null, z: 0,
})
const live = (): Route => ({
  path: `/diagrams/${ID}`,
  body: { ...base, generation: 'live', nodes: [], elements: [A, B], relationships: [LINK] },
})
const frozen = (): Route => ({
  path: `/diagrams/${ID}`,
  body: { ...base, generation: 'frozen', revision: 1, nodes: [node(A.id, 24, 24), node(B.id, 300, 24)], elements: [A, B], relationships: [LINK] },
})

async function pause(): Promise<void> {
  await vi.advanceTimersByTimeAsync(SAVE_PAUSE_MS)
  await vi.waitFor(() => undefined)
}

describe('useDiagram — a live view', () => {
  it('lays itself out by layer and saves nothing, ever', async () => {
    const calls = stubApi([live(), { method: 'PUT', path: `/diagrams/${ID}/layout`, status: 204 }])
    const editor = useDiagram()

    await editor.open(ID)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    editor.persist()
    await pause()

    expect(editor.live.value).toBe(true)
    const y = (id: string) => editor.boxes.value.find((box) => box.element.id === id)!.y
    expect(y(B.id)).toBeLessThan(y(A.id)) // business above application
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
  })

  it('freezes with the layout it computed and then behaves as an editable diagram', async () => {
    const calls = stubApi([
      live(),
      { method: 'POST', path: `/diagrams/${ID}/freeze`, body: frozen().body },
    ])
    const editor = useDiagram()
    await editor.open(ID)

    await editor.freeze()

    const sent = calls.find((call) => call.method === 'POST')?.body as {
      revision: number
      nodes: { element_id: string }[]
    }
    expect(sent.revision).toBe(0)
    expect(sent.nodes.map((n) => n.element_id).sort()).toEqual([A.id, B.id])
    expect(editor.generation.value).toBe('frozen')
    expect(editor.live.value).toBe(false)
    expect(editor.revision.value).toBe(1)
  })
})

describe('useDiagram — a frozen view', () => {
  it('shows the gap with its source and writes nothing until it is applied', async () => {
    const calls = stubApi([
      frozen(),
      { path: `/diagrams/${ID}/source-diff`, body: { added: [C], removed: [B.id] } },
    ])
    const editor = useDiagram()
    await editor.open(ID)

    await editor.refresh()

    expect(editor.diff.value?.added.map((e) => e.id)).toEqual([C.id])
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    expect(editor.boxes.value).toHaveLength(2)
  })

  it('places the added below the survivors without moving them, and removes the gone only when asked', async () => {
    const calls = stubApi([
      frozen(),
      { path: `/diagrams/${ID}/source-diff`, body: { added: [C], removed: [B.id] } },
      { method: 'PUT', path: `/diagrams/${ID}/layout`, status: 204 },
    ])
    const editor = useDiagram()
    await editor.open(ID)
    await editor.refresh()

    await editor.applyDiff({ removeGone: false })

    const put = calls.find((call) => call.method === 'PUT')?.body as { nodes: { element_id: string; x: number; y: number }[] }
    const byId = new Map(put.nodes.map((n) => [n.element_id, n]))
    expect(byId.get(A.id)).toMatchObject({ x: 24, y: 24 })
    expect(byId.get(B.id)).toMatchObject({ x: 300, y: 24 })
    expect(byId.get(C.id)!.y).toBeGreaterThan(24 + 46)
    expect(editor.diff.value).toBeNull()
  })

  it('removes the elements that left the source when the user confirmed it', async () => {
    const calls = stubApi([
      frozen(),
      { path: `/diagrams/${ID}/source-diff`, body: { added: [], removed: [B.id] } },
      { method: 'PUT', path: `/diagrams/${ID}/layout`, status: 204 },
    ])
    const editor = useDiagram()
    await editor.open(ID)
    await editor.refresh()

    await editor.applyDiff({ removeGone: true })

    const put = calls.find((call) => call.method === 'PUT')?.body as { nodes: { element_id: string }[] }
    expect(put.nodes.map((n) => n.element_id)).toEqual([A.id])
  })

  it('thaws, surfacing the refusal when notes would be lost', async () => {
    stubApi([
      frozen(),
      { method: 'POST', path: `/diagrams/${ID}/unfreeze`, status: 409, body: { error: 'has_annotations', detail: 'would delete notes' } },
    ])
    const editor = useDiagram()
    await editor.open(ID)

    await expect(editor.unfreeze()).rejects.toMatchObject({ code: 'has_annotations' })
    expect(editor.generation.value).toBe('frozen')
  })

  it('thaws on request and goes back to a live layout', async () => {
    const calls = stubApi([
      frozen(),
      { method: 'POST', path: `/diagrams/${ID}/unfreeze`, body: live().body },
    ])
    const editor = useDiagram()
    await editor.open(ID)

    await editor.unfreeze(true)

    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      revision: 1,
      discard_annotations: true,
    })
    expect(editor.live.value).toBe(true)
  })
})
