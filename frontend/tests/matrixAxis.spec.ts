import { describe, expect, it } from 'vitest'

import { axisToBody, axisToParam, parseAxis } from '../src/features/matrix/axis'
import { cellToParam, parseCell } from '../src/features/matrix/gridNavigation'

describe('the axis in the URL', () => {
  it('reads nothing, or something unknown, as the whole catalogue', () => {
    expect(parseAxis(undefined)).toEqual({ kind: 'all' })
    expect(parseAxis('')).toEqual({ kind: 'all' })
    expect(parseAxis('banane:3')).toEqual({ kind: 'all' })
  })

  it('round-trips a layer, a type and a list of ids', () => {
    for (const text of ['all', 'layer:business', 'type:node', 'ids:aaa,bbb']) {
      expect(axisToParam(parseAxis(text))).toBe(text)
    }
  })

  it('round-trips a package and a saved query, and sends their ids', () => {
    for (const text of ['pkg:abc', 'q:abc']) {
      expect(axisToParam(parseAxis(text))).toBe(text)
    }
    expect(axisToBody(parseAxis('pkg:abc'))).toEqual({ package_id: 'abc' })
    expect(axisToBody(parseAxis('q:abc'))).toEqual({ query_id: 'abc' })
    expect(parseAxis('pkg:')).toEqual({ kind: 'all' })
  })

  it('drops empty ids rather than sending them', () => {
    expect(parseAxis('ids:aaa,,bbb,')).toEqual({ kind: 'ids', ids: ['aaa', 'bbb'] })
    expect(parseAxis('ids:')).toEqual({ kind: 'all' })
  })

  it('builds the request body each choice means', () => {
    expect(axisToBody({ kind: 'all' })).toEqual({})
    expect(axisToBody({ kind: 'layer', layer: 'business' })).toEqual({ layers: ['business'] })
    expect(axisToBody({ kind: 'type', type: 'node' })).toEqual({ element_types: ['node'] })
    expect(axisToBody({ kind: 'ids', ids: ['a'] })).toEqual({ element_ids: ['a'] })
  })
})

describe('the open cell in the URL', () => {
  it('round-trips a source and a target', () => {
    expect(parseCell(cellToParam('s-1', 't-2'))).toEqual({ source: 's-1', target: 't-2' })
  })

  it('reads anything else as no cell', () => {
    expect(parseCell(undefined)).toBeNull()
    expect(parseCell('seul')).toBeNull()
    expect(parseCell(',t')).toBeNull()
  })
})
