// The element behind an id: one read, and the state of that read.
//
// The catalogue already holds the rows it displays, but the detail is not
// taken from one: a link naming an element may be opened on a page — or with
// filters — that does not contain it, and a row rendered minutes ago may be
// stale. Asking the API for the element keeps one code path, whichever way the
// detail was opened.
import { ref } from 'vue'

import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { ElementRead } from './useElementCatalogue'

export function useElementDetail() {
  const element = ref<ElementRead | null>(null)
  // `?element=` changes with the back button as fast as with a click, and the
  // detail must name the element the URL names — not the slowest answer.
  const read = useLatestRequest()
  const { status, error } = read

  async function open(id: string): Promise<void> {
    // The previous element goes first: showing it under the new id, even for
    // the length of one request, would be showing the wrong element.
    element.value = null
    await read.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}', {
            params: { path: { element_id: id } },
            signal,
          }),
        ),
      (answer) => {
        element.value = answer
      },
    )
  }

  function close(): void {
    read.cancel()
    element.value = null
  }

  return { element, status, error, open, close }
}
