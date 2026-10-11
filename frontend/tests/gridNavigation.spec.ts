import { describe, expect, it } from 'vitest'

import { moveFocus } from '../src/features/matrix/gridNavigation'

const SIZE = { rows: 3, cols: 4 }

describe('moveFocus', () => {
  it('moves one cell with the arrows', () => {
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowRight', SIZE)).toEqual({ row: 1, col: 2 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowLeft', SIZE)).toEqual({ row: 1, col: 0 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowDown', SIZE)).toEqual({ row: 2, col: 1 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowUp', SIZE)).toEqual({ row: 0, col: 1 })
  })

  it('stops at the edges instead of wrapping', () => {
    expect(moveFocus({ row: 0, col: 0 }, 'ArrowUp', SIZE)).toEqual({ row: 0, col: 0 })
    expect(moveFocus({ row: 2, col: 3 }, 'ArrowRight', SIZE)).toEqual({ row: 2, col: 3 })
  })

  it('jumps to the ends of the row with Home and End', () => {
    expect(moveFocus({ row: 1, col: 2 }, 'Home', SIZE)).toEqual({ row: 1, col: 0 })
    expect(moveFocus({ row: 1, col: 2 }, 'End', SIZE)).toEqual({ row: 1, col: 3 })
  })

  it('leaves the position alone for any other key', () => {
    expect(moveFocus({ row: 1, col: 1 }, 'a', SIZE)).toEqual({ row: 1, col: 1 })
  })
})
