// Where a roadmap goes on the canvas.
//
// Pure geometry, like `graphLayout.ts`: dates in, coordinates out, nothing
// animated, nothing random, so a test can assert an abscissa and the same
// roadmap always draws the same way. A day is a unit of the x axis: the window
// `[from, to]` (both days included) is stretched over the plot, and a bar runs
// from the start of its first day to the end of its last.
import type { components } from '../api/schema'

export type RoadmapItemRead = components['schemas']['RoadmapItemRead']

/** The days drawn, both included, as YYYY-MM-DD. */
export type DayWindow = { from: string; to: string }

/** Room for the names, on the left of the plot. */
export const LABEL_WIDTH = 190
export const ROW_HEIGHT = 30
export const AXIS_HEIGHT = 28
const PLOT_MARGIN = 16
const DAY_MS = 86_400_000

export type RoadmapMark = {
  item: RoadmapItemRead
  /** A work package or a deliverable is a bar; a plateau is a diamond on the day it is reached. */
  kind: 'bar' | 'milestone'
  row: number
  x: number
  /** 0 for a milestone. */
  width: number
  /** The top of its row. */
  y: number
  /** The bar goes on past the edge of the window, or has no start at all. */
  clippedStart: boolean
  clippedEnd: boolean
}

export type Tick = { x: number; label: string }

export type RoadmapLayout = {
  marks: RoadmapMark[]
  /** Items with no date at all: listed apart, never hidden. */
  undated: RoadmapItemRead[]
  ticks: Tick[]
  /** The x of the as-of day, or null when none is asked or it is off the window. */
  today: number | null
  width: number
  height: number
}

const KIND_ORDER: Record<string, number> = { plateau: 0, work_package: 1, deliverable: 2 }

/** The number of whole days since 1970-01-01 (UTC, so a clock change moves nothing). */
export function dayNumber(iso: string): number {
  return Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY_MS)
}

/** A day, or null when there is none - or when what was given is not a day: never NaN. */
function dayOrNull(iso: string | null | undefined): number | null {
  if (!iso) {
    return null
  }
  const day = dayNumber(iso)
  return Number.isFinite(day) ? day : null
}

function startOf(item: RoadmapItemRead): number | null {
  return dayOrNull(item.valid_from)
}

function byRow(left: RoadmapItemRead, right: RoadmapItemRead): number {
  const kind = (KIND_ORDER[left.element_type] ?? 3) - (KIND_ORDER[right.element_type] ?? 3)
  if (kind !== 0) {
    return kind
  }
  const a = startOf(left)
  const b = startOf(right)
  if (a !== b) {
    return a === null ? 1 : b === null ? -1 : a - b
  }
  return left.name.localeCompare(right.name) || left.id.localeCompare(right.id)
}

function ticksOf(from: number, to: number, scale: (day: number) => number): Tick[] {
  const years = (to - from) / 365 > 3
  const first = new Date(from * DAY_MS).getUTCFullYear()
  const last = new Date((to - 1) * DAY_MS).getUTCFullYear()
  const ticks: Tick[] = []
  for (let year = first; year <= last; year += 1) {
    for (const month of years ? [0] : [0, 3, 6, 9]) {
      const day = Date.UTC(year, month, 1) / DAY_MS
      if (day >= from && day < to) {
        ticks.push({ x: scale(day), label: years ? `${year}` : `${year} T${month / 3 + 1}` })
      }
    }
  }
  return ticks
}

export function layoutRoadmap(
  items: RoadmapItemRead[],
  window: DayWindow,
  width: number,
  asOf?: string,
): RoadmapLayout {
  const from = dayNumber(window.from)
  // Exclusive upper bound: the last day of the window is drawn in full.
  const to = dayNumber(window.to) + 1
  const span = Math.max(1, to - from)
  const plot = Math.max(1, width - LABEL_WIDTH - PLOT_MARGIN)
  const scale = (day: number) => LABEL_WIDTH + ((Math.min(Math.max(day, from), to) - from) / span) * plot

  const marks: RoadmapMark[] = []
  const undated: RoadmapItemRead[] = []

  for (const item of [...items].sort(byRow)) {
    const start = startOf(item)
    // `valid_until` is inclusive: the bar ends where the next day begins.
    const last = dayOrNull(item.valid_until)
    const end = last === null ? null : last + 1
    if (start === null && end === null) {
      undated.push(item)
      continue
    }
    // The row is taken only once the item is known to be drawn, so rows stay consecutive.
    const row = marks.length
    const y = AXIS_HEIGHT + row * ROW_HEIGHT
    if (item.element_type === 'plateau') {
      const day = start ?? (end as number) - 1
      if (day >= from && day < to) {
        marks.push({ item, kind: 'milestone', row, x: scale(day), width: 0, y, clippedStart: false, clippedEnd: false })
      }
      continue
    }
    const first = start ?? from
    const after = end ?? to
    if (after <= from || first >= to) {
      continue
    }
    marks.push({
      item,
      kind: 'bar',
      row,
      x: scale(first),
      width: Math.max(2, scale(after) - scale(first)),
      y,
      clippedStart: start === null || start < from,
      clippedEnd: end === null || end > to,
    })
  }

  const asOfDay = asOf ? dayNumber(asOf) : null
  return {
    marks,
    undated,
    ticks: ticksOf(from, to, scale),
    today: asOfDay !== null && asOfDay >= from && asOfDay < to ? scale(asOfDay) : null,
    width,
    height: AXIS_HEIGHT + Math.max(1, marks.length) * ROW_HEIGHT,
  }
}
