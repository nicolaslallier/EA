import { describe, expect, it } from 'vitest'

import { BOX_HEIGHT, BOX_WIDTH, hopsFrom, layout } from '../src/lib/graphLayout'
import { aGraph, anElement, aRelationship } from './support/api'

const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Commandes' })
const C = anElement({ id: 'cccccccc-3333-4333-8333-333333333333', name: 'Clients' })
const D = anElement({ id: 'dddddddd-4444-4444-8444-444444444444', name: 'Livraison' })

function link(source: string, target: string, id = `${source}-${target}`) {
  return aRelationship({ id, source_id: source, target_id: target })
}

/** How far a node ended up from the centre of the canvas. */
function radiusOf(node: { x: number; y: number }, size: number): number {
  return Math.hypot(node.x - size / 2, node.y - size / 2)
}

describe('hopsFrom', () => {
  it('counts hops in both directions, because a neighbour is a neighbour', () => {
    // B → A → C: read as arrows, C is unreachable from B; as a neighbourhood,
    // it is two hops away.
    const graph = aGraph([A, B, C], [link(B.id, A.id), link(A.id, C.id)])

    const hops = hopsFrom(graph, B.id)

    expect(hops.get(B.id)).toBe(0)
    expect(hops.get(A.id)).toBe(1)
    expect(hops.get(C.id)).toBe(2)
  })

  it('takes the shortest walk when two lead to the same element', () => {
    const graph = aGraph(
      [A, B, C],
      [link(A.id, B.id), link(B.id, C.id), link(A.id, C.id, 'shortcut')],
    )

    expect(hopsFrom(graph, A.id).get(C.id)).toBe(1)
  })

  it('says nothing at all about a subject the sub-graph does not contain', () => {
    expect(hopsFrom(aGraph([A], []), B.id).size).toBe(0)
  })
})

describe('layout', () => {
  it('puts the subject at the centre of the canvas', () => {
    const diagram = layout(aGraph([A, B], [link(A.id, B.id)]), A.id)
    const subject = diagram.nodes.find((node) => node.element.id === A.id)

    expect(subject).toMatchObject({ hops: 0, x: diagram.size / 2, y: diagram.size / 2 })
  })

  it('places each element on the ring of its hop count', () => {
    const graph = aGraph([A, B, C], [link(A.id, B.id), link(B.id, C.id)])

    const diagram = layout(graph, A.id)
    const [first, second] = [B, C].map(
      (element) => diagram.nodes.find((node) => node.element.id === element.id)!,
    )

    expect(first.hops).toBe(1)
    expect(second.hops).toBe(2)
    expect(radiusOf(second, diagram.size)).toBeGreaterThan(radiusOf(first, diagram.size))
  })

  it('spreads the elements of one ring instead of stacking them', () => {
    const graph = aGraph([A, B, C, D], [link(A.id, B.id), link(A.id, C.id), link(A.id, D.id)])

    const ring = layout(graph, A.id).nodes.filter((node) => node.hops === 1)

    expect(ring).toHaveLength(3)
    const points = new Set(ring.map((node) => `${Math.round(node.x)},${Math.round(node.y)}`))
    expect(points.size).toBe(3)
  })

  it('pushes a crowded ring outwards rather than overlapping its boxes', () => {
    const many = Array.from({ length: 12 }, (_, index) =>
      anElement({ id: `eeeeeeee-0000-4000-8000-00000000000${index.toString(36)}` }),
    )
    const graph = aGraph([A, ...many], many.map((element) => link(A.id, element.id)))

    const diagram = layout(graph, A.id)
    const ring = diagram.nodes.filter((node) => node.hops === 1)
    const angle = (node: { x: number; y: number }) =>
      Math.atan2(node.y - diagram.size / 2, node.x - diagram.size / 2)
    const sorted = [...ring].sort((left, right) => angle(left) - angle(right))
    const gaps = sorted
      .slice(1)
      .map((node, index) => Math.hypot(node.x - sorted[index].x, node.y - sorted[index].y))

    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(BOX_WIDTH)
  })

  it('keeps an element no walk reaches rather than dropping it off the drawing', () => {
    // The API never answers this, but a drawing that silently loses an element
    // is worse than one that shows it adrift on the outer ring.
    const diagram = layout(aGraph([A, B, C], [link(A.id, B.id)]), A.id)

    expect(diagram.nodes.map((node) => node.element.id)).toContain(C.id)
    expect(diagram.nodes.find((node) => node.element.id === C.id)!.hops).toBe(2)
  })

  it('draws nothing when the subject is not in the sub-graph', () => {
    expect(layout(aGraph([B], []), A.id).nodes).toHaveLength(0)
  })

  it('trims an edge to the border of the boxes it joins', () => {
    const graph = aGraph([A, B], [link(A.id, B.id)])

    const [edge] = layout(graph, A.id).edges
    const [source, target] = [A, B].map(
      (element) => layout(graph, A.id).nodes.find((node) => node.element.id === element.id)!,
    )

    // Both ends sit on a box border: never further than half a diagonal from
    // the centre, so the arrow head lands on the box and not under it.
    const half = Math.hypot(BOX_WIDTH / 2, BOX_HEIGHT / 2)
    expect(Math.hypot(edge.x1 - source.x, edge.y1 - source.y)).toBeLessThanOrEqual(half)
    expect(Math.hypot(edge.x2 - target.x, edge.y2 - target.y)).toBeLessThanOrEqual(half)
  })

  it('bows two links between the same pair apart so both can be read', () => {
    const graph = aGraph([A, B], [link(A.id, B.id, 'one'), link(B.id, A.id, 'two')])

    const [first, second] = layout(graph, A.id).edges

    expect(first.labelX).not.toBeCloseTo(second.labelX)
  })

  it('draws a self-link as a loop instead of a segment of zero length', () => {
    const graph = aGraph([A], [link(A.id, A.id)])

    const [edge] = layout(graph, A.id).edges

    expect(edge.loop).toBe(true)
    expect(edge.path).toMatch(/A /)
  })

  it('ignores an edge whose endpoint is missing from the response', () => {
    const graph = aGraph([A, B], [link(A.id, B.id), link(A.id, 'ffffffff-0000-4000-8000-000000000000')])

    expect(layout(graph, A.id).edges).toHaveLength(1)
  })

  it('sizes the canvas from the outermost ring', () => {
    const near = layout(aGraph([A, B], [link(A.id, B.id)]), A.id)
    const far = layout(
      aGraph([A, B, C], [link(A.id, B.id), link(B.id, C.id)]),
      A.id,
    )

    expect(far.size).toBeGreaterThan(near.size)
    expect(far.viewBox).toBe(`0 0 ${far.size} ${far.size}`)
  })
})
