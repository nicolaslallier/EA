import { describe, expect, it } from 'vitest'

import {
  MAX_WAYPOINTS,
  insertWaypoint,
  midpoints,
  moveWaypoint,
  removeWaypoint,
  routeOf,
} from '../src/lib/waypoints'

const left = { x: 0, y: 0, width: 100, height: 40 }
const right = { x: 400, y: 0, width: 100, height: 40 }

describe('routeOf', () => {
  it('is the trimmed straight segment when there is no bend', () => {
    expect(routeOf(left, right, [])).toEqual([
      { x: 100, y: 20 },
      { x: 400, y: 20 },
    ])
  })

  it('leaves each box towards the nearest bend and ends on the last bend', () => {
    const route = routeOf(left, right, [{ x: 250, y: 200 }])!

    expect(route).toHaveLength(3)
    expect(route[1]).toEqual({ x: 250, y: 200 })
    expect(route[0].y).toBeGreaterThan(20) // leaves downwards, not straight across
    expect(route[2].y).toBeGreaterThan(20)
  })

  it('is null when the boxes overlap and there is no bend to route through', () => {
    expect(routeOf(left, { ...right, x: 50 }, [])).toBeNull()
  })
})

describe('editing the bends', () => {
  const route = [
    { x: 0, y: 0 },
    { x: 100, y: 0 },
    { x: 100, y: 100 },
  ]

  it('finds the middle of each segment', () => {
    expect(midpoints(route)).toEqual([
      { x: 50, y: 0 },
      { x: 100, y: 50 },
    ])
  })

  it('inserts a bend before the one the segment ends on', () => {
    const bends = [{ x: 1, y: 1 }, { x: 3, y: 3 }]

    expect(insertWaypoint(bends, 1, { x: 2, y: 2 })).toEqual([
      { x: 1, y: 1 },
      { x: 2, y: 2 },
      { x: 3, y: 3 },
    ])
    expect(insertWaypoint(bends, 0, { x: 0, y: 0 })[0]).toEqual({ x: 0, y: 0 })
    expect(insertWaypoint(bends, 2, { x: 4, y: 4 })[2]).toEqual({ x: 4, y: 4 })
  })

  it('never exceeds the bound the API enforces', () => {
    const full = Array.from({ length: MAX_WAYPOINTS }, (_, i) => ({ x: i, y: 0 }))

    expect(insertWaypoint(full, 0, { x: 0, y: 9 })).toEqual(full)
  })

  it('moves and removes a bend without touching the others', () => {
    const bends = [{ x: 1, y: 1 }, { x: 2, y: 2 }]

    expect(moveWaypoint(bends, 1, { x: 9, y: 9 })).toEqual([{ x: 1, y: 1 }, { x: 9, y: 9 }])
    expect(removeWaypoint(bends, 0)).toEqual([{ x: 2, y: 2 }])
    expect(bends).toHaveLength(2) // the input is never mutated
  })
})
