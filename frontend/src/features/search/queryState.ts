// A search, as the browser holds it and as an address carries it.
//
// The shape is the API's (`ElementQuerySchema`, generated); nothing is
// restated except the parts of the address: `?q=` carries the JSON, bounded,
// and a query that does not fit is the cue to save it instead.
import type { components } from '../../api/schema'

export type Query = components['schemas']['ElementQuerySchema']
export type PropertyTest = components['schemas']['PropertyTestSchema']
export type RelationTest = components['schemas']['RelationTestSchema']

/** The longest `?q=` worth sharing in a link; past it, "Enregistrer" is the way to share. */
export const MAX_URL_QUERY = 2000
export const PAGE_SIZE = 25

/** The query that filters nothing. Every field is required by the API, so every field is here. */
export function emptyQuery(): Query {
  return {
    version: 1,
    element_types: [],
    layers: [],
    text: null,
    package_id: null,
    include_subpackages: true,
    root_only: false,
    properties: [],
    relations: [],
    order: 'name',
    limit: PAGE_SIZE,
    offset: 0,
  }
}

/** A query is plain data: a JSON round trip is a deep copy. */
export function cloneQuery(query: Query): Query {
  return JSON.parse(JSON.stringify(query)) as Query
}

/**
 * The form a link carries, or `null` when it is too long for one.
 *
 * The page is left out: where in the results a reader was is not part of the
 * question they would send to a colleague.
 */
export function encodeQuery(query: Query): string | null {
  const json = JSON.stringify({ ...query, offset: 0 })
  return json.length <= MAX_URL_QUERY ? json : null
}

/**
 * A query read from an address, or `null` for anything that is not a version-1 object.
 *
 * Absent fields take their defaults, so a short hand-written link works. What a
 * value *means* is the API's to judge: a link carrying nonsense gets its 422.
 */
export function decodeQuery(raw: string): Query | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return null
    }
    if ((parsed as { version?: unknown }).version !== 1) {
      return null
    }
    const query = { ...emptyQuery(), ...(parsed as Partial<Query>) }
    // The form iterates these: a non-list would throw while rendering. Whatever
    // else is wrong is the API's to refuse, with a readable 422.
    const isObject = (item: unknown): boolean =>
      typeof item === 'object' && item !== null && !Array.isArray(item)
    const kept: Record<string, (item: unknown) => boolean> = {
      element_types: (item) => typeof item === 'string',
      layers: (item) => typeof item === 'string',
      properties: isObject,
      relations: isObject,
    }
    for (const [key, keep] of Object.entries(kept)) {
      const list = (query as Record<string, unknown>)[key]
      ;(query as Record<string, unknown>)[key] = Array.isArray(list) ? list.filter(keep) : []
    }
    return query
  } catch {
    return null
  }
}
