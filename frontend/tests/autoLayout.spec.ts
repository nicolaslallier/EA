import { describe, expect, it } from 'vitest'

import {
  GAP_X,
  GAP_Y,
  MAX_ROW_WIDTH,
  ORIGIN,
  ROW_OF,
  layoutByLayers,
  placeAdded,
  type LayoutInput,
} from '../src/lib/autoLayout'
import { BOX_HEIGHT, BOX_WIDTH } from '../src/lib/graphLayout'

type El = LayoutInput['elements'][number]
const el = (id: string, layer: El['layer'], name = id): El => ({ id, name, layer })
const link = (source_id: string, target_id: string) => ({ source_id, target_id })

describe('layoutByLayers', () => {
  it('stacks the layers top to bottom in the order of ArchiMate, physical with technology', () => {
    const positions = layoutByLayers({
      elements: [
        el('t', 'technology'),
        el('b', 'business'),
        el('a', 'application'),
        el('m', 'motivation'),
        el('p', 'physical'),
        el('i', 'implementation_migration'),
      ],
      relationships: [],
    })

    const y = (id: string) => positions.get(id)!.y
    expect(y('m')).toBeLessThan(y('b'))
    expect(y('b')).toBeLessThan(y('a'))
    expect(y('a')).toBeLessThan(y('t'))
    expect(y('p')).toBe(y('t'))
    expect(y('t')).toBeLessThan(y('i'))
  })

  it('leaves no empty band between two layers that are present', () => {
    const positions = layoutByLayers({ elements: [el('b', 'business'), el('t', 'technology')], relationships: [] })

    expect(positions.get('t')!.y - positions.get('b')!.y).toBe(BOX_HEIGHT + GAP_Y)
  })

  it('gives the same answer whatever the order of the input', () => {
    const elements = ['d', 'a', 'c', 'b', 'e'].map((id, i) => el(id, i % 2 ? 'business' : 'application', `nom ${id}`))
    const relationships = [link('a', 'b'), link('c', 'd'), link('e', 'a')]

    const first = layoutByLayers({ elements, relationships })
    const second = layoutByLayers({ elements: [...elements].reverse(), relationships: [...relationships].reverse() })

    expect([...first.entries()].sort()).toEqual([...second.entries()].sort())
  })

  it('orders a row by the barycentre of its neighbours, so links cross less', () => {
    // Business a, b (alphabetical). Application x is wired to b and y to a:
    // alphabetically x < y would cross; the barycentre swaps them.
    const positions = layoutByLayers({
      elements: [el('a', 'business'), el('b', 'business'), el('x', 'application'), el('y', 'application')],
      relationships: [link('a', 'y'), link('b', 'x')],
    })

    expect(positions.get('y')!.x).toBeLessThan(positions.get('x')!.x)
  })

  it('breaks ties by name, then by id', () => {
    const positions = layoutByLayers({
      elements: [el('2', 'business', 'Même nom'), el('1', 'business', 'Même nom'), el('0', 'business', 'Autre')],
      relationships: [],
    })

    const order = [...positions.entries()].sort((a, b) => a[1].x - b[1].x).map(([id]) => id)
    expect(order).toEqual(['0', '1', '2'])
  })

  it('wraps a row wider than the limit into sub-rows, never past the limit', () => {
    const elements = Array.from({ length: 20 }, (_, i) => el(`e${String(i).padStart(2, '0')}`, 'application'))

    const positions = layoutByLayers({ elements, relationships: [] })

    const rows = new Set([...positions.values()].map((p) => p.y))
    expect(rows.size).toBe(3)
    const right = Math.max(...[...positions.values()].map((p) => p.x + BOX_WIDTH))
    expect(right).toBeLessThanOrEqual(ORIGIN.x + MAX_ROW_WIDTH)
  })

  it('never yields a negative coordinate and ignores a link to an element that is not there', () => {
    const positions = layoutByLayers({ elements: [el('a', 'business')], relationships: [link('a', 'ghost')] })

    const only = positions.get('a')!
    expect(only).toEqual({ x: ORIGIN.x, y: ORIGIN.y })
    expect(only.x).toBeGreaterThanOrEqual(0)
  })

  it('lays out nothing as nothing', () => {
    expect(layoutByLayers({ elements: [], relationships: [] }).size).toBe(0)
  })

  it('keeps the visual order as a presentation table over every layer of the API', () => {
    expect(Object.keys(ROW_OF).sort()).toEqual(
      ['application', 'business', 'implementation_migration', 'motivation', 'other', 'physical', 'strategy', 'technology'],
    )
    expect(GAP_X).toBeGreaterThan(0)
  })
})

describe('placeAdded', () => {
  it('places only the added elements, below everything that is already there', () => {
    const occupied = [{ x: 24, y: 24, width: BOX_WIDTH, height: 300 }]

    const positions = placeAdded(
      { elements: [el('new1', 'application'), el('new2', 'application')], relationships: [] },
      occupied,
    )

    expect([...positions.keys()].sort()).toEqual(['new1', 'new2'])
    for (const point of positions.values()) {
      expect(point.y).toBeGreaterThanOrEqual(24 + 300 + GAP_Y)
    }
  })

  it('starts at the origin when nothing is there yet', () => {
    const positions = placeAdded({ elements: [el('a', 'business')], relationships: [] }, [])

    expect(positions.get('a')).toEqual({ x: ORIGIN.x, y: ORIGIN.y })
  })
})
