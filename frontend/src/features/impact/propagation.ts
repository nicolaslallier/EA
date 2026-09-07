// Which way a failure travels, and how far.
//
// `/impact` answers with a `GraphRead` — the impacted elements and every link
// between them — but not with distances, and a cascade without distances is a
// pile. So the walk is redone here, over the very edges the server returned,
// following each one the way dependency runs.
//
// The direction is *not* decided here. `impact_follows_direction` is a fact of
// ArchiMate that `/metamodel` serves per relationship type, and the caller
// passes it in: a table of directions written in TypeScript would be a second
// metamodel, free to draw a cascade the API never walked (`docs/adr/0012`).
//
// The module is pure and deterministic, like the geometry it feeds: the same
// response always yields the same waves, so a test asserts them without
// mounting anything.
import type { components } from '../../api/schema'

type ElementRead = components['schemas']['ElementRead']
type RelationshipRead = components['schemas']['RelationshipRead']
type GraphRead = components['schemas']['GraphRead']

/** Whether an outage at the source of this relationship travels along its arrow. */
export type FollowsArrow = (relationshipType: string) => boolean

/** The elements a failure reaches after the same number of hops. */
export type Wave = {
  hops: number
  elements: ElementRead[]
}

export type Propagation = {
  /** How many hops of dependency each element is from the subject; 0 is the subject. */
  hops: Map<string, number>
  /** The impacted elements, nearest first, the subject excluded. */
  waves: Wave[]
  /**
   * The links drawn but explaining no distance — sideways or backwards
   * relations between two elements the cascade reached by other paths.
   */
  inert: Set<string>
}

const EMPTY: Propagation = { hops: new Map(), waves: [], inert: new Set() }

/** The element a failure at `from` reaches through `link`, if it reaches one. */
function downstream(
  link: RelationshipRead,
  from: string,
  follows: FollowsArrow,
): string | null {
  const [source, target] = follows(link.relationship_type)
    ? [link.source_id, link.target_id]
    : [link.target_id, link.source_id]
  return source === from ? target : null
}

/**
 * Walk the cascade out from `subjectId`.
 *
 * A breadth-first walk, so an element's hop count is the shortest chain of
 * dependencies that reaches it — the answer to "how far away does this break
 * things", which the longest chain would overstate.
 */
export function propagate(
  graph: GraphRead,
  subjectId: string,
  follows: FollowsArrow,
): Propagation {
  const known = new Map(graph.elements.map((element) => [element.id, element]))
  if (!known.has(subjectId)) {
    return EMPTY
  }

  // A link naming an element the response did not carry cannot be walked;
  // dropping it here keeps every later step free of missing endpoints.
  const links = graph.relationships.filter(
    (link) => known.has(link.source_id) && known.has(link.target_id),
  )

  const hops = new Map<string, number>([[subjectId, 0]])
  const queue = [subjectId]
  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index]
    const distance = hops.get(current)!
    for (const link of links) {
      const next = downstream(link, current, follows)
      if (next !== null && !hops.has(next)) {
        hops.set(next, distance + 1)
        queue.push(next)
      }
    }
  }

  // A link belongs to the cascade when it is what puts its far end one hop
  // further out. Everything else — a link between two elements of the same
  // wave, or one pointing back inwards — is context, not explanation.
  const inert = new Set(
    links
      .filter((link) => {
        const from = follows(link.relationship_type) ? link.source_id : link.target_id
        const to = downstream(link, from, follows)!
        const start = hops.get(from)
        return start === undefined || hops.get(to) !== start + 1
      })
      .map((link) => link.id),
  )

  const reached = [...hops.entries()].filter(([, distance]) => distance > 0)
  const depth = reached.reduce((furthest, [, distance]) => Math.max(furthest, distance), 0)
  const waves: Wave[] = []
  for (let distance = 1; distance <= depth; distance += 1) {
    waves.push({
      hops: distance,
      // Alphabetical: this is read as a list, where the server's order carries
      // no meaning. The drawing keeps that order instead, for stability.
      elements: reached
        .filter(([, at]) => at === distance)
        .map(([id]) => known.get(id)!)
        .sort((one, other) => one.name.localeCompare(other.name, 'fr')),
    })
  }

  return { hops, waves, inert }
}
