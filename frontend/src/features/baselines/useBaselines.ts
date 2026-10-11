// Baselines (docs/adr/0047): the list, the capture, the diff, the restoration.
//
// The list and the diff are two independent questions, so they do not cancel
// each other (`useLatestRequest`, one instance each). A write — a capture, a
// deletion, a restoration — raises on refusal: the refusal is the screen's to
// show, not this composable's state.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type Baseline = components['schemas']['BaselineRead']
export type BaselineDiff = components['schemas']['BaselineDiffRead']
export type RestoreRequest = components['schemas']['RestoreRequest']
export type RestoreResult = components['schemas']['RestoreRead']

export function useBaselines() {
  const baselines = ref<Baseline[]>([])
  const diff = ref<BaselineDiff | null>(null)
  const listing = useLatestRequest()
  const comparing = useLatestRequest()

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/baselines', { signal })),
      (answer) => {
        baselines.value = answer
      },
    )
  }

  async function capture(name: string, description: string): Promise<void> {
    unwrap(await api.POST('/baselines', { body: { name, description } }))
    await load()
  }

  async function remove(id: string): Promise<void> {
    unwrap(
      await api.DELETE('/baselines/{baseline_id}', {
        params: { path: { baseline_id: id } },
      }),
    )
    await load()
  }

  /** `against` absent = the model as it is now. */
  async function compare(id: string, against?: string): Promise<void> {
    diff.value = null
    await comparing.run(
      async (signal) =>
        unwrap(
          await api.GET('/baselines/{baseline_id}/diff', {
            params: { path: { baseline_id: id }, query: { against } },
            signal,
          }),
        ),
      (answer) => {
        diff.value = answer
      },
    )
  }

  /** Forget the diff and whatever was being computed for it. */
  function forgetDiff(): void {
    comparing.cancel()
    diff.value = null
  }

  /** A dry run when `request.dry_run` is true (the plan, nothing written); else the restoration. */
  async function restore(
    id: string,
    request: RestoreRequest,
    signal?: AbortSignal,
  ): Promise<RestoreResult> {
    return unwrap(
      await api.POST('/baselines/{baseline_id}/restore', {
        params: { path: { baseline_id: id } },
        body: request,
        signal,
      }),
    )
  }

  return {
    baselines,
    status: listing.status,
    error: listing.error,
    diff,
    diffStatus: comparing.status,
    diffError: comparing.error,
    load,
    capture,
    remove,
    compare,
    forgetDiff,
    restore,
  }
}
