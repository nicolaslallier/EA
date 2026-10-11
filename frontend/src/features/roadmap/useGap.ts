// Two plateaus and what separates them.
//
// The difference is the server's to compute — departures, arrivals, what stays,
// and the gaps already drawn — because membership of a plateau is a graph
// question (docs/adr/0061). Here: the list to choose from, the answer for one
// pair, and the one write (draw the gap). Asking again drops the answer still
// in flight, so the screen shows the pair chosen last.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type PlateauRead = components['schemas']['PlateauRead']
export type PlateauGapRead = components['schemas']['PlateauGapRead']
export type ElementRead = components['schemas']['ElementRead']

export function useGap() {
  const plateaus = ref<PlateauRead[]>([])
  const comparison = ref<PlateauGapRead | null>(null)
  const listing = useLatestRequest()
  const comparing = useLatestRequest()
  const creating = ref(false)
  const createError = ref('')

  async function loadPlateaus(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/plateaus', { signal })),
      (answer) => {
        plateaus.value = answer
      },
    )
  }

  async function compare(a: string, b: string): Promise<void> {
    await comparing.run(
      async (signal) =>
        unwrap(await api.GET('/plateaus/gap', { params: { query: { a, b } }, signal })),
      (answer) => {
        comparison.value = answer
      },
      () => {
        comparison.value = null
      },
    )
  }

  function clear(): void {
    comparing.cancel()
    comparison.value = null
  }

  async function createGap(a: string, b: string): Promise<void> {
    creating.value = true
    createError.value = ''
    try {
      unwrap(await api.POST('/plateaus/gap', { body: { a, b } }))
      await compare(a, b)
    } catch (caught) {
      createError.value = messageOf(caught)
    } finally {
      creating.value = false
    }
  }

  return {
    plateaus,
    comparison,
    status: comparing.status,
    error: comparing.error,
    loadPlateaus,
    compare,
    clear,
    createGap,
    creating,
    createError,
  }
}
