// The geometry of the heat map: a grid of rectangles with headers.
//
// Pure and deterministic, like `graphLayout.ts`: the same grid always draws the
// same picture, and it is tested without mounting anything. There is no chart
// library behind it — a grid of rectangles does not justify one (docs/adr/0058).
// Colours are not here either: the server decides a `level`, and
// `features/dashboard/presentation.ts` maps it to a swatch.

export const CELL_WIDTH = 96
export const CELL_HEIGHT = 44
export const HEADER_WIDTH = 150
export const HEADER_HEIGHT = 56
export const GAP = 2
export const PADDING = 8

export type HeatCell = {
  row: string
  col: string
  count: number
  value: number | null
  level: number | null
}

export type HeatRect = {
  row: string
  col: string
  x: number
  y: number
  width: number
  height: number
  count: number
  value: number | null
  level: number | null
}

export type HeatLayout = {
  width: number
  height: number
  viewBox: string
  rects: HeatRect[]
  rowHeaders: { key: string; y: number }[]
  colHeaders: { key: string; x: number }[]
}

export function layoutHeatmap(rows: string[], cols: string[], cells: HeatCell[]): HeatLayout {
  const known = new Map(cells.map((entry) => [`${entry.row}\u0000${entry.col}`, entry]))
  const left = PADDING + HEADER_WIDTH
  const top = PADDING + HEADER_HEIGHT
  const columnX = (index: number) => left + index * (CELL_WIDTH + GAP)
  const rowY = (index: number) => top + index * (CELL_HEIGHT + GAP)

  const rects: HeatRect[] = rows.flatMap((row, r) =>
    cols.map((col, c) => {
      const found = known.get(`${row}\u0000${col}`)
      return {
        row,
        col,
        x: columnX(c),
        y: rowY(r),
        width: CELL_WIDTH,
        height: CELL_HEIGHT,
        count: found?.count ?? 0,
        value: found?.value ?? null,
        level: found?.level ?? null,
      }
    }),
  )

  const width = PADDING * 2 + HEADER_WIDTH + cols.length * CELL_WIDTH + Math.max(0, cols.length - 1) * GAP
  const height = PADDING * 2 + HEADER_HEIGHT + rows.length * CELL_HEIGHT + Math.max(0, rows.length - 1) * GAP

  return {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    rects,
    rowHeaders: rows.map((key, index) => ({ key, y: rowY(index) })),
    colHeaders: cols.map((key, index) => ({ key, x: columnX(index) })),
  }
}

/** The levels actually present, lowest first: the legend shows what the picture uses. */
export function legendLevels(cells: HeatCell[]): number[] {
  const levels = new Set<number>()
  for (const entry of cells) {
    if (entry.level !== null) {
      levels.add(entry.level)
    }
  }
  return [...levels].sort((one, other) => one - other)
}
