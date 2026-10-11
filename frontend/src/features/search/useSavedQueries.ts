// The shared, named filters: the list, the one opened, and the writes.
//
// Two questions that must not cancel each other: the list, and the query being
// opened. An opened query the API cannot read (`invalid_query`) is a message in
// `openError`; the list, which never reads a query, stays usable around it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { Query } from './queryState'

export type SavedQuerySummaryRead = components['schemas']['SavedQuerySummaryRead']
export type SavedQueryRead = components['schemas']['SavedQueryRead']
export type SavedQueryUpdate = components['schemas']['SavedQueryUpdate']

export function useSavedQueries() {
  const queries = ref<SavedQuerySummaryRead[]>([])
  const opened = ref<SavedQueryRead | null>(null)
  const listing = useLatestRequest()
  const opening = useLatestRequest()
  const { status, error } = listing

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/queries', { signal })),
      (answer) => {
        queries.value = answer
      },
    )
  }

  async function open(id: string): Promise<void> {
    // The previous query goes first: showing it under another id would show the wrong filter.
    opened.value = null
    await opening.run(
      async (signal) =>
        unwrap(
          await api.GET('/queries/{query_id}', { params: { path: { query_id: id } }, signal }),
        ),
      (answer) => {
        opened.value = answer
      },
    )
  }

  function close(): void {
    opening.cancel()
    opened.value = null
  }

  /** A refusal — a name already taken — is thrown, for the form to show. */
  async function create(name: string, description: string, query: Query): Promise<SavedQueryRead> {
    const created = unwrap(await api.POST('/queries', { body: { name, description, query } }))
    await load()
    return created
  }

  async function update(id: string, body: SavedQueryUpdate): Promise<SavedQueryRead> {
    const updated = unwrap(
      await api.PATCH('/queries/{query_id}', { params: { path: { query_id: id } }, body }),
    )
    await load()
    return updated
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/queries/{query_id}', { params: { path: { query_id: id } } }))
    await load()
  }

  return {
    queries,
    opened,
    status,
    error,
    openStatus: opening.status,
    openError: opening.error,
    load,
    open,
    close,
    create,
    update,
    remove,
  }
}
