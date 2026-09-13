// The sub-graph around one element: what the traversal answered, and the
// question that produced it.
//
// Every part of the question is the server's: the depth, the relationship
// types followed, and which elements the walk reaches. The browser receives a
// `GraphRead` — the nodes *and* the edges between them — so nothing here has
// to stitch two lists together, and the drawing never issues a second request
// to learn a name.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type ElementRead = components['schemas']['ElementRead']
export type GraphRead = components['schemas']['GraphRead']
export type RelationshipType = components['schemas']['RelationshipType']

/**
 * The deepest walk the API accepts, mirroring `MAX_TRAVERSAL_DEPTH` in
 * `repositories/archimate_graph.py`. It is repeated here to bound a slider,
 * not to validate: the bound is not in the generated types (OpenAPI numeric
 * constraints do not survive the generator), and the API refuses anything
 * beyond it anyway — its refusal is what the user would see.
 */
export const MAX_DEPTH = 10
export const DEFAULT_DEPTH = 1

const EMPTY: GraphRead = { elements: [], relationships: [] }

export type Question = {
  depth: number
  /** One ArchiMate relationship type, or '' to follow them all. */
  relationshipType?: RelationshipType | ''
}

export function useNeighbourhood() {
  /** The element the drawing is centred on; '' when nothing is chosen yet. */
  const focusId = ref('')
  const graph = ref<GraphRead>(EMPTY)
  // The subject changes as fast as a neighbour can be clicked, so a slow
  // traversal must never land over the one asked for after it.
  const traversal = useLatestRequest()

  // The traversal returns the subject among its own elements, so the screen
  // knows its name without asking `/elements/{id}` a second time.
  const subject = computed<ElementRead | null>(
    () => graph.value.elements.find((element) => element.id === focusId.value) ?? null,
  )

  /** Everything around the subject — the count that says whether it is alone. */
  const neighbours = computed(() => graph.value.elements.length - (subject.value ? 1 : 0))

  async function explore(elementId: string, question: Question): Promise<void> {
    focusId.value = elementId
    await traversal.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}/neighbourhood', {
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
            signal,
          }),
        ),
      (answer) => {
        graph.value = answer
      },
      () => {
        graph.value = EMPTY
      },
    )
  }

  function clear(): void {
    traversal.cancel()
    focusId.value = ''
    graph.value = EMPTY
  }

  const { status, error } = traversal

  return { focusId, graph, subject, neighbours, status, error, explore, clear }
}
