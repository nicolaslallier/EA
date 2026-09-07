// What breaks if this element stops working — and how far away.
//
// `/impact` is the second reason this application stores a graph
// (`docs/adr/0004`): the traversal walks each relationship the way dependency
// actually runs, which is not always the way the arrow is drawn, and returns
// the sub-graph it reached. Everything that decides *which* elements are in
// that answer is the server's.
//
// What is left here is the reading of it: the distances, the waves and the
// links that explain them, all from `propagation.ts` — over the direction rule
// the metamodel serves, which the caller hands in.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'
import { propagate, type FollowsArrow, type Wave } from './propagation'

export type ElementRead = components['schemas']['ElementRead']
export type GraphRead = components['schemas']['GraphRead']
export type RelationshipType = components['schemas']['RelationshipType']
export type { Wave }

/**
 * The deepest walk the API accepts, mirroring `MAX_TRAVERSAL_DEPTH` in
 * `repositories/archimate_graph.py`, and the depth it walks when asked for
 * none. Both bound a `<select>` rather than validate: the numeric constraints
 * of OpenAPI do not survive the generator, and beyond them it is the API that
 * refuses — and its refusal is the message the user reads.
 */
export const MAX_DEPTH = 10
export const DEFAULT_DEPTH = 5

const EMPTY: GraphRead = { elements: [], relationships: [] }

export type Question = {
  depth: number
  /** One ArchiMate relationship type, or '' to follow them all. */
  relationshipType?: RelationshipType | ''
}

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function useImpact(follows: FollowsArrow) {
  /** The element the cascade starts from; '' when nothing is chosen yet. */
  const subjectId = ref('')
  const graph = ref<GraphRead>(EMPTY)
  const status = ref<Status>('idle')
  const error = ref('')

  // The traversal returns the subject among its own elements, so the screen
  // knows its name without asking `/elements/{id}` a second time.
  const subject = computed<ElementRead | null>(
    () => graph.value.elements.find((element) => element.id === subjectId.value) ?? null,
  )

  // A computed, not a value stored at fetch time: the direction rule arrives
  // from `/metamodel` on its own schedule, and the cascade must re-read itself
  // when it does rather than stay drawn from a guess.
  const cascade = computed(() => propagate(graph.value, subjectId.value, follows))

  const hops = computed(() => cascade.value.hops)
  const waves = computed<Wave[]>(() => cascade.value.waves)
  const inert = computed(() => cascade.value.inert)

  /** How many elements would be affected — the answer, in one number. */
  const impacted = computed(() =>
    waves.value.reduce((total, wave) => total + wave.elements.length, 0),
  )

  async function analyse(elementId: string, question: Question): Promise<void> {
    subjectId.value = elementId
    status.value = 'loading'
    error.value = ''
    try {
      graph.value = unwrap(
        await api.GET('/elements/{element_id}/impact', {
          params: {
            path: { element_id: elementId },
            // An unset filter is left out rather than sent empty: an empty
            // `relationship_type` would be a filter matching nothing.
            query: {
              depth: question.depth,
              ...(question.relationshipType
                ? { relationship_type: [question.relationshipType] }
                : {}),
            },
          },
        }),
      )
      status.value = 'ready'
    } catch (caught) {
      graph.value = EMPTY
      error.value = messageOf(caught)
      status.value = 'error'
    }
  }

  function clear(): void {
    subjectId.value = ''
    graph.value = EMPTY
    status.value = 'idle'
    error.value = ''
  }

  return { subjectId, graph, subject, hops, waves, inert, impacted, status, error, analyse, clear }
}
