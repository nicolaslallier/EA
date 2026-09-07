// Where a sub-graph goes on the canvas.
//
// A neighbourhood has a centre and a distance, so its drawing has them too:
// concentric rings, one per hop, the subject in the middle. The reading is
// then immediate — an element on the second ring is two relationships away —
// which no force-directed layout can promise.
//
// Nothing here is animated, iterative or random: the same sub-graph always
// draws the same way, so a test can assert a coordinate and a user can compare
// two screenshots. That is also why this is a module of pure functions rather
// than a component: the geometry is testable without mounting anything.
import type { components } from '../../api/schema'

type ElementRead = components['schemas']['ElementRead']
type RelationshipRead = components['schemas']['RelationshipRead']
export type GraphRead = components['schemas']['GraphRead']

/** The size of an element's box, in user units of the viewBox. */
export const BOX_WIDTH = 132
export const BOX_HEIGHT = 46
/** The smallest distance between two rings — a box width, and room to breathe. */
const RING_GAP = 190
/** Clear space around the outermost ring, so a label never touches the edge. */
const MARGIN = 36
/** The gap a ring leaves between two boxes before it pushes itself outwards. */
const BOX_GAP = 18
/** How far apart two links between the same pair bow. */
const BOW = 34
/** The radius of the arc a self-link is drawn as. */
const LOOP = 30

export type PositionedNode = {
  element: ElementRead
  /** Relationships away from the subject: 0 is the subject itself. */
  hops: number
  x: number
  y: number
}

export type PositionedEdge = {
  relationship: RelationshipRead
  /** The stroke, an SVG path: a quadratic curve, or an arc for a self-link. */
  path: string
  /** The ends, trimmed to the box borders so the arrow head stays visible. */
  x1: number
  y1: number
  x2: number
  y2: number
  /** Where the verb is written. */
  labelX: number
  labelY: number
  loop: boolean
}

export type Diagram = {
  nodes: PositionedNode[]
  edges: PositionedEdge[]
  /** The canvas is square: rings are. */
  size: number
  viewBox: string
  /** The radius of each ring, so the drawing can trace them as a guide. */
  rings: number[]
  /** The greatest hop count actually drawn. */
  hops: number
}

/**
 * How many relationships away each element is from `rootId`.
 *
 * The walk ignores direction: `/neighbourhood` itself follows links either
 * way, and "who is around this element" is not a question about arrows.
 */
export function hopsFrom(graph: GraphRead, rootId: string): Map<string, number> {
  const near = new Map<string, string[]>(graph.elements.map((element) => [element.id, []]))
  for (const relationship of graph.relationships) {
    const { source_id: source, target_id: target } = relationship
    // An edge naming an element the response did not carry cannot be drawn;
    // dropping it here keeps every later step free of missing endpoints.
    if (!near.has(source) || !near.has(target)) {
      continue
    }
    near.get(source)!.push(target)
    near.get(target)!.push(source)
  }

  const hops = new Map<string, number>()
  if (!near.has(rootId)) {
    return hops
  }
  hops.set(rootId, 0)
  const queue = [rootId]
  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index]
    const distance = hops.get(current)!
    for (const other of near.get(current)!) {
      if (!hops.has(other)) {
        hops.set(other, distance + 1)
        queue.push(other)
      }
    }
  }
  return hops
}

/**
 * The radius of the ring holding `count` boxes, never closer in than `inner`.
 *
 * A ring of a fixed radius overlaps its own boxes as soon as it holds a dozen
 * elements — exactly what a hub looks like. So a crowded ring pushes itself
 * outwards until the boxes fit side by side.
 */
function radiusFor(count: number, inner: number): number {
  const needed = (count * (BOX_WIDTH + BOX_GAP)) / (2 * Math.PI)
  return Math.max(inner + RING_GAP, needed)
}

/** The point where a segment leaving `node` towards (dx, dy) crosses its box. */
function border(node: PositionedNode, dx: number, dy: number): { x: number; y: number } {
  const scale = Math.min(
    Math.abs(dx) < 1e-6 ? Infinity : BOX_WIDTH / 2 / Math.abs(dx),
    Math.abs(dy) < 1e-6 ? Infinity : BOX_HEIGHT / 2 / Math.abs(dy),
  )
  return { x: node.x + dx * scale, y: node.y + dy * scale }
}

/** A link from an element to itself, drawn as an arc above its box. */
function selfEdge(relationship: RelationshipRead, node: PositionedNode): PositionedEdge {
  const top = node.y - BOX_HEIGHT / 2
  const [x1, x2] = [node.x - 14, node.x + 14]
  return {
    relationship,
    path: `M ${x1},${top} A ${LOOP},${LOOP} 0 1 1 ${x2},${top}`,
    x1,
    y1: top,
    x2,
    y2: top,
    labelX: node.x,
    labelY: top - LOOP * 1.6,
    loop: true,
  }
}

