import { describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'

import { ApiError, UNREACHABLE } from '../src/lib/api'
import { useLatestRequest } from '../src/lib/latest'

/** A promise the test settles by hand, in whatever order it wants. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

/** A task that honours its signal exactly as `fetch` does: rejects on abort. */
function abortable<T>(answer: Promise<T>) {
  return (signal: AbortSignal) =>
    new Promise<T>((resolve, reject) => {
      // `abort()` without an argument always rejects with a DOMException.
      signal.addEventListener('abort', () => reject(signal.reason as Error))
      answer.then(resolve, reject)
    })
}

describe('useLatestRequest', () => {
  it('applies the answer and says it is ready', async () => {
    const request = useLatestRequest()
    const apply = vi.fn()

    await request.run(() => Promise.resolve('answer'), apply)

    expect(apply).toHaveBeenCalledWith('answer')
    expect(request.status.value).toBe('ready')
    expect(request.error.value).toBe('')
  })

  it('keeps the latest question when the answers arrive out of order', async () => {
    const request = useLatestRequest()
    const applied: string[] = []
    const first = deferred<string>()
    const second = deferred<string>()

    const one = request.run(abortable(first.promise), (value) => applied.push(value))
    const two = request.run(abortable(second.promise), (value) => applied.push(value))
    second.resolve('second')
    first.resolve('first')
    await Promise.all([one, two])

    expect(applied).toEqual(['second'])
    expect(request.status.value).toBe('ready')
  })

  it('ignores a superseded answer even from a task that never looked at its signal', async () => {
    const request = useLatestRequest()
    const applied: string[] = []
    const first = deferred<string>()
    const second = deferred<string>()

    const one = request.run(() => first.promise, (value) => applied.push(value))
    const two = request.run(() => second.promise, (value) => applied.push(value))
    second.resolve('second')
    await two
    first.resolve('first')
    await one

    expect(applied).toEqual(['second'])
  })

  it('aborts the request it supersedes, so the browser stops waiting for it', async () => {
    const request = useLatestRequest()
    let firstSignal: AbortSignal | undefined

    void request.run(
      (signal) => {
        firstSignal = signal
        return new Promise<never>(() => {})
      },
      () => {},
    )
    await request.run(() => Promise.resolve('second'), () => {})

    expect(firstSignal?.aborted).toBe(true)
  })

  it('never reports a superseded request as a failure', async () => {
    const request = useLatestRequest()
    const onFailure = vi.fn()
    const first = deferred<string>()
    const second = deferred<string>()

    const one = request.run(abortable(first.promise), () => {}, onFailure)
    const two = request.run(abortable(second.promise), () => {}, onFailure)
    // The first rejects with the AbortError its own signal raised, and is
    // still waiting to be noticed while the second is in flight.
    await one
    expect(request.error.value).toBe('')
    expect(request.status.value).toBe('loading')

    second.resolve('second')
    await two

    expect(onFailure).not.toHaveBeenCalled()
    expect(request.error.value).toBe('')
    expect(request.status.value).toBe('ready')
  })

  it("keeps a later failure from being hidden by an earlier call's success", async () => {
    const request = useLatestRequest()
    const first = deferred<string>()
    const second = deferred<string>()
    const apply = vi.fn()

    const one = request.run(() => first.promise, apply)
    const two = request.run(() => second.promise, apply)
    second.reject(new ApiError('Refusé.', 'refused', 409))
    await two
    first.resolve('first')
    await one

    expect(apply).not.toHaveBeenCalled()
    expect(request.status.value).toBe('error')
    expect(request.error.value).toBe('Refusé.')
  })

  it('reports a failure in words, and lets the caller forget what it showed', async () => {
    const request = useLatestRequest()
    const onFailure = vi.fn()

    await request.run(
      () => Promise.reject(new TypeError('Failed to fetch')),
      () => {},
      onFailure,
    )

    expect(request.status.value).toBe('error')
    expect(request.error.value).toBe(UNREACHABLE)
    expect(onFailure).toHaveBeenCalledOnce()
  })

  it('clears the previous error as soon as a new question is asked', async () => {
    const request = useLatestRequest()
    await request.run(
      () => Promise.reject(new ApiError('Refusé.', 'refused', 409)),
      () => {},
    )

    const pending = request.run(abortable(new Promise<never>(() => {})), () => {})

    expect(request.error.value).toBe('')
    expect(request.status.value).toBe('loading')
    request.cancel()
    await pending
  })

  it('drops whatever is in flight on cancel, and goes back to idle', async () => {
    const request = useLatestRequest()
    const answer = deferred<string>()
    const apply = vi.fn()

    const pending = request.run(abortable(answer.promise), apply)
    request.cancel()
    answer.resolve('late')
    await pending

    expect(apply).not.toHaveBeenCalled()
    expect(request.status.value).toBe('idle')
    expect(request.error.value).toBe('')
  })

  it('aborts what is in flight when the component that asked goes away', async () => {
    const scope = effectScope()
    const request = scope.run(() => useLatestRequest())!
    let seen: AbortSignal | undefined

    const pending = request.run(
      (signal) => {
        seen = signal
        return new Promise<never>((_, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason as Error))
        })
      },
      () => {},
    )
    scope.stop()
    await pending

    expect(seen?.aborted).toBe(true)
    expect(request.error.value).toBe('')
  })
})
