// Every stereotype, flat: what the element panel offers and the canvas draws from.
import { ref } from 'vue'

import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { StereotypeRead } from './useProfiles'

export function useStereotypes() {
  const all = ref<StereotypeRead[]>([])
  const listing = useLatestRequest()

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/stereotypes', { signal })),
      (answer) => {
        all.value = answer
      },
    )
  }

  /** The stereotypes made for this element type — the only ones the API will accept. */
  function forType(elementType: string): StereotypeRead[] {
    return all.value.filter((stereotype) => stereotype.base_type === elementType)
  }

  return { all, load, forType }
}
