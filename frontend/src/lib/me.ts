// Who is logged in, and whether the SPA should offer to change anything.
//
// The API decides (docs/adr/0031): this only asks `GET /me` — once per page
// load, shared by every screen — and hides what the answer says the user may
// not do. Until the answer arrives nothing is offered, so a reader never sees
// a button flash before it disappears.
import { computed, readonly, ref } from 'vue'

import type { components } from '../api/schema'
import { api, messageOf, unwrap } from './api'

type MeRead = components['schemas']['MeRead']

const me = ref<MeRead | null>(null)
const error = ref<string | null>(null)
let pending: Promise<void> | null = null

// A standalone function referenced by the shorthand below, not a method
// written inline in the returned object: the latter is a *method signature*
// to TypeScript, and @typescript-eslint/unbound-method then flags every
// caller that destructures it — the same reason every other composable here
// returns functions this way (see `useElementCatalogue`).
function load(): Promise<void> {
  pending ??= api
    .GET('/me')
    .then((result) => {
      me.value = unwrap(result)
      error.value = null
    })
    .catch((failure: unknown) => {
      error.value = messageOf(failure)
      pending = null
    })
  return pending
}

export function useMe() {
  return {
    me: readonly(me),
    error: readonly(error),
    canWrite: computed(() => me.value?.can_write === true),
    load,
  }
}

/** Forget the answer — for tests, and nothing else. */
export function resetMe(): void {
  me.value = null
  error.value = null
  pending = null
}
