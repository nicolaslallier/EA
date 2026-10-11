import { describe, expect, it } from 'vitest'

import {
  CELL_HEIGHT,
  CELL_WIDTH,
  GAP,
  HEADER_HEIGHT,
  HEADER_WIDTH,
  PADDING,
  layoutHeatmap,
  legendLevels,
  type HeatCell,
} from '../src/lib/heatmapLayout'

const cell = (row: string, col: string, count: number, level: number | null): HeatCell => ({
  row,
  col,
  count,
  value: count,
  level,
})

describe('layoutHeatmap', () => {
  it('puts one rect per row and column, row by row', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y', 'z'], [cell('a', 'x', 1, 0)])

    expect(layout.rects).toHaveLength(6)
    expect(layout.rects.map((rect) => `${rect.row}/${rect.col}`)).toEqual([
      'a/x', 'a/y', 'a/z', 'b/x', 'b/y', 'b/z',
    ])
  })

  it('places a rect after the headers, one cell and one gap further each step', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y'], [])
    const [first, second, third] = layout.rects

    expect(first).toMatchObject({ x: PADDING + HEADER_WIDTH, y: PADDING + HEADER_HEIGHT })
    expect(second.x - first.x).toBe(CELL_WIDTH + GAP)
    expect(third.y - first.y).toBe(CELL_HEIGHT + GAP)
  })

  it('carries the count, value and level of a known cell and none for a missing one', () => {
    const layout = layoutHeatmap(['a'], ['x', 'y'], [cell('a', 'x', 7, 3)])

    expect(layout.rects[0]).toMatchObject({ count: 7, value: 7, level: 3 })
    expect(layout.rects[1]).toMatchObject({ count: 0, value: null, level: null })
  })

  it('sizes the canvas to its content and says so in the viewBox', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y', 'z'], [])

    expect(layout.width).toBe(PADDING * 2 + HEADER_WIDTH + 3 * CELL_WIDTH + 2 * GAP)
    expect(layout.height).toBe(PADDING * 2 + HEADER_HEIGHT + 2 * CELL_HEIGHT + GAP)
    expect(layout.viewBox).toBe(`0 0 ${layout.width} ${layout.height}`)
  })

  it('lines the headers up with their rects', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y'], [])

    expect(layout.colHeaders.map((header) => header.x)).toEqual(
      layout.rects.slice(0, 2).map((rect) => rect.x),
    )
    expect(layout.rowHeaders.map((header) => header.y)).toEqual([
      layout.rects[0].y,
      layout.rects[2].y,
    ])
  })

  it('lays out nothing, but a valid canvas, for an empty grid', () => {
    const layout = layoutHeatmap([], [], [])

    expect(layout.rects).toEqual([])
    expect(layout.width).toBeGreaterThan(0)
    expect(layout.height).toBeGreaterThan(0)
  })

  it('always draws the same picture for the same grid', () => {
    const cells = [cell('a', 'x', 2, 1)]

    expect(layoutHeatmap(['a'], ['x'], cells)).toEqual(layoutHeatmap(['a'], ['x'], cells))
  })
})

describe('legendLevels', () => {
  it('lists the distinct levels actually received, lowest first', () => {
    expect(legendLevels([cell('a', 'x', 1, 3), cell('a', 'y', 1, 0), cell('b', 'x', 1, 3)])).toEqual([0, 3])
  })

  it('leaves out cells with no level', () => {
    expect(legendLevels([cell('a', 'x', 0, null)])).toEqual([])
  })
})
