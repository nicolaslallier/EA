// The IPAM screen's state: the subnets, one of them in detail, and one
// address looked up.
//
// Not one rule lives here. Which addresses a prefix keeps for itself, which
// element types may answer on one, what the next free address is — all of it
// is the API's, and this module asks. A percentage computed in the browser
// would be a second answer to a question the server has already answered, free
// to disagree with it; the counts come down the wire instead.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, messageOf, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type SubnetRead = components['schemas']['SubnetRead']
export type SubnetDetailRead = components['schemas']['SubnetDetailRead']
export type AddressRead = components['schemas']['AddressRead']
export type AddressLocationRead = components['schemas']['AddressLocationRead']
export type SubnetCreate = components['schemas']['SubnetCreate']

/** The scope an address is unique in. One flat network is still a scope. */
export const DEFAULT_VRF = 'default'

export function useIpam() {
  const subnets = ref<SubnetRead[]>([])
  const detail = ref<SubnetDetailRead | null>(null)
  const located = ref<AddressLocationRead | null>(null)
  // Three questions, each answered on its own schedule, so three guards: the
  // scope turned, the subnet opened and the address typed must not cancel one
  // another — only a newer version of themselves.
  const listing = useLatestRequest()
  const opening = useLatestRequest()
  const lookup = useLatestRequest()
  const status = listing.status
  /**
   * What the banner says: the first of the three questions whose latest answer
   * was a failure. Each clears its own part when it is asked again, so a lookup
   * that failed once never keeps the banner up over the one that succeeded.
   */
  const error = computed(
    () => listing.error.value || opening.error.value || lookup.error.value,
  )
  /** The last write's refusal, kept apart so a failed form never blanks the table. */
  const refusal = ref('')

  /** More than one scope in the model is what makes the scope worth showing. */
  const scopes = computed(() => [...new Set(subnets.value.map((subnet) => subnet.vrf))].sort())

  async function loadSubnets(vrf = ''): Promise<void> {
    await listing.run(
      async (signal) =>
        unwrap(await api.GET('/ipam/subnets', { params: { query: vrf ? { vrf } : {} }, signal })),
      (answer) => {
        subnets.value = answer
      },
    )
  }

  /** Open one subnet, or close the one open — `''` is "none chosen". */
  async function openSubnet(elementId: string): Promise<void> {
    refusal.value = ''
    if (!elementId) {
      // Closing is a question too: a subnet still loading must not reopen.
      opening.cancel()
      detail.value = null
      return
    }
    await opening.run(
      async (signal) =>
        unwrap(
          await api.GET('/ipam/subnets/{subnet_id}', {
            params: { path: { subnet_id: elementId } },
            signal,
          }),
        ),
      (answer) => {
        detail.value = answer
      },
      () => {
        detail.value = null
      },
    )
  }

  /**
   * Look one address up, and say plainly when nothing holds it.
   *
   * A 404 here is an answer rather than a failure — "nothing has this address"
   * is exactly what somebody typing one into the box wants to know — so it
   * clears the card instead of raising a banner.
   */
  async function locate(address: string, vrf = DEFAULT_VRF): Promise<void> {
    refusal.value = ''
    if (!address) {
      lookup.cancel()
      located.value = null
      return
    }
    await lookup.run(
      async (signal) => {
        try {
          return unwrap(
            await api.GET('/ipam/addresses/{address}', {
              params: { path: { address }, query: { vrf } },
              signal,
            }),
          )
        } catch (caught) {
          if (caught instanceof ApiError && caught.status === 404) {
            return null
          }
          throw caught
        }
      },
      (answer) => {
        located.value = answer
      },
      () => {
        located.value = null
      },
    )
  }

  /** Run a write, keeping its refusal readable instead of throwing it away. */
  async function attempt(write: () => Promise<void>): Promise<boolean> {
    refusal.value = ''
    try {
      await write()
      return true
    } catch (caught) {
      refusal.value = messageOf(caught)
      return false
    }
  }

  async function declareSubnet(payload: SubnetCreate): Promise<boolean> {
    return attempt(async () => {
      unwrap(await api.POST('/ipam/subnets', { body: payload }))
      await loadSubnets()
    })
  }

  async function allocate(subnetId: string, elementId: string): Promise<boolean> {
    return attempt(async () => {
      unwrap(
        await api.POST('/ipam/subnets/{subnet_id}/allocate', {
          params: { path: { subnet_id: subnetId } },
          body: { element_id: elementId },
        }),
      )
      await refresh(subnetId)
    })
  }

  async function assign(elementId: string, address: string, vrf = DEFAULT_VRF): Promise<boolean> {
    return attempt(async () => {
      unwrap(
        await api.POST('/ipam/addresses', {
          body: { element_id: elementId, address, vrf },
        }),
      )
      await refresh(detail.value?.subnet.element_id)
    })
  }

  async function release(elementId: string): Promise<boolean> {
    return attempt(async () => {
      unwrap(
        await api.DELETE('/ipam/elements/{element_id}/address', {
          params: { path: { element_id: elementId } },
        }),
      )
      await refresh(detail.value?.subnet.element_id)
    })
  }

  /** Re-read what a write changed: the counts on every subnet, and the one open. */
  async function refresh(subnetId?: string): Promise<void> {
    await loadSubnets()
    if (subnetId) {
      await openSubnet(subnetId)
    }
  }

  return {
    subnets,
    detail,
    located,
    scopes,
    status,
    error,
    refusal,
    loadSubnets,
    openSubnet,
    locate,
    declareSubnet,
    allocate,
    assign,
    release,
  }
}
