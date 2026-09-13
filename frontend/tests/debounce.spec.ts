import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { debounce } from '../src/lib/debounce'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('debounce', () => {
  it('calls once, with the last arguments, after the burst has stopped', () => {
    const target = vi.fn()
    const debounced = debounce(target, 250)

    debounced.call('s')
    vi.advanceTimersByTime(100)
    debounced.call('se')
    vi.advanceTimersByTime(100)
    debounced.call('ser')
    vi.advanceTimersByTime(249)
    expect(target).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(target).toHaveBeenCalledOnce()
    expect(target).toHaveBeenCalledWith('ser')
  })

  it('never calls what was cancelled', () => {
    const target = vi.fn()
    const debounced = debounce(target, 250)

    debounced.call('s')
    debounced.cancel()
    vi.advanceTimersByTime(1000)

    expect(target).not.toHaveBeenCalled()
  })
})
