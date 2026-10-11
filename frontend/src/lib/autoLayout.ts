// Where the boxes of a generated view go: ArchiMate's own reading, in layers.
//
// A hand-drawn diagram is placed by a person; a view generated from a query is
// placed by this, and must come out the same every time the same elements are
// there — otherwise a live view would shuffle at each opening. So: rows by
// layer, boxes ordered inside a row by the barycentre of their neighbours (two
// sweeps, down then up), ties broken by name then id, rows folded when too
// wide. No randomness, no iteration to convergence.
//
// Which layer an element is in comes from the API (`ElementRead.layer`); only
// the *visual order* of the layers is written here, as a presentation table
// like `LAYER_LABELS` — it is not a rule of the metamodel. This module shares
// with the rings of the neighbourhood nothing but the size of a box.
import type { components } from '../api/schema'
import type { Point, Rect } from './diagramGeometry'
import { BOX_HEIGHT, BOX_WIDTH } from './graphLayout'

type Layer = components['schemas']['Layer']

/** A row is folded into sub-rows past this width, in canvas units. */
export const MAX_ROW_WIDTH = 1400
export const GAP_X = 40
export const GAP_Y = 90
export const ORIGIN: Point = { x: 24, y: 24 }

/** Top to bottom: motivation and strategy, business, application, technology (physical with it), migration. */
export const ROW_OF: Record<Layer, number> = {
  motivation: 0,
  strategy: 0,
  business: 1,
  application: 2,
  technology: 3,
  physical: 3,
  implementation_migration: 4,
  other: 5,
}

export type LayoutInput = {
  elements: { id: string; name: string; layer: Layer }[]
  relationships: { source_id: string; target_id: string }[]
}

type Item = LayoutInput['elements'][number]

const byName = (a: Item, b: Item) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)

export function layoutByLayers(
  input: LayoutInput,
  options: { rowOf?: Record<Layer, number> } = {},
): Map<string, Point> {
  const rowOf = options.rowOf ?? ROW_OF
  const rows = new Map<number, Item[]>()
  for (const item of [...input.elements].sort(byName)) {
    const key = rowOf[item.layer]
    rows.set(key, [...(rows.get(key) ?? []), item])
  }
  const keys = [...rows.keys()].sort((a, b) => a - b)

  const known = new Set(input.elements.map((item) => item.id))
  const neighbours = new Map<string, Set<string>>()
  for (const { source_id, target_id } of input.relationships) {
    if (known.has(source_id) && known.has(target_id)) {
      neighbours.set(source_id, (neighbours.get(source_id) ?? new Set()).add(target_id))
      neighbours.set(target_id, (neighbours.get(target_id) ?? new Set()).add(source_id))
    }
  }

  const index = new Map<string, number>()
  const reindex = (row: Item[]) => row.forEach((item, position) => index.set(item.id, position))
  keys.forEach((key) => reindex(rows.get(key)!))

  // Order one row by the mean position of the neighbours it has in `reference`.
  function sweep(key: number, reference: number): void {
    const row = rows.get(key)!
    const above = new Set((rows.get(reference) ?? []).map((item) => item.id))
    const centre = (item: Item): number => {
      const positions = [...(neighbours.get(item.id) ?? [])]
        .filter((other) => above.has(other))
        .map((other) => index.get(other)!)
      return positions.length > 0
        ? positions.reduce((sum, p) => sum + p, 0) / positions.length
        : index.get(item.id)!
    }
    const scored = new Map(row.map((item) => [item.id, centre(item)]))
    row.sort((a, b) => scored.get(a.id)! - scored.get(b.id)! || byName(a, b))
    reindex(row)
  }
  for (let k = 1; k < keys.length; k += 1) {
    sweep(keys[k], keys[k - 1])
  }
  for (let k = keys.length - 2; k >= 0; k -= 1) {
    sweep(keys[k], keys[k + 1])
  }

  const perLine = Math.max(1, Math.floor((MAX_ROW_WIDTH + GAP_X) / (BOX_WIDTH + GAP_X)))
  const positions = new Map<string, Point>()
  let line = 0
  for (const key of keys) {
    const row = rows.get(key)!
    for (let start = 0; start < row.length; start += perLine) {
      row.slice(start, start + perLine).forEach((item, column) => {
        positions.set(item.id, {
          x: ORIGIN.x + column * (BOX_WIDTH + GAP_X),
          y: ORIGIN.y + line * (BOX_HEIGHT + GAP_Y),
        })
      })
      line += 1
    }
  }
  return positions
}

/**
 * Lay out only what a refresh adds, below everything already on the diagram,
 * so no box that is there — and may have been moved by hand — is touched.
 */
export function placeAdded(added: LayoutInput, occupied: Rect[]): Map<string, Point> {
  const bottom = occupied.length > 0 ? Math.max(...occupied.map((box) => box.y + box.height)) : null
  const shift = bottom === null ? 0 : bottom + GAP_Y - ORIGIN.y
  const fresh = layoutByLayers(added)
  return new Map([...fresh].map(([id, point]) => [id, { x: point.x, y: point.y + shift }]))
}
