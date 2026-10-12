// Undo and redo for one editing session of a diagram.
//
// A drag has already moved the box on screen by the time it ends, so what is
// recorded is a command *already applied*: `undo` puts the scene back, `do`
// puts it forward again (redo). Memory only — an undo stack that survived a
// reload would undo a layout somebody else may have saved since. Nothing here
// knows what a scene is; the caller's closures do.

export type Command = {
  label: string
  do: () => void
  undo: () => void
  /** Consecutive commands with the same key are one entry — arrow-key nudges. */
  mergeKey?: string
}

/** The most gestures that can be taken back. */
export const HISTORY_LIMIT = 100

export function createHistory(options: { limit?: number; onChange?: () => void } = {}) {
  const limit = options.limit ?? HISTORY_LIMIT
  const done: Command[] = []
  const undone: Command[] = []
  // Set by undo, redo and `seal`: the next record starts a fresh entry
  // instead of folding into the one on top.
  let sealed = false

  const changed = () => options.onChange?.()

  function record(command: Command): void {
    const top = done[done.length - 1]
    if (top && !sealed && command.mergeKey !== undefined && top.mergeKey === command.mergeKey) {
      done[done.length - 1] = { ...top, do: command.do, label: command.label }
    } else {
      done.push(command)
      if (done.length > limit) {
        done.shift()
      }
    }
    sealed = false
    undone.length = 0
    changed()
  }

  function undo(): boolean {
    const command = done.pop()
    if (!command) {
      return false
    }
    command.undo()
    undone.push(command)
    sealed = true
    changed()
    return true
  }

  function redo(): boolean {
    const command = undone.pop()
    if (!command) {
      return false
    }
    command.do()
    done.push(command)
    sealed = true
    changed()
    return true
  }

  function clear(): void {
    done.length = 0
    undone.length = 0
    sealed = false
    changed()
  }

  return {
    record,
    undo,
    redo,
    clear,
    seal: () => {
      sealed = true
    },
    canUndo: () => done.length > 0,
    canRedo: () => undone.length > 0,
    size: () => done.length,
  }
}
