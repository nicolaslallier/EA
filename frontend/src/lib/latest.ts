// One question at a time: the answer on screen is the answer to the question
// asked last.
//
// Every screen here reads its question from something that changes faster
// than the network answers — a URL a click rewrites, a box being typed in, a
// select turned twice. Two requests in flight then race, and without a guard
// the slower one wins: click two neighbours in quick succession and the
// drawing ends up centred on the first while `?element=` names the second.
//
// So a load goes through `useLatestRequest`. Asking again aborts the request
// in flight — the browser stops waiting for it — and whatever it still
// produces, answer or failure, is dropped. An abort is never an error: it is
// what the user asked for by asking something else.
import { getCurrentScope, onScopeDispose, ref } from 'vue'

import { messageOf } from './api'

export type Status = 'idle' | 'loading' | 'ready' | 'error'

/**
 * The state of one kind of question, and the only way to ask it.
 *
 * `run(task, apply, onFailure?)` starts `task` with a fresh `AbortSignal`, to
 * hand to `openapi-fetch` as `{ signal }`. If no later `run` or `cancel` came
 * in the meantime, `apply` receives the answer and `status` becomes `ready`;
 * a failure becomes `error` in words, after `onFailure` has let the caller
 * forget what it was showing. A superseded call touches nothing at all.
 *
 * One instance per independent question: the subnet list and the subnet
 * opened beside it must not cancel each other.
 */
export function useLatestRequest() {
  const status = ref<Status>('idle')
  const error = ref('')
  let current: AbortController | null = null

  async function run<T>(
    task: (signal: AbortSignal) => Promise<T>,
    apply: (value: T) => void,
    onFailure?: (caught: unknown) => void,
  ): Promise<void> {
    current?.abort()
    const mine = new AbortController()
    current = mine
    status.value = 'loading'
    error.value = ''
    try {
      const value = await task(mine.signal)
      // Checked after the await and not trusted to the abort alone: a task
      // that ignored its signal still resolves, and must still lose.
      if (mine.signal.aborted) {
        return
      }
      apply(value)
      status.value = 'ready'
    } catch (caught) {
      if (mine.signal.aborted) {
        return
      }
      onFailure?.(caught)
      error.value = messageOf(caught)
      status.value = 'error'
    } finally {
      if (current === mine) {
        current = null
      }
    }
  }

  /** Drop whatever is in flight and go back to having asked nothing. */
  function cancel(): void {
    current?.abort()
    current = null
    status.value = 'idle'
    error.value = ''
  }

  // A component that goes away takes its questions with it; outside one — a
  // composable exercised directly by a spec — there is no scope to follow.
  if (getCurrentScope()) {
    onScopeDispose(cancel)
  }

  return { status, error, run, cancel }
}
