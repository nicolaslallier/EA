// The saved diagrams: the list, and the two writes that change it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type DiagramSummaryRead = components['schemas']['DiagramSummaryRead']

export function useDiagrams() {
  const diagrams = ref<DiagramSummaryRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/diagrams', { signal })),
      (answer) => {
        diagrams.value = answer
      },
    )
  }

  /** A refusal — a name already taken — is thrown, for the form to show. */
  async function create(name: string): Promise<DiagramSummaryRead> {
    const created = unwrap(await api.POST('/diagrams', { body: { name } }))
    await load()
    return created
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/diagrams/{diagram_id}', { params: { path: { diagram_id: id } } }))
    await load()
  }

  return { diagrams, status, error, load, create, remove }
}
