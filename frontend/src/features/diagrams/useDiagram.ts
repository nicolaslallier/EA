// One diagram being edited: its boxes, the links between them, and the saving.
//
// A diagram is a view (docs/adr/0031): it owns no fact, only which elements are
// shown and where. So the boxes are edited here, locally and at once, and the
// whole layout is PUT once the edits pause — no Save button. A link drawn
// between two boxes is a real relationship, created through `/relationships`.
import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'
import { debounce } from '../../lib/debounce'
import {
  DEFAULT_BOX,
  alignBoxes,
  distributeBoxes,
  type AlignMode,
  type DistributeAxis,
  type Moved,
  type Placed,
  type Point,
  type Size,
} from '../../lib/diagramGeometry'
import { createHistory } from '../../lib/diagramHistory'
import { useLatestRequest } from '../../lib/latest'

export type DiagramRead = components['schemas']['DiagramRead']
export type DiagramNode = components['schemas']['DiagramNode']
export type ElementRead = components['schemas']['ElementRead']
export type RelationshipRead = components['schemas']['RelationshipRead']
export type RelationshipCreate = components['schemas']['RelationshipCreate']

/** A box on the canvas: an element, its top-left and its size in canvas units. */
export type Box = { element: ElementRead; x: number; y: number; width: number; height: number }

/** The `dataTransfer` type a palette row carries: the element's id, nothing else. */
export const ELEMENT_DRAG_TYPE = 'application/x-ea-element'

/** How long the layout waits for the edits to stop before it is saved. */
export const SAVE_PAUSE_MS = 400

/** The largest page `/elements` serves — the API's own `Limit` bound. */
export const PALETTE_LIMIT = 200

