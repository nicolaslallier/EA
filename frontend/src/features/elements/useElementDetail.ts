// The element behind an id: one read, and the state of that read.
//
// The catalogue already holds the rows it displays, but the detail is not
// taken from one: a link naming an element may be opened on a page — or with
// filters — that does not contain it, and a row rendered minutes ago may be
// stale. Asking the API for the element keeps one code path, whichever way the
// detail was opened.
import { ref } from 'vue'

import { api, messageOf, unwrap } from '../../lib/api'
import type { ElementRead } from './useElementCatalogue'

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function useElementDetail() {
  const element = ref<ElementRead | null>(null)
  const status = ref<Status>('idle')
  const error = ref('')

  async function open(id: string): Promise<void> {
    // The previous element goes first: showing it under the new id, even for
    // the length of one request, would be showing the wrong element.
    element.value = null
    status.value = 'loading'
    error.value = ''
    try {
      element.value = unwrap(
        await api.GET('/elements/{element_id}', { params: { path: { element_id: id } } }),
      )
      status.value = 'ready'
    } catch (caught) {
      error.value = messageOf(caught)
      status.value = 'error'
    }
  }

  function close(): void {
    element.value = null
    status.value = 'idle'
    error.value = ''
  }

  return { element, status, error, open, close }
}
