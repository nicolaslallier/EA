import { afterEach, describe, expect, it, vi } from 'vitest'

import { SAVE_PAUSE_MS, useDiagram } from '../src/features/diagrams/useDiagram'
import { anElement, aRelationship, stubApi, type RecordedCall, type Route } from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const DIAGRAM_ID = 'dddddddd-0000-4000-8000-000000000000'
const LINK_ID = 'rrrrrrrr-0000-4000-8000-000000000000'
const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Commandes' })
const LINK = aRelationship({ id: LINK_ID, source_id: A.id, target_id: B.id })

const node = (element_id: string, x: number, y: number) => ({
  element_id,
  x,
  y,
  width: 132,
  height: 46,
  parent_object_id: null,
  z: 0,
})

function aDiagram(overrides: Record<string, unknown> = {}) {
  return {
    id: DIAGRAM_ID,
    name: 'Vue',
    description: '',
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
    revision: 0,
    nodes: [node(A.id, 10, 20), node(B.id, 400, 20)],
    objects: [],
    edges: [],
    elements: [A, B],
    relationships: [LINK],
    ...overrides,
  }
}

const READ = (overrides: Record<string, unknown> = {}): Route => ({
  path: `/diagrams/${DIAGRAM_ID}`,
  body: aDiagram(overrides),
})
const SAVE: Route = {
  method: 'PUT',
  path: `/diagrams/${DIAGRAM_ID}/layout`,
  status: 204,
  headers: { 'X-Diagram-Revision': '1' },
}
const CONFLICT: Route = {
  method: 'PUT',
  path: `/diagrams/${DIAGRAM_ID}/layout`,
  status: 409,
  body: { error: 'diagram_revision_conflict', detail: 'the diagram is at revision 5, not 0' },
}

const saves = (calls: RecordedCall[]) => calls.filter((call) => call.method === 'PUT')
type Saved = {
  revision: number
  nodes: { element_id: string; parent_object_id: string | null; x: number }[]
  objects: { id: string; kind: string; parent_id: string | null; x: number; text: string; anchor_element_id: string | null }[]
  edges: { relationship_id: string; waypoints: { x: number; y: number }[] }[]
}
const body = (call: RecordedCall) => call.body as Saved

async function pause(): Promise<void> {
  await vi.advanceTimersByTimeAsync(SAVE_PAUSE_MS)
  await vi.waitFor(() => undefined)
}

async function opened(routes: Route[] = [READ(), SAVE]) {
  const calls = stubApi(routes)
  const editor = useDiagram()
  await editor.open(DIAGRAM_ID)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  return { calls, editor }
}

describe('useDiagram — notes and groups', () => {
  it('adds a group and a note and saves them with the revision it read', async () => {
    const { calls, editor } = await opened()

    const group = editor.addObject('group', { x: 0, y: 100 })
    const note = editor.addObject('note', { x: 50, y: 300 })
    await pause()

    const saved = body(saves(calls)[0])
    expect(saved.revision).toBe(0)
    expect(saved.objects.map((o) => o.id).sort()).toEqual([group, note].sort())
    expect(saved.objects.find((o) => o.id === note)?.kind).toBe('note')
  })

  it('drops a box into a group, and out of it again, at the end of the gesture', async () => {
    const { editor } = await opened()
    const group = editor.addObject('group', { x: 0, y: 100 }) // 320 x 200 at (0, 100)

    editor.move(A.id, { x: 50, y: 150 })
    editor.endGesture()
    expect(editor.nodes.value.find((n) => n.element_id === A.id)?.parent_object_id).toBe(group)

    editor.move(A.id, { x: 900, y: 900 })
    editor.endGesture()
    expect(editor.nodes.value.find((n) => n.element_id === A.id)?.parent_object_id).toBeNull()
  })

  it('moves a group with everything inside it', async () => {
    const { editor } = await opened()
    const group = editor.addObject('group', { x: 0, y: 100 })
    editor.move(A.id, { x: 50, y: 150 })
    editor.endGesture()

    editor.moveObject(group, { x: 100, y: 200 })

    expect(editor.nodes.value.find((n) => n.element_id === A.id)).toMatchObject({ x: 150, y: 250 })
  })

  it('removes a group by lifting its children, and takes it back', async () => {
    const { editor } = await opened()
    const outer = editor.addObject('group', { x: 0, y: 100 })
    const inner = editor.addObject('group', { x: 20, y: 120 })
    editor.updateObject(inner, { text: 'Interne' })
    // inner sits in outer, A in inner (each adopted where it is dropped)
    editor.moveObject(inner, { x: 20, y: 120 })
    editor.endGesture()
    editor.move(A.id, { x: 40, y: 140 })
    editor.endGesture()
    const parentOfA = () => editor.nodes.value.find((n) => n.element_id === A.id)?.parent_object_id
    expect(parentOfA()).toBe(inner)

    editor.removeObject(inner)

    expect(editor.objects.value.map((o) => o.id)).toEqual([outer])
    expect(parentOfA()).toBe(outer)
    editor.undo()
    expect(editor.objects.value.map((o) => o.id)).toContain(inner)
    expect(parentOfA()).toBe(inner)
  })

  it('removes an empty group and a note without touching a box', async () => {
    const { editor } = await opened()
    const empty = editor.addObject('group', { x: 600, y: 600 })
    const note = editor.addObject('note', { x: 700, y: 700 })

    editor.removeObject(empty)
    editor.removeObject(note)

    expect(editor.objects.value).toEqual([])
    expect(editor.boxes.value).toHaveLength(2)
  })

  it('folds the typing of a note into one undo step', async () => {
    const { editor } = await opened()
    const note = editor.addObject('note', { x: 0, y: 0 })

    editor.updateObject(note, { text: 'A' })
    editor.updateObject(note, { text: 'AB' })
    editor.updateObject(note, { text: 'ABC' })
    editor.undo()

    expect(editor.objects.value[0].text).toBe('')
  })
})

