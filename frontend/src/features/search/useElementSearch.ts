// The results of a search: one page of elements, and the total of the whole.
//
// A failure — a filter the API refuses — lands in `error` rather than being
// thrown: the person fixes the filter on the same screen. Asking again drops
// the question in flight, so the table is the answer to the last one.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { Query } from './queryState'

export type ElementRead = components['schemas']['ElementRead']
type ElementPage = components['schemas']['ElementPage']

export function useElementSearch() {
  const items = ref<ElementRead[]>([])
  const total = ref(0)
  const asking = useLatestRequest()
  const { status, error } = asking

  function show(page: ElementPage): void {
    items.value = page.items
    total.value = page.total
  }

  function forget(): void {
    items.value = []
    total.value = 0
  }

  /** A filter built on screen or read from an address. */
  async function run(query: Query): Promise<void> {
    await asking.run(
      async (signal) => unwrap(await api.POST('/elements/search', { body: query, signal })),
      show,
      forget,
    )
  }

  /** A saved query, by its own endpoint: that is where a vanished package is explained. */
  async function runSaved(id: string, limit: number, offset: number): Promise<void> {
    await asking.run(
      async (signal) =>
        unwrap(
          await api.GET('/queries/{query_id}/elements', {
            params: { path: { query_id: id }, query: { limit, offset } },
            signal,
          }),
        ),
      show,
      forget,
    )
  }

  function clear(): void {
    asking.cancel()
    forget()
  }

  return { items, total, status, error, run, runSaved, clear }
}
