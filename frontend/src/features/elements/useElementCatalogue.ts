// The catalogue screen's state: one page of elements, the filters that
// produced it, and the four writes.
//
// Filtering and pagination are the server's job — the API takes `search`,
// `element_type`, `layer`, `limit` and `offset`, and reports the total. Doing
// either in the browser would only work until the graph outgrew one page.
import { computed, reactive, ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, messageOf, unwrap } from '../../lib/api'

export type ElementRead = components['schemas']['ElementRead']
export type ElementCreate = components['schemas']['ElementCreate']
export type ElementUpdate = components['schemas']['ElementUpdate']
export type ElementType = components['schemas']['ElementType']
export type Layer = components['schemas']['Layer']

export const PAGE_SIZE = 25

export type Filters = {
  search: string
  elementType: ElementType | ''
  layer: Layer | ''
}

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function useElementCatalogue() {
  const items = ref<ElementRead[]>([])
  const total = ref(0)
  const page = ref(0)
  const status = ref<Status>('idle')
  const error = ref('')
  const filters = reactive<Filters>({ search: '', elementType: '', layer: '' })

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
  const hasNextPage = computed(() => (page.value + 1) * PAGE_SIZE < total.value)
  const hasPreviousPage = computed(() => page.value > 0)

  async function load(): Promise<void> {
    status.value = 'loading'
    error.value = ''
    try {
      const result = unwrap(
        await api.GET('/elements', {
          params: {
            // An empty filter is left out rather than sent blank: `search=`
            // would be a filter matching nothing, not the absence of one.
            query: {
              limit: PAGE_SIZE,
              offset: page.value * PAGE_SIZE,
              ...(filters.search ? { search: filters.search } : {}),
              ...(filters.elementType ? { element_type: [filters.elementType] } : {}),
              ...(filters.layer ? { layer: [filters.layer] } : {}),
            },
          },
        }),
      )
      items.value = result.items
      total.value = result.total
      status.value = 'ready'
    } catch (caught) {
      error.value = messageOf(caught)
      status.value = 'error'
    }
  }

  /** Re-run the query from the first page — what a changed filter means. */
  async function search(): Promise<void> {
    page.value = 0
    await load()
  }

  function goTo(target: number): void {
    page.value = Math.max(0, target)
  }

  async function create(payload: ElementCreate): Promise<ElementRead> {
    const created = unwrap(await api.POST('/elements', { body: payload }))
    // Reload rather than push: the stored element carries the server's id and
    // timestamps, and it may not belong on the page currently displayed.
    await load()
    return created
  }

  async function update(id: string, payload: ElementUpdate): Promise<ElementRead> {
    const updated = unwrap(
      await api.PATCH('/elements/{element_id}', {
        params: { path: { element_id: id } },
        body: payload,
      }),
    )
    await load()
    return updated
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/elements/{element_id}', { params: { path: { element_id: id } } }))
    // Deleting the last row of the last page would otherwise leave the user on
    // a page that no longer exists, staring at an empty table.
    if (items.value.length === 1 && page.value > 0) {
      page.value -= 1
    }
    await load()
  }

  return {
    items,
    total,
    page,
    pageCount,
    hasNextPage,
    hasPreviousPage,
    status,
    error,
    filters,
    load,
    search,
    goTo,
    create,
    update,
    remove,
    ApiError,
  }
}
