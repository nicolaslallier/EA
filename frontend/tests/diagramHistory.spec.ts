import { describe, expect, it, vi } from 'vitest'

import { HISTORY_LIMIT, createHistory, type Command } from '../src/lib/diagramHistory'

/** A counter whose every change is a command already applied. */
function counter() {
  let value = 0
  const history = createHistory()
  const set = (next: number, mergeKey?: string) => {
    const before = value
    value = next
    const command: Command = {
      label: `to ${next}`,
      mergeKey,
      do: () => {
        value = next
      },
      undo: () => {
        value = before
      },
    }
    history.record(command)
  }
  return { history, set, value: () => value }
}

describe('createHistory', () => {
  it('undoes then redoes, in order', () => {
    const { history, set, value } = counter()
    set(1)
    set(2)

    history.undo()
    expect(value()).toBe(1)
    history.undo()
    expect(value()).toBe(0)
    expect(history.canUndo()).toBe(false)

    history.redo()
    history.redo()
    expect(value()).toBe(2)
    expect(history.canRedo()).toBe(false)
  })

  it('forgets what could be redone once something new is recorded', () => {
    const { history, set } = counter()
    set(1)
    history.undo()

    set(5)

    expect(history.canRedo()).toBe(false)
  })

  it('merges consecutive commands sharing a key into one entry, keeping the first undo', () => {
    const { history, set, value } = counter()
    set(1, 'nudge')
    set(2, 'nudge')
    set(3, 'nudge')

    expect(history.size()).toBe(1)
    history.undo()
    expect(value()).toBe(0)
    history.redo()
    expect(value()).toBe(3)
  })

  it('does not merge across a seal, nor across different keys', () => {
    const { history, set } = counter()
    set(1, 'nudge')
    history.seal()
    set(2, 'nudge')
    set(3, 'other')

    expect(history.size()).toBe(3)
  })

  it('keeps at most the limit, dropping the oldest', () => {
    const { history, set, value } = counter()
    for (let i = 1; i <= HISTORY_LIMIT + 5; i += 1) {
      set(i)
    }

    expect(history.size()).toBe(HISTORY_LIMIT)
    for (let i = 0; i < HISTORY_LIMIT; i += 1) {
      history.undo()
    }
    expect(value()).toBe(5)
  })

  it('tells its owner when it changes, and undo on an empty stack is a no-op', () => {
    const onChange = vi.fn()
    const history = createHistory({ onChange })

    expect(history.undo()).toBe(false)
    history.record({ label: 'x', do: () => undefined, undo: () => undefined })

    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('clear empties both stacks', () => {
    const { history, set } = counter()
    set(1)
    history.undo()

    history.clear()

    expect([history.canUndo(), history.canRedo()]).toEqual([false, false])
  })
})
