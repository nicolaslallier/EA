// Moving an element through review. A refusal (403, 409, 422) is the banner's to
// show, so a write is thrown rather than kept in a state; the re-read after a
// stale approval is a question like any other and goes through `useLatestRequest`.
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { Transition } from './approvalLabels'
import type { ElementRead } from './useElementCatalogue'

export function useApproval() {
  const read = useLatestRequest()

  /**
   * `revision` is the one the approver read: only an approval carries it, since
   * only an approval certifies content (the API answers 409 `approval_stale`
   * when the element has moved on since).
   */
  async function transition(id: string, next: Transition, revision?: number): Promise<ElementRead> {
    return unwrap(
      await api.POST('/elements/{element_id}/approval', {
        params: { path: { element_id: id } },
        body:
          next === 'approve'
            ? { transition: next, expected_revision: revision }
            : { transition: next },
      }),
    )
  }

  /** The element as it is now, or `null` if a newer question replaced this one or it failed. */
  async function reread(id: string): Promise<ElementRead | null> {
    let fresh: ElementRead | null = null
    await read.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}', {
            params: { path: { element_id: id } },
            signal,
          }),
        ),
      (answer) => {
        fresh = answer
      },
    )
    return fresh
  }

  return { transition, reread }
}