describe('useDiagram — routed links', () => {
  it('adds a bend and drags it as one gesture, then straightens the link', async () => {
    const { calls, editor } = await opened()

    editor.addWaypoint(LINK_ID, 0, { x: 250, y: 150 })
    editor.moveWaypoint(LINK_ID, 0, { x: 260, y: 160 })
    editor.endGesture()
    await pause()
    expect(body(saves(calls)[0]).edges).toEqual([
      { relationship_id: LINK_ID, waypoints: [{ x: 260, y: 160 }] },
    ])

    editor.undo()
    expect(editor.edges.value).toEqual([])
    editor.redo()
    editor.straighten(LINK_ID)
    expect(editor.edges.value).toEqual([])
  })

  it('removes one bend and keeps the others', async () => {
    const { editor } = await opened([
      READ({ edges: [{ relationship_id: LINK_ID, waypoints: [{ x: 1, y: 1 }, { x: 2, y: 2 }] }] }),
      SAVE,
    ])

    editor.removeWaypoint(LINK_ID, 0)

    expect(editor.edges.value[0].waypoints).toEqual([{ x: 2, y: 2 }])
  })

  it('drops a route and a note anchor when the box they hang on leaves the diagram', async () => {
    const { calls, editor } = await opened([
      READ({
        edges: [{ relationship_id: LINK_ID, waypoints: [{ x: 250, y: 150 }] }],
        objects: [
          {
            id: 'nnnnnnnn-0000-4000-8000-000000000000',
            kind: 'note',
            x: 0,
            y: 0,
            width: 100,
            height: 50,
            text: 'à voir',
            colour: null,
            z: 0,
            parent_id: null,
            anchor_element_id: B.id,
          },
        ],
      }),
      SAVE,
    ])

    editor.removeMany([B.id])
    await pause()

    const saved = body(saves(calls)[0])
    expect(saved.edges).toEqual([])
    expect(saved.objects[0].anchor_element_id).toBeNull()
    editor.undo()
    expect(editor.edges.value).toHaveLength(1)
  })
})

describe('useDiagram — undo after a save', () => {
  it('restores the groups too, and saves the restored scene', async () => {
    const { calls, editor } = await opened()
    editor.addObject('group', { x: 0, y: 100 })
    await pause()
    expect(saves(calls)).toHaveLength(1)

    editor.undo()
    await pause()

    expect(saves(calls)).toHaveLength(2)
    expect(body(saves(calls)[1]).objects).toEqual([])
  })
})

describe('useDiagram — a revision conflict', () => {
  it('says so, keeps the local work, and never retries by itself', async () => {
    const { calls, editor } = await opened([READ(), CONFLICT])

    editor.addObject('note', { x: 0, y: 0 })
    await pause()
    await pause()

    expect(editor.conflict.value).toBe(true)
    expect(editor.saveError.value).toContain('revision 5')
    expect(saves(calls)).toHaveLength(1)
    expect(editor.objects.value).toHaveLength(1)
  })

  it('reloads the colleague\'s version, then pastes mine back on the new revision', async () => {
    stubApi([READ(), CONFLICT])
    const editor = useDiagram()
    await editor.open(DIAGRAM_ID)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    editor.addObject('note', { x: 0, y: 0 })
    await pause()
    expect(editor.conflict.value).toBe(true)

    // Now the server answers with the colleague's diagram, at revision 5.
    stubApi([READ({ revision: 5, nodes: [node(A.id, 99, 99)], elements: [A], relationships: [] }), SAVE])
    await editor.reloadAfterConflict()
    expect(editor.reloaded.value).toBe(true)
    expect(editor.revision.value).toBe(5)
    expect(editor.boxes.value).toHaveLength(1)
    expect(editor.objects.value).toEqual([])
    expect(editor.canUndo.value).toBe(false) // a rebuilt history cannot undo across a conflict

    editor.pasteMine()
    await pause()

    expect(editor.conflict.value).toBe(false)
    expect(editor.reloaded.value).toBe(false)
    expect(editor.objects.value).toHaveLength(1)
    expect(editor.boxes.value).toHaveLength(2) // my scene, with the elements it needs
  })

  it('lets the user give up their changes instead', async () => {
    const { editor } = await opened([READ(), CONFLICT])
    editor.addObject('note', { x: 0, y: 0 })
    await pause()
    stubApi([READ({ revision: 5 }), SAVE])

    await editor.reloadAfterConflict()
    editor.discardMine()

    expect(editor.conflict.value).toBe(false)
    expect(editor.objects.value).toEqual([])
  })
})
