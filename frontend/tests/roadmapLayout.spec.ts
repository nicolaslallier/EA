import { describe, expect, it } from 'vitest'

import {
  AXIS_HEIGHT,
  LABEL_WIDTH,
  ROW_HEIGHT,
  dayNumber,
  layoutRoadmap,
  type RoadmapItemRead,
} from '../src/lib/roadmapLayout'

function item(overrides: Partial<RoadmapItemRead> = {}): RoadmapItemRead {
  return {
    id: 'id-' + (overrides.name ?? 'x'),
    name: 'x',
    element_type: 'work_package',
    valid_from: null,
    valid_until: null,
    plateau_ids: [],
    dated: Boolean(overrides.valid_from ?? overrides.valid_until),
    ...overrides,
  }
}

// A ten-day window drawn on a 1000-unit plot: one day is exactly 100 units.
const WINDOW = { from: '2026-01-01', to: '2026-01-10' }
const WIDTH = LABEL_WIDTH + 1000 + 16

describe('layoutRoadmap', () => {
  it('counts days without drifting across months and leap years', () => {
    expect(dayNumber('2028-03-01') - dayNumber('2028-02-28')).toBe(2)
    expect(dayNumber('2027-03-01') - dayNumber('2027-02-28')).toBe(1)
  })

  it('puts a bar where its first and last day are, the last day included', () => {
    const layout = layoutRoadmap(
      [item({ name: 'Phase', valid_from: '2026-01-03', valid_until: '2026-01-04' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks).toHaveLength(1)
    expect(layout.marks[0]).toMatchObject({
      kind: 'bar',
      row: 0,
      x: LABEL_WIDTH + 200,
      width: 200,
      y: AXIS_HEIGHT,
      clippedStart: false,
      clippedEnd: false,
    })
  })

  it('cuts a bar that leaves the window and says so', () => {
    const layout = layoutRoadmap(
      [item({ name: 'Long', valid_from: '2025-12-01', valid_until: '2026-03-01' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks[0]).toMatchObject({ x: LABEL_WIDTH, width: 1000, clippedStart: true, clippedEnd: true })
  })

  it('draws an open end up to the edge of the window', () => {
    const layout = layoutRoadmap([item({ name: 'Open', valid_from: '2026-01-06' })], WINDOW, WIDTH)

    expect(layout.marks[0]).toMatchObject({ x: LABEL_WIDTH + 500, width: 500, clippedEnd: true })
  })

  it('leaves out what lies wholly outside the window', () => {
    const layout = layoutRoadmap(
      [
        item({ name: 'Before', valid_until: '2025-12-31' }),
        item({ name: 'After', valid_from: '2026-02-01' }),
      ],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks).toEqual([])
    expect(layout.undated).toEqual([])
  })

  it('draws a plateau as a milestone on the day it is reached', () => {
    const layout = layoutRoadmap(
      [item({ name: 'To-Be', element_type: 'plateau', valid_from: '2026-01-06', valid_until: '2026-12-31' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks[0]).toMatchObject({ kind: 'milestone', x: LABEL_WIDTH + 500, width: 0 })
  })

  it('lists an undated element apart instead of hiding it', () => {
    const undated = item({ name: 'Runbook', element_type: 'deliverable' })

    const layout = layoutRoadmap([undated, item({ name: 'Dated', valid_from: '2026-01-02' })], WINDOW, WIDTH)

    expect(layout.undated).toEqual([undated])
    expect(layout.marks.map((mark) => mark.item.name)).toEqual(['Dated'])
  })

  it('stacks plateaus first, then work packages, then deliverables, each by start then name', () => {
    const layout = layoutRoadmap(
      [
        item({ name: 'Livrable', element_type: 'deliverable', valid_from: '2026-01-01' }),
        item({ name: 'B', valid_from: '2026-01-05' }),
        item({ name: 'A', valid_from: '2026-01-05' }),
        item({ name: 'Early', valid_from: '2026-01-02' }),
        item({ name: 'To-Be', element_type: 'plateau', valid_from: '2026-01-09' }),
      ],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks.map((mark) => [mark.item.name, mark.row])).toEqual([
      ['To-Be', 0],
      ['Early', 1],
      ['A', 2],
      ['B', 3],
      ['Livrable', 4],
    ])
    expect(layout.marks[4].y).toBe(AXIS_HEIGHT + 4 * ROW_HEIGHT)
    expect(layout.height).toBe(AXIS_HEIGHT + 5 * ROW_HEIGHT)
  })

  it('marks the as-of day with a vertical line, only when it is in the window', () => {
    expect(layoutRoadmap([], WINDOW, WIDTH, '2026-01-06').today).toBe(LABEL_WIDTH + 500)
    expect(layoutRoadmap([], WINDOW, WIDTH, '2027-01-01').today).toBeNull()
    expect(layoutRoadmap([], WINDOW, WIDTH).today).toBeNull()
  })

  it('graduates the axis by quarter on a short window and by year on a long one', () => {
    const short = layoutRoadmap([], { from: '2026-01-01', to: '2027-06-30' }, WIDTH)
    const long = layoutRoadmap([], { from: '2024-01-01', to: '2030-12-31' }, WIDTH)

    expect(short.ticks.map((tick) => tick.label)).toEqual(['2026 T1', '2026 T2', '2026 T3', '2026 T4', '2027 T1', '2027 T2'])
    expect(long.ticks.map((tick) => tick.label)).toEqual(['2024', '2025', '2026', '2027', '2028', '2029', '2030'])
    expect(short.ticks[0].x).toBe(LABEL_WIDTH)
  })

  it('treats a date it cannot read as no date, never as NaN', () => {
    const odd = item({ name: 'Odd', valid_from: 'demain' })

    const layout = layoutRoadmap([odd], WINDOW, WIDTH)

    expect(layout.marks).toEqual([])
    expect(layout.undated).toEqual([odd])
    expect(layout.height).toBeGreaterThan(0)
  })

  it('is deterministic: the same input draws the same way', () => {
    const items = [
      item({ name: 'A', valid_from: '2026-01-02', valid_until: '2026-01-05' }),
      item({ name: 'B', element_type: 'plateau', valid_from: '2026-01-08' }),
    ]

    expect(layoutRoadmap(items, WINDOW, WIDTH, '2026-01-04')).toEqual(
      layoutRoadmap([...items].reverse(), WINDOW, WIDTH, '2026-01-04'),
    )
  })
})