export function useDiagram() {
  const diagram = ref<DiagramRead | null>(null)
  const nodes = ref<DiagramNode[]>([])
  const elements = ref<ElementRead[]>([])
  const relationships = ref<RelationshipRead[]>([])
  /** Boxes whose element is out of period on the day asked: greyed, never removed (docs/adr/0060). */
  const inactiveIds = ref<string[]>([])
  const read = useLatestRequest()
  const { status, error } = read
  const saving = ref(false)
  const saveError = ref('')

  // Undo and redo (docs/adr: historique côté SPA). Memory only, per editing
  // session: `open` and `close` empty it. A command restores the *boxes* and
  // asks for a save; the graph (a relationship created from a link) is not
  // part of it — taking one back is `DELETE /relationships`, offered by hand.
  const canUndo = ref(false)
  const canRedo = ref(false)
  const history = createHistory({
    onChange: () => {
      canUndo.value = history.canUndo()
      canRedo.value = history.canRedo()
    },
  })
  const copy = (list: DiagramNode[]): DiagramNode[] => list.map((node) => ({ ...node }))
  /** The boxes as they were when the gesture in progress began. */
  let gestureBefore: DiagramNode[] | null = null

  function record(label: string, before: DiagramNode[], mergeKey?: string): void {
    const after = copy(nodes.value)
    const restoring = (scene: DiagramNode[]) => () => {
      nodes.value = copy(scene)
      persist()
    }
    history.record({
      label,
      undo: restoring(before),
      do: restoring(after),
      ...(mergeKey ? { mergeKey } : {}),
    })
  }

  const byId = computed(() => new Map(elements.value.map((element) => [element.id, element])))
  const placed = computed(() => new Set(nodes.value.map((node) => node.element_id)))

  /** A node whose element no longer exists is kept in the layout, and not drawn. */
  const boxes = computed<Box[]>(() =>
    nodes.value.flatMap((node) => {
      const element = byId.value.get(node.element_id)
      return element ? [{ element, x: node.x, y: node.y, width: node.width, height: node.height }] : []
    }),
  )

  // Saves are chained, never concurrent: two PUTs racing on the network could
  // land in the wrong order and store the older layout. Each one snapshots the
  // layout when it is *asked*, so a diagram switched in between is not saved
  // with the next diagram's boxes.
  let queue: Promise<void> = Promise.resolve()
  let pending = false

  function saveNow(): Promise<void> {
    const current = diagram.value
    if (!current || !pending) {
      return queue
    }
    pending = false
    const body = { nodes: nodes.value.map((node) => ({ ...node })) }
    queue = queue.then(async () => {
      saving.value = true
      try {
        unwrap(
          await api.PUT('/diagrams/{diagram_id}/layout', {
            params: { path: { diagram_id: current.id } },
            body,
          }),
        )
        saveError.value = ''
      } catch (caught) {
        saveError.value = messageOf(caught)
        // Still unsaved: the next edit, close, switch or unmount sends it again.
        // Nothing retries on its own, so a server that keeps refusing is not
        // hammered in a loop.
        pending = true
      } finally {
        saving.value = false
      }
    })
    return queue
  }

  const autosave = debounce(saveNow, SAVE_PAUSE_MS)

  /** Save the layout once the edits pause. */
  function persist(): void {
    pending = true
    autosave.call()
  }

  /** Save now what is waiting for its pause, so switching away loses nothing. */
  function flush(): Promise<void> {
    autosave.cancel()
    return saveNow()
  }

  async function open(id: string, asOf?: string): Promise<void> {
    await flush()
    await read.run(
      async (signal) =>
        unwrap(
          await api.GET('/diagrams/{diagram_id}', {
            params: { path: { diagram_id: id }, query: asOf ? { as_of: asOf } : {} },
            signal,
          }),
        ),
      (answer) => {
        // The previous diagram stayed on screen, and editable, while this one
        // loaded: whatever was changed there goes to *that* diagram before its
        // boxes are replaced. `saveNow` snapshots synchronously, so it does.
        void flush()
        diagram.value = answer
        nodes.value = answer.nodes
        elements.value = answer.elements
        relationships.value = answer.relationships
        inactiveIds.value = answer.inactive_element_ids ?? []
        gestureBefore = null
        history.clear()
      },
      () => {
        diagram.value = null
        nodes.value = []
      },
    )
  }

  function close(): void {
    void flush()
    read.cancel()
    diagram.value = null
    nodes.value = []
    elements.value = []
    relationships.value = []
    inactiveIds.value = []
    gestureBefore = null
    history.clear()
  }

  function place(element: ElementRead, at: Point): void {
    if (placed.value.has(element.id)) {
      return
    }
    const before = copy(nodes.value)
    nodes.value.push({ element_id: element.id, x: at.x, y: at.y, ...DEFAULT_BOX })
    if (!byId.value.has(element.id)) {
      elements.value.push(element)
    }
    record('Ajouter un élément', before)
    persist()
  }

  /** Move a box on screen only; `endGesture` once the gesture is over. */
  function move(elementId: string, to: Point): void {
    const node = nodes.value.find((candidate) => candidate.element_id === elementId)
    if (node) {
      gestureBefore ??= copy(nodes.value)
      node.x = to.x
      node.y = to.y
    }
  }

  /** Resize a box on screen only; `endGesture` once the gesture is over. */
  function resize(elementId: string, size: Size): void {
    const node = nodes.value.find((candidate) => candidate.element_id === elementId)
    if (node) {
      gestureBefore ??= copy(nodes.value)
      node.width = size.width
      node.height = size.height
    }
  }

  /**
   * The gesture that `move` and `resize` were part of is over: one history
   * entry for all of it, then the save. Arrow-key nudges of the same boxes
   * fold into one entry (`source: 'keyboard'`).
   */
  function endGesture(source?: 'keyboard'): void {
    const before = gestureBefore
    gestureBefore = null
    if (before) {
      const changed = nodes.value.filter((node) => {
        const old = before.find((candidate) => candidate.element_id === node.element_id)
        return (
          !old ||
          old.x !== node.x ||
          old.y !== node.y ||
          old.width !== node.width ||
          old.height !== node.height
        )
      })
      if (changed.length > 0) {
        const key =
          source === 'keyboard'
            ? `keyboard:${changed.map((node) => node.element_id).sort().join(',')}`
            : undefined
        record('Déplacer ou redimensionner', before, key)
      }
    }
    persist()
  }

  /** Take boxes off the diagram. The elements themselves are never deleted. */
  function removeMany(elementIds: string[]): void {
    const before = copy(nodes.value)
    nodes.value = nodes.value.filter((node) => !elementIds.includes(node.element_id))
    record('Retirer du diagramme', before)
    persist()
  }

  function remove(elementId: string): void {
    removeMany([elementId])
  }

  /** Apply the new top-lefts an arrangement computed, as one undoable step. */
  function arrange(label: string, elementIds: string[], compute: (boxes: Placed[]) => Moved[]): void {
    const chosen: Placed[] = nodes.value
      .filter((node) => elementIds.includes(node.element_id))
      .map((node) => ({
        id: node.element_id,
        x: node.x,
        y: node.y,
        width: node.width,
        height: node.height,
      }))
    const before = copy(nodes.value)
    let changed = false
    for (const target of compute(chosen)) {
      const node = nodes.value.find((candidate) => candidate.element_id === target.id)
      if (node && (node.x !== target.x || node.y !== target.y)) {
        node.x = target.x
        node.y = target.y
        changed = true
      }
    }
    if (changed) {
      record(label, before)
      persist()
    }
  }

  function align(elementIds: string[], mode: AlignMode): void {
    arrange('Aligner', elementIds, (boxes) => alignBoxes(boxes, mode))
  }

  function distribute(elementIds: string[], axis: DistributeAxis): void {
    arrange('Répartir', elementIds, (boxes) => distributeBoxes(boxes, axis))
  }

  function undo(): void {
    history.undo()
  }

  function redo(): void {
    history.redo()
  }

  /**
   * Create a relationship and draw it.
   *
   * The created link is added from the answer rather than by reading the
   * diagram again: a re-read would put back the stored positions over a box
   * moved while it was in flight. A refusal is thrown, for the form to show.
   */
  async function connect(payload: RelationshipCreate): Promise<void> {
    const created = unwrap(await api.POST('/relationships', { body: payload }))
    relationships.value.push(created)
  }

  if (getCurrentScope()) {
    onScopeDispose(() => void flush())
  }

  return {
    diagram,
    boxes,
    placed,
    relationships,
    inactiveIds,
    status,
    error,
    saving,
    saveError,
    byId,
    open,
    close,
    place,
    move,
    resize,
    persist,
    remove,
    removeMany,
    endGesture,
    align,
    distribute,
    undo,
    redo,
    canUndo,
    canRedo,
    connect,
  }
}

/** Every element the palette offers, narrowed server-side by a search. */
export function useElementPalette() {
  const items = ref<ElementRead[]>([])
  const total = ref(0)
  const listing = useLatestRequest()
  const { status, error } = listing

  async function search(term: string): Promise<void> {
    await listing.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements', {
            params: { query: { limit: PALETTE_LIMIT, ...(term ? { search: term } : {}) } },
            signal,
          }),
        ),
      (page) => {
        items.value = page.items
        total.value = page.total
      },
      () => {
        items.value = []
        total.value = 0
      },
    )
  }

  function find(id: string): ElementRead | undefined {
    return items.value.find((element) => element.id === id)
  }

  return { items, total, status, error, search, find }
}
