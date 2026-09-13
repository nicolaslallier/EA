// The relations of one element: what it is linked to, and the two writes that
// change that.
//
// A relationship carries the *ids* and the *types* of its endpoints, never
// their names — a name belongs to the element and changes without the link.
// So the panel does not read `/relationships`, which would need one extra
// request per row to render anything; it reads `/elements/{id}/relationships`,
// which answers with the links and the elements at both ends in one response.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type ElementRead = components['schemas']['ElementRead']
export type RelationshipRead = components['schemas']['RelationshipRead']
export type RelationshipCreate = components['schemas']['RelationshipCreate']
export type RelationshipType = components['schemas']['RelationshipType']
export type ElementType = components['schemas']['ElementType']
export type AccessType = components['schemas']['AccessType']

/** How many candidates a target search offers before asking for a narrower term. */
export const CANDIDATE_LIMIT = 20

/** Shown in place of a name the response did not carry — never a raw uuid. */
export const UNKNOWN_ENDPOINT = 'élément inconnu'

export function useElementRelationships() {
  /** The element the panel is about; null when it is closed. */
  const subject = ref<ElementRead | null>(null)
  const links = ref<RelationshipRead[]>([])
  /** Every element named by a link, the subject included. */
  const endpoints = ref<ElementRead[]>([])
  /** The relationships the metamodel allows for the pair currently being built. */
  const permitted = ref<RelationshipType[]>([])
  const candidates = ref<ElementRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing
  // The form asks two questions of its own, each as fast as the user changes
  // a field — a keystroke, a direction flipped — so each keeps only its latest
  // answer, and neither cancels the other or the list of links.
  const permission = useLatestRequest()
  const search = useLatestRequest()
  /** Why the form cannot offer a choice right now; '' while it can. */
  const lookupError = computed(() => permission.error.value || search.error.value)

  const names = computed(
    () => new Map(endpoints.value.map((element) => [element.id, element.name])),
  )

  /** The name of an endpoint, resolved from the same response that carried the link. */
  function nameOf(id: string): string {
    return names.value.get(id) ?? UNKNOWN_ENDPOINT
  }

  async function load(): Promise<void> {
    const element = subject.value
    if (!element) {
      return
    }
    await listing.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}/relationships', {
            params: { path: { element_id: element.id } },
            signal,
          }),
        ),
      (view) => {
        links.value = view.relationships
        endpoints.value = view.elements
      },
    )
  }

  /** Point the panel at an element and fetch its links. */
  async function open(element: ElementRead): Promise<void> {
    subject.value = element
    links.value = []
    endpoints.value = []
    search.cancel()
    candidates.value = []
    clearPermitted()
    await load()
  }

  function close(): void {
    listing.cancel()
    search.cancel()
    permission.cancel()
    subject.value = null
  }

  /**
   * Ask which relationships may run from one element type to another.
   *
   * The answer comes from the same rules the API validates with, so the form
   * offers exactly what will be accepted rather than a list of eleven types of
   * which nine would be refused.
   */
  async function loadPermitted(source: ElementType, target: ElementType): Promise<void> {
    await permission.run(
      async (signal) =>
        unwrap(
          await api.GET('/metamodel/relationships', {
            params: { query: { source, target } },
            signal,
          }),
        ),
      (answer) => {
        permitted.value = answer
      },
      () => {
        permitted.value = []
      },
    )
  }

  /** Forget the pair being built, including an answer about it still on the way. */
  function clearPermitted(): void {
    permission.cancel()
    permitted.value = []
  }

  /** Candidate other ends, searched server-side — the graph outgrows one page. */
  async function searchCandidates(term: string): Promise<void> {
    await search.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements', {
            params: { query: { limit: CANDIDATE_LIMIT, ...(term ? { search: term } : {}) } },
            signal,
          }),
        ),
      (page) => {
        // ArchiMate permits a self-association, but the panel does not offer
        // one: "associate X with X" is far more often a mis-click than an
        // intention, and the API stays free to accept it from another client.
        candidates.value = page.items.filter((element) => element.id !== subject.value?.id)
      },
      () => {
        // Candidates found for an earlier term are not candidates for this one.
        candidates.value = []
      },
    )
  }

  async function connect(payload: RelationshipCreate): Promise<void> {
    unwrap(await api.POST('/relationships', { body: payload }))
    // Reload rather than push: the stored link carries the server's id, which
    // is what the delete button needs.
    await load()
  }

  async function disconnect(relationshipId: string): Promise<void> {
    unwrap(
      await api.DELETE('/relationships/{relationship_id}', {
        params: { path: { relationship_id: relationshipId } },
      }),
    )
    await load()
  }

  return {
    subject,
    links,
    endpoints,
    permitted,
    candidates,
    status,
    error,
    lookupError,
    nameOf,
    open,
    close,
    load,
    loadPermitted,
    clearPermitted,
    searchCandidates,
    connect,
    disconnect,
  }
}
