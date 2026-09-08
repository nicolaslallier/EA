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

export type SubnetRead = components['schemas']['SubnetRead']
export type SubnetDetailRead = components['schemas']['SubnetDetailRead']
export type AddressRead = components['schemas']['AddressRead']
export type AddressLocationRead = components['schemas']['AddressLocationRead']
export type SubnetCreate = components['schemas']['SubnetCreate']

/** The scope an address is unique in. One flat network is still a scope. */
export const DEFAULT_VRF = 'default'

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function useIpam() {
  const subnets = ref<SubnetRead[]>([])
  const detail = ref<SubnetDetailRead | null>(null)
  const located = ref<AddressLocationRead | null>(null)
  const status = ref<Status>('idle')
  const error = ref('')
  /** The last write's refusal, kept apart so a failed form never blanks the table. */
  const refusal = ref('')

  /** More than one scope in the model is what makes the scope worth showing. */
  const scopes = computed(() => [...new Set(subnets.value.map((subnet) => subnet.vrf))].sort())

  async function loadSubnets(vrf = ''): Promise<void> {
    status.value = 'loading'
    error.value = ''
    try {
      subnets.value = unwrap(
        await api.GET('/ipam/subnets', { params: { query: vrf ? { vrf } : {} } }),
      )
      status.value = 'ready'
    } catch (caught) {
      error.value = messageOf(caught)
      status.value = 'error'
    }
  }

  /** Open one subnet, or close the one open — `''` is "none chosen". */
  async function openSubnet(elementId: string): Promise<void> {
    refusal.value = ''
    if (!elementId) {
      detail.value = null
      return
    }
    try {
      detail.value = unwrap(
        await api.GET('/ipam/subnets/{subnet_id}', {
          params: { path: { subnet_id: elementId } },
        }),
      )
    } catch (caught) {
      detail.value = null
      error.value = messageOf(caught)
    }
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
      located.value = null
      return
    }
    try {
      located.value = unwrap(
        await api.GET('/ipam/addresses/{address}', {
          params: { path: { address }, query: { vrf } },
        }),
      )
    } catch (caught) {
      located.value = null
      if (!(caught instanceof ApiError && caught.status === 404)) {
        error.value = messageOf(caught)
      }
    }
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
