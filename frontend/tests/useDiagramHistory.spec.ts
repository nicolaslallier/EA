import { afterEach, describe, expect, it, vi } from 'vitest'

import { SAVE_PAUSE_MS, useDiagram } from '../src/features/diagrams/useDiagram'
import {
  anElement,
  aRelationship,
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
const B = anElement({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Commandes' })

const READ: Route = {
  path: `/diagrams/${DIAGRAM_ID}`,
  body: {
    id: DIAGRAM_ID,
    name: 'Vue',
    description: '',
    created_at: '2026-09-13T12:00:00Z',
    updated_at: '2026-09-13T12:00:00Z',
    nodes: [{ element_id: A.id, x: 10, y: 20, width: 132, height: 46 }],
    elements: [A],
    relationships: [],
  },
}
const SAVE: Route = { method: 'PUT', path: `/diagrams/${DIAGRAM_ID}/layout`, status: 204 }
const LINK: Route = {
  method: 'POST',
  path: '/relationships',
  status: 201,
  body: aRelationship({ source_id: A.id, target_id: B.id }),
}

const saves = (calls: RecordedCall[]) => calls.filter((call) => call.method === 'PUT')
const savedIds = (call: RecordedCall) =>
  (call.body as { nodes: { element_id: string }[] }).nodes.map((node) => node.element_id)
const ids = (editor: ReturnType<typeof useDiagram>) =>
  editor.boxes.value.map((box) => box.element.id)

async function pause(): Promise<void> {
  await vi.advanceTimersByTimeAsync(SAVE_PAUSE_MS)
  await vi.waitFor(() => undefined)
}

async function opened() {
  const calls = stubApi([READ, SAVE, LINK])
  const editor = useDiagram()
  await editor.open(DIAGRAM_ID)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  return { calls, editor }
}

describe('useDiagram — undo and redo', () => {
  it('takes back a placed box, then puts it back', async () => {
    const { editor } = await opened()
    editor.place(B, { x: 300, y: 100 })

    editor.undo()
    expect(ids(editor)).toEqual([A.id])
    expect(editor.canRedo.value).toBe(true)

    editor.redo()
    expect(ids(editor)).toEqual([A.id, B.id])
  })

  it('saves again after an undo that follows a save', async () => {
    const { calls, editor } = await opened()
    editor.place(B, { x: 300, y: 100 })
    await pause()
    expect(saves(calls)).toHaveLength(1)

    editor.undo()
    await pause()

    expect(saves(calls)).toHaveLength(2)
    expect(savedIds(saves(calls)[1])).toEqual([A.id])
  })

  it('records one drag as one entry', async () => {
    const { editor } = await opened()

    editor.move(A.id, { x: 20, y: 30 })
    editor.move(A.id, { x: 40, y: 50 })
    editor.move(A.id, { x: 60, y: 70 })
    editor.endGesture()
    editor.undo()

    expect(editor.boxes.value[0]).toMatchObject({ x: 10, y: 20 })
    expect(editor.canUndo.value).toBe(false)
  })

  it('folds consecutive keyboard nudges of the same box into one entry', async () => {
    const { editor } = await opened()

    editor.move(A.id, { x: 11, y: 20 })
    editor.endGesture('keyboard')
    editor.move(A.id, { x: 12, y: 20 })
    editor.endGesture('keyboard')
    editor.undo()

    expect(editor.boxes.value[0]).toMatchObject({ x: 10 })
  })

  it('takes back a resize', async () => {
    const { editor } = await opened()

    editor.resize(A.id, { width: 300, height: 90 })
    editor.endGesture()
    editor.undo()

    expect(editor.boxes.value[0]).toMatchObject({ width: 132, height: 46 })
  })

  it('does not put a created relationship on the stack: the graph is not a view', async () => {
    const { editor } = await opened()
    editor.place(B, { x: 300, y: 100 })
    editor.undo()
    editor.redo()

    await editor.connect({ relationship_type: 'serving', source_id: A.id, target_id: B.id })
    editor.undo() // takes back the placement of B, never the relationship
    editor.undo()

    expect(editor.relationships.value).toHaveLength(1)
  })

  it('forgets its history when another diagram is opened', async () => {
    const { editor } = await opened()
    editor.place(B, { x: 300, y: 100 })
    expect(editor.canUndo.value).toBe(true)

    await editor.open(DIAGRAM_ID)

    expect(editor.canUndo.value).toBe(false)
  })

  it('takes back several removals at once', async () => {
    const { editor } = await opened()
    editor.place(B, { x: 300, y: 100 })

    editor.removeMany([A.id, B.id])
    expect(ids(editor)).toEqual([])
    editor.undo()

    expect(ids(editor)).toEqual([A.id, B.id])
  })
})

describe('useDiagram — arranging a selection', () => {
  it('aligns boxes on their left edge and takes it back in one step', async () => {
    const { editor } = await opened()
    editor.place(B, { x: 300, y: 100 })

    editor.align([A.id, B.id], 'left')
    expect(editor.boxes.value.map((box) => box.x)).toEqual([10, 10])
    editor.undo()

    expect(editor.boxes.value.map((box) => box.x)).toEqual([10, 300])
  })

  it('records nothing when the arrangement changes nothing', async () => {
    const { editor } = await opened()

    editor.align([A.id], 'left')

    expect(editor.canUndo.value).toBe(false)
  })
})