/**
 * Lay the sub-graph out around its subject.
 *
 * An element the walk cannot reach — which the API does not return, but a
 * drawing should not lose silently — lands one ring beyond the last.
 */
export function layout(graph: GraphRead, rootId: string): Diagram {
  const hops = hopsFrom(graph, rootId)
  if (hops.size === 0) {
    return { nodes: [], edges: [], size: 2 * MARGIN, viewBox: `0 0 ${2 * MARGIN} ${2 * MARGIN}`, rings: [], hops: 0 }
  }

  const reached = Math.max(...hops.values())
  const adrift = reached + 1
  const distanceOf = (element: ElementRead): number => hops.get(element.id) ?? adrift
  const depth = graph.elements.some((element) => !hops.has(element.id)) ? adrift : reached

  // Elements keep the order the API sent them in, so the drawing is stable
  // between two loads of the same sub-graph.
  const byRing: ElementRead[][] = Array.from({ length: depth + 1 }, () => [])
  for (const element of graph.elements) {
    byRing[distanceOf(element)].push(element)
  }

  const rings: number[] = []
  let radius = 0
  for (let ring = 1; ring <= depth; ring += 1) {
    radius = radiusFor(byRing[ring].length, radius)
    rings.push(radius)
  }

  const size = 2 * (radius + BOX_WIDTH / 2 + MARGIN)
  const centre = size / 2
  const nodes: PositionedNode[] = []
  byRing.forEach((elements, ring) => {
    const step = (2 * Math.PI) / elements.length
    // Odd rings start half a step round, so a node is never hidden directly
    // behind the one in front of it on the ring inside.
    const start = -Math.PI / 2 + (ring % 2 === 1 ? step / 2 : 0)
    elements.forEach((element, index) => {
      const angle = start + index * step
      const distance = ring === 0 ? 0 : rings[ring - 1]
      nodes.push({
        element,
        hops: ring,
        x: centre + distance * Math.cos(angle),
        y: centre + distance * Math.sin(angle),
      })
    })
  })

  const placed = new Map(nodes.map((node) => [node.element.id, node]))
  // Two links between the same pair would land on the same stroke, so they are
  // counted first and then bowed apart, symmetrically about the straight line.
  const pairs = new Map<string, RelationshipRead[]>()
  const drawable = graph.relationships.filter(
    (relationship) =>
      placed.has(relationship.source_id) && placed.has(relationship.target_id),
  )
  for (const relationship of drawable) {
    const key = [relationship.source_id, relationship.target_id].sort().join('|')
    pairs.set(key, [...(pairs.get(key) ?? []), relationship])
  }

  const edges = drawable.map((relationship) => {
    const source = placed.get(relationship.source_id)!
    const target = placed.get(relationship.target_id)!
    if (source === target) {
      return selfEdge(relationship, source)
    }

    const pair = [relationship.source_id, relationship.target_id].sort()
    const siblings = pairs.get(pair.join('|'))!
    // The bow is measured on the pair, not on this link's own direction: two
    // links drawn head to tail would otherwise curve to the same side and land
    // back on top of each other.
    const way = relationship.source_id === pair[0] ? 1 : -1
    const rank = (siblings.indexOf(relationship) - (siblings.length - 1) / 2) * way

    const [dx, dy] = [target.x - source.x, target.y - source.y]
    const length = Math.hypot(dx, dy)
    const control = {
      x: (source.x + target.x) / 2 - (dy / length) * rank * BOW,
      y: (source.y + target.y) / 2 + (dx / length) * rank * BOW,
    }
    // Both ends aim at the control point rather than at the other box, so a
    // bowed edge leaves and arrives along the curve it is actually drawn as.
    const from = border(source, control.x - source.x, control.y - source.y)
    const to = border(target, control.x - target.x, control.y - target.y)
    return {
      relationship,
      path: `M ${from.x},${from.y} Q ${control.x},${control.y} ${to.x},${to.y}`,
      x1: from.x,
      y1: from.y,
      x2: to.x,
      y2: to.y,
      // The midpoint of a quadratic curve, where its verb is written.
      labelX: (from.x + 2 * control.x + to.x) / 4,
      labelY: (from.y + 2 * control.y + to.y) / 4,
      loop: false,
    }
  })

  return { nodes, edges, size, viewBox: `0 0 ${size} ${size}`, rings, hops: depth }
}
