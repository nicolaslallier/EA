// The saved diagrams: the list, the filter that produced it, and the writes.
import { reactive, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { ROOT } from '../packages/packageTree'

export type DiagramSummaryRead = components['schemas']['DiagramSummaryRead']

export function useDiagrams() {
  const diagrams = ref<DiagramSummaryRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing
  /** `package`: `''` all, `'root'` the unfiled, else a package id — the catalogue's convention. */
  const filters = reactive({ package: '', includeSubpackages: true })

  async function load(): Promise<void> {
    const query = {
      ...(filters.package === ROOT ? { root_only: true } : {}),
      ...(filters.package && filters.package !== ROOT ? { package: filters.package } : {}),
      ...(filters.package && filters.package !== ROOT && !filters.includeSubpackages
        ? { include_subpackages: false }
        : {}),
    }
    await listing.run(
      async (signal) => unwrap(await api.GET('/diagrams', { params: { query }, signal })),
      (answer) => {
        diagrams.value = answer
      },
    )
  }

  /** A refusal — a name already taken — is thrown, for the form to show. */
  async function create(name: string, packageId?: string): Promise<DiagramSummaryRead> {
    const created = unwrap(
      await api.POST('/diagrams', {
        body: { name, ...(packageId ? { package_id: packageId } : {}) },
      }),
    )
    await load()
    return created
  }

  /** `packageId: null` puts the diagram at the root; the name is left alone. */
  async function move(id: string, packageId: string | null): Promise<void> {
    unwrap(
      await api.PATCH('/diagrams/{diagram_id}', {
        params: { path: { diagram_id: id } },
        body: { package_id: packageId },
      }),
    )
    await load()
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/diagrams/{diagram_id}', { params: { path: { diagram_id: id } } }))
    await load()
  }

  return { diagrams, status, error, filters, load, create, move, remove }
}
