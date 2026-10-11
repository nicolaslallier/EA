// One diagram being edited: its boxes, the links between them, and the saving.
//
// A diagram is a view (docs/adr/0031): it owns no fact, only which elements are
// shown and where. So the boxes are edited here, locally and at once, and the
// whole layout is PUT once the edits pause — no Save button. A link drawn
// between two boxes is a real relationship, created through `/relationships`.
import { computed, getCurrentScope, onScopeDispose, ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, messageOf, unwrap } from '../../lib/api'
import { debounce } from '../../lib/debounce'
import {
  DEFAULT_BOX,
  centreOf,
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
import { newId } from '../../lib/newId'
import { groupAt, moveWithChildren, removeGroup } from '../../lib/sceneObjects'
import {
  insertWaypoint,
  moveWaypoint as movedWaypoint,
  removeWaypoint as removedWaypoint,
} from '../../lib/waypoints'

export type DiagramRead = components['schemas']['DiagramRead']
export type DiagramNode = components['schemas']['DiagramNode']
export type DiagramObject = components['schemas']['DiagramObject']
export type DiagramEdge = components['schemas']['DiagramEdge']
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

type Scene = { nodes: DiagramNode[]; objects: DiagramObject[]; edges: DiagramEdge[] }

export function useDiagram() {
  const diagram = ref<DiagramRead | null>(null)
  const nodes = ref<DiagramNode[]>([])
  const elements = ref<ElementRead[]>([])
  const relationships = ref<RelationshipRead[]>([])
  const objects = ref<DiagramObject[]>([])
  const edges = ref<DiagramEdge[]>([])
  /** The revision of the scene as last read or saved: the token the next save presents. */
  const revision = ref(0)
  /** The same, per diagram: a save asked for one diagram must not use another's. */
  const revisions = new Map<string, number>()
  /** What the server answered to our own saves: the only revision a queued save may inherit. */
  const savedRevisions = new Map<string, number>()
  /** Boxes whose element is out of period on the day asked: greyed, never removed (docs/adr/0060). */
  const inactiveIds = ref<string[]>([])
  const read = useLatestRequest()
  const { status, error } = read
  const saving = ref(false)
  const saveError = ref('')
  /** A save was refused because somebody saved the diagram first (a 409): nothing is retried. */
  const conflict = ref(false)
  /** The colleague's version is loaded; mine is kept aside until it is pasted back or given up. */
  const reloaded = ref(false)
  let mine: (Scene & { elements: ElementRead[]; relationships: RelationshipRead[] }) | null = null

  // Undo and redo (docs/adr/0062). Memory only, per editing
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
  /** A plain-data copy of the whole scene: what a history entry remembers. */
  function cloneScene(): Scene {
    return {
      nodes: nodes.value.map((node) => ({ ...node })),
      objects: objects.value.map((item) => ({ ...item })),
      edges: edges.value.map((edge) => ({
        ...edge,
        waypoints: edge.waypoints.map((point) => ({ ...point })),
      })),
    }
  }
  function restore(scene: Scene): void {
    // A JSON round trip rather than `structuredClone`: the scene is plain data,
    // and this works wherever the SPA or its tests run.
    const fresh = JSON.parse(JSON.stringify(scene)) as Scene
    nodes.value = fresh.nodes
    objects.value = fresh.objects
    edges.value = fresh.edges
  }
  /** The scene as it was when the gesture in progress began. */
  let gestureBefore: Scene | null = null
  /** Boxes and objects the pointer itself moved: the only ones that change group. */
  const grabbed = new Set<string>()

  function record(label: string, before: Scene, mergeKey?: string): void {
    const after = cloneScene()
    const restoring = (scene: Scene) => () => {
      restore(scene)
      persist()
    }
    history.record({
      label,
      undo: restoring(before),
      do: restoring(after),
      ...(mergeKey ? { mergeKey } : {}),
    })
  }

  function differing<T>(before: T[], after: T[], key: (item: T) => string): string[] {
    const old = new Map(before.map((item) => [key(item), JSON.stringify(item)]))
    return after.filter((item) => old.get(key(item)) !== JSON.stringify(item)).map(key)
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
    const snapshot = {
      nodes: nodes.value.map((node) => ({ ...node })),
      objects: objects.value.map((item) => ({ ...item })),
      edges: edges.value.map((edge) => ({
        ...edge,
        waypoints: edge.waypoints.map((point) => ({ ...point })),
      })),
    }
    // The token is the one the edits were made on: read now, with the content. A save
    // queued behind another may inherit what *that save* was answered (its content is a
    // superset of the earlier one) - never a revision a later read brought in, which
    // would let a stale scene overwrite a colleague's (the server then answers 409).
    const asked = revisions.get(current.id) ?? revision.value
    queue = queue.then(async () => {
      const body = { revision: Math.max(asked, savedRevisions.get(current.id) ?? 0), ...snapshot }
      saving.value = true
      try {
        const result = await api.PUT('/diagrams/{diagram_id}/layout', {
          params: { path: { diagram_id: current.id } },
          body,
        })
        unwrap(result)
        // The server's revision when it sent one (a browser sees it through
        // CORS `expose_headers`), else one more than the revision just sent.
        const answered = Number(result.response.headers.get('X-Diagram-Revision'))
        const next = Number.isInteger(answered) && answered > 0 ? answered : body.revision + 1
        savedRevisions.set(current.id, next)
        revisions.set(current.id, Math.max(revisions.get(current.id) ?? 0, next))
        if (diagram.value?.id === current.id) {
          revision.value = Math.max(revision.value, next)
        }
        saveError.value = ''
      } catch (caught) {
        if (caught instanceof ApiError && caught.code === 'diagram_revision_conflict') {
          // Somebody saved first. Nothing is retried: the next step is the
          // user's — reload their version, paste mine over it, or give up.
          conflict.value = true
          saveError.value = caught.message
          return
        }
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
        objects.value = answer.objects
        edges.value = answer.edges
        revision.value = answer.revision
        revisions.set(answer.id, answer.revision)
        inactiveIds.value = answer.inactive_element_ids ?? []
        gestureBefore = null
        grabbed.clear()
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
    objects.value = []
    edges.value = []
    revision.value = 0
    inactiveIds.value = []
    gestureBefore = null
    grabbed.clear()
    history.clear()
    conflict.value = false
    reloaded.value = false
    mine = null
  }

  function place(element: ElementRead, at: Point): void {
    if (placed.value.has(element.id)) {
      return
    }
    const before = cloneScene()
    nodes.value.push({
      element_id: element.id,
      x: at.x,
      y: at.y,
      ...DEFAULT_BOX,
      parent_object_id: null,
      z: 0,
    })
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
      gestureBefore ??= cloneScene()
      grabbed.add(elementId)
      node.x = to.x
      node.y = to.y
    }
  }

  /** Resize a box on screen only; `endGesture` once the gesture is over. */
  function resize(elementId: string, size: Size): void {
    const node = nodes.value.find((candidate) => candidate.element_id === elementId)
    if (node) {
      gestureBefore ??= cloneScene()
      node.width = size.width
      node.height = size.height
    }
  }

  /** The group (if any) a thing just dropped should now belong to. */
  function adopt(): void {
    for (const id of grabbed) {
      const node = nodes.value.find((candidate) => candidate.element_id === id)
      if (node) {
        node.parent_object_id = groupAt(objects.value, centreOf(node), { id: null, isGroup: false })
        continue
      }
      const item = objects.value.find((candidate) => candidate.id === id)
      if (item) {
        item.parent_id = groupAt(objects.value, centreOf(item), {
          id: item.id,
          isGroup: item.kind === 'group',
        })
      }
    }
    grabbed.clear()
  }

  /**
   * The gesture that `move`, `resize`, `moveObject` and the bend setters were
   * part of is over: boxes dropped into a group join it, then one history
   * entry for all of it, then the save. Arrow-key nudges of the same things
   * fold into one entry (`source: 'keyboard'`).
   */
  function endGesture(source?: 'keyboard'): void {
    const before = gestureBefore
    gestureBefore = null
    if (before) {
      adopt()
      const after = cloneScene()
      const changed = [
        ...differing(before.nodes, after.nodes, (n) => n.element_id),
        ...differing(before.objects, after.objects, (o) => o.id),
        ...differing(before.edges, after.edges, (e) => e.relationship_id),
      ]
      if (changed.length > 0) {
        const key = source === 'keyboard' ? `keyboard:${changed.sort().join(',')}` : undefined
        record('Déplacer ou redimensionner', before, key)
      }
    }
    grabbed.clear()
    persist()
  }

  /**
   * Keep the scene one the server accepts after boxes left it: a route whose
   * link no longer joins two placed elements is dropped (the API answers 422
   * to a stray one), and a note anchored on a removed element is let go.
   */
  function prune(): void {
    const present = new Set(nodes.value.map((node) => node.element_id))
    const joined = new Set(
      relationships.value
        .filter((link) => present.has(link.source_id) && present.has(link.target_id))
        .map((link) => link.id),
    )
    edges.value = edges.value.filter((edge) => joined.has(edge.relationship_id))
    objects.value = objects.value.map((item) =>
      item.anchor_element_id && !present.has(item.anchor_element_id)
        ? { ...item, anchor_element_id: null }
        : item,
    )
  }

  /** Take boxes off the diagram. The elements themselves are never deleted. */
  function removeMany(elementIds: string[]): void {
    const before = cloneScene()
    nodes.value = nodes.value.filter((node) => !elementIds.includes(node.element_id))
    prune()
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
    const before = cloneScene()
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

  // --- Notes and groups (docs/adr/0063) -------------------------

  const DEFAULT_OBJECT: Record<'note' | 'group', Size & { text: string; z: number }> = {
    note: { width: 180, height: 90, text: '', z: 1 },
    group: { width: 320, height: 200, text: 'Groupe', z: -1 },
  }

  /** A new note or group at `at`; returns its id. A group sits behind, a note in front. */
  function addObject(kind: 'note' | 'group', at: Point): string {
    const before = cloneScene()
    const id = newId()
    const base = DEFAULT_OBJECT[kind]
    objects.value.push({
      id,
      kind,
      x: at.x,
      y: at.y,
      width: base.width,
      height: base.height,
      text: base.text,
      colour: null,
      z: base.z,
      parent_id: null,
      anchor_element_id: null,
    })
    record(kind === 'group' ? 'Ajouter un groupe' : 'Ajouter une note', before)
    persist()
    return id
  }

  function updateObject(
    id: string,
    patch: Partial<Pick<DiagramObject, 'text' | 'colour' | 'anchor_element_id'>>,
  ): void {
    const item = objects.value.find((candidate) => candidate.id === id)
    if (!item) {
      return
    }
    const before = cloneScene()
    Object.assign(item, patch)
    // Typing is one step, not one per key.
    record('Modifier', before, `edit:${id}:${Object.keys(patch).sort().join(',')}`)
    persist()
  }

  /** Move a note or a group on screen; a group takes everything inside it along. */
  function moveObject(id: string, to: Point): void {
    const item = objects.value.find((candidate) => candidate.id === id)
    if (!item) {
      return
    }
    gestureBefore ??= cloneScene()
    grabbed.add(id)
    const moved = moveWithChildren(
      { nodes: nodes.value, objects: objects.value },
      id,
      to.x - item.x,
      to.y - item.y,
    )
    nodes.value = moved.nodes
    objects.value = moved.objects
  }

  function resizeObject(id: string, size: Size): void {
    const item = objects.value.find((candidate) => candidate.id === id)
    if (item) {
      gestureBefore ??= cloneScene()
      item.width = size.width
      item.height = size.height
    }
  }

  /** A note disappears; a group disappears and hands what it held to its own parent. */
  function removeObject(id: string): void {
    const item = objects.value.find((candidate) => candidate.id === id)
    if (!item) {
      return
    }
    const before = cloneScene()
    if (item.kind === 'group') {
      const next = removeGroup({ nodes: nodes.value, objects: objects.value }, id)
      nodes.value = next.nodes
      objects.value = next.objects
    } else {
      objects.value = objects.value.filter((candidate) => candidate.id !== id)
    }
    record(item.kind === 'group' ? 'Retirer le groupe' : 'Supprimer la note', before)
    persist()
  }

  // --- The route of a link ---------------------------------------------

  function waypointsOf(relationshipId: string) {
    return edges.value.find((edge) => edge.relationship_id === relationshipId)?.waypoints ?? []
  }

  function setWaypoints(relationshipId: string, waypoints: { x: number; y: number }[]): void {
    const others = edges.value.filter((edge) => edge.relationship_id !== relationshipId)
    // No bend left = no row: the link goes back to its straight segment.
    edges.value = waypoints.length > 0 ? [...others, { relationship_id: relationshipId, waypoints }] : others
  }

  /** Start a bend on a segment; the drag that follows is `moveWaypoint`, ended by `endGesture`. */
  function addWaypoint(relationshipId: string, segment: number, at: Point): void {
    gestureBefore ??= cloneScene()
    setWaypoints(relationshipId, insertWaypoint(waypointsOf(relationshipId), segment, at))
  }

  function moveWaypoint(relationshipId: string, index: number, to: Point): void {
    gestureBefore ??= cloneScene()
    setWaypoints(relationshipId, movedWaypoint(waypointsOf(relationshipId), index, to))
  }

  function removeWaypoint(relationshipId: string, index: number): void {
    const before = cloneScene()
    setWaypoints(relationshipId, removedWaypoint(waypointsOf(relationshipId), index))
    record('Supprimer un coude', before)
    persist()
  }

  /** Back to the straight segment. */
  function straighten(relationshipId: string): void {
    const before = cloneScene()
    setWaypoints(relationshipId, [])
    record('Redresser le lien', before)
    persist()
  }

  async function reloadAfterConflict(): Promise<void> {
    const current = diagram.value
    if (!current) {
      return
    }
    autosave.cancel()
    mine = {
      ...cloneScene(),
      elements: elements.value.map((element) => ({ ...element })),
      relationships: relationships.value.map((link) => ({ ...link })),
    }
    await open(current.id) // empties the history: nothing can be undone across a conflict
    saveError.value = ''
    reloaded.value = true
  }

  /** Put my scene back over the colleague's, on the revision just loaded, and save it. */
  function pasteMine(): void {
    if (!mine) {
      return
    }
    restore(mine)
    for (const element of mine.elements) {
      if (!byId.value.has(element.id)) {
        elements.value.push(element)
      }
    }
    for (const link of mine.relationships) {
      if (!relationships.value.some((known) => known.id === link.id)) {
        relationships.value.push(link)
      }
    }
    mine = null
    conflict.value = false
    reloaded.value = false
    saveError.value = ''
    persist()
  }

  function discardMine(): void {
    mine = null
    conflict.value = false
    reloaded.value = false
    saveError.value = ''
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
    nodes,
    objects,
    edges,
    revision,
    conflict,
    reloaded,
    reloadAfterConflict,
    pasteMine,
    discardMine,
    addObject,
    updateObject,
    moveObject,
    resizeObject,
    removeObject,
    addWaypoint,
    moveWaypoint,
    removeWaypoint,
    straighten,
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
