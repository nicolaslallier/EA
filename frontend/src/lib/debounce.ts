// Wait for a burst to stop before acting on it.
//
// A search box fires on every keystroke, and "s", "se", "ser" are three
// questions nobody asked — only the last one is. Waiting for a pause sends one
// request instead of three; `useLatestRequest` still guards the ones that do
// go out, because a pause is no promise that the answers come back in order.

export function debounce<Args extends unknown[]>(target: (...args: Args) => unknown, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined

  function cancel(): void {
    if (timer !== undefined) {
      clearTimeout(timer)
      timer = undefined
    }
  }

  function call(...args: Args): void {
    cancel()
    timer = setTimeout(() => {
      timer = undefined
      void target(...args)
    }, ms)
  }

  return { call, cancel }
}
