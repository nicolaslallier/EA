// Keyboard movement over the matrix grid, and the open cell's URL spelling.
// Pure, so it is tested without mounting anything.

export type Position = { row: number; col: number }

/** Where a key takes the focus. Stops at the edges: a grid does not wrap. */
export function moveFocus(
  from: Position,
  key: string,
  size: { rows: number; cols: number },
): Position {
  const lastRow = Math.max(0, size.rows - 1)
  const lastCol = Math.max(0, size.cols - 1)
  switch (key) {
    case 'ArrowRight':
      return { row: from.row, col: Math.min(lastCol, from.col + 1) }
    case 'ArrowLeft':
      return { row: from.row, col: Math.max(0, from.col - 1) }
    case 'ArrowDown':
      return { row: Math.min(lastRow, from.row + 1), col: from.col }
    case 'ArrowUp':
      return { row: Math.max(0, from.row - 1), col: from.col }
    case 'Home':
      return { row: from.row, col: 0 }
    case 'End':
      return { row: from.row, col: lastCol }
    default:
      return from
  }
}

export function cellToParam(source: string, target: string): string {
  return `${source},${target}`
}

export function parseCell(text: string | undefined): { source: string; target: string } | null {
  if (!text) {
    return null
  }
  const [source, target, ...extra] = text.split(',')
  return source && target && extra.length === 0 ? { source, target } : null
}
