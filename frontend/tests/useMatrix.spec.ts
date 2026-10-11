import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMatrix } from '../src/features/matrix/useMatrix'
import { aCellDetail, aGrid, aRelationship, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SOURCE = 'aaaaaaaa-1111-4111-8111-111111111111'
const TARGET = 'bbbbbbbb-2222-4222-8222-222222222222'

describe('useMatrix', () => {
  it('posts both axes as the API wants them', async () => {
    const calls = stubApi([{ method: 'POST', path: '/matrix', body: aGrid() }])
    const matrix = useMatrix()

    await matrix.load({ kind: 'layer', layer: 'business' }, { kind: 'all' })

    expect(calls[0].body).toEqual({ rows: { layers: ['business'] }, cols: {} })
    expect(matrix.matrix.value?.rows).toHaveLength(2)
    expect(matrix.status.value).toBe('ready')
  })

  it('reports a refusal in words and shows no stale grid', async () => {
    stubApi([
      { method: 'POST', path: '/matrix', body: aGrid() },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })

    stubApi([
      {
        method: 'POST',
        path: '/matrix',
        status: 422,
        body: { error: 'matrix_axis_too_large', detail: 'an axis names 60 elements' },
      },
    ])
    await matrix.load({ kind: 'ids', ids: ['x'] }, { kind: 'all' })

    expect(matrix.matrix.value).toBeNull()
    expect(matrix.error.value).toBe('an axis names 60 elements')
  })

  it('keeps only the answer to the grid asked last', async () => {
    const held = deferApi()
    const matrix = useMatrix()

    const first = matrix.load({ kind: 'layer', layer: 'business' }, { kind: 'all' })
    const second = matrix.load({ kind: 'layer', layer: 'application' }, { kind: 'all' })
    // The client awaits the token before it fetches: wait for both requests to be in flight.
    await vi.waitFor(() => expect(held).toHaveLength(2))
    held[1].answer(aGrid({ rows_total: 7 }))
    held[0].answer(aGrid({ rows_total: 1 }))
    await Promise.all([first, second])

    expect(held[0].aborted()).toBe(true)
    expect(matrix.matrix.value?.rows_total).toBe(7)
  })

  it('opens a cell with the pair in the query', async () => {
    const calls = stubApi([{ path: '/matrix/cell', body: aCellDetail() }])
    const matrix = useMatrix()

    await matrix.openCell(SOURCE, TARGET)

    expect(calls[0].url.searchParams.get('source')).toBe(SOURCE)
    expect(calls[0].url.searchParams.get('target')).toBe(TARGET)
    expect(matrix.detail.value?.allowed).toEqual(['serving', 'association'])
  })

  it('creates through POST /relationships, then reloads the cell and the grid', async () => {
    const calls = stubApi([
      { path: '/matrix/cell', body: aCellDetail() },
      { method: 'POST', path: '/matrix', body: aGrid() },
      { method: 'POST', path: '/relationships', status: 201, body: aRelationship() },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })
    await matrix.openCell(SOURCE, TARGET)

    await matrix.connect('association')

    const post = calls.find((call) => call.method === 'POST' && call.url.pathname === '/relationships')
    expect(post?.body).toEqual({
      relationship_type: 'association',
      source_id: SOURCE,
      target_id: TARGET,
    })
    expect(calls.filter((call) => call.url.pathname === '/matrix/cell')).toHaveLength(2)
    expect(calls.filter((call) => call.url.pathname === '/matrix')).toHaveLength(2)
  })

  it('removes through DELETE /relationships/{id}', async () => {
    const link = aRelationship()
    const calls = stubApi([
      { path: '/matrix/cell', body: aCellDetail({ links: [link] }) },
      { method: 'POST', path: '/matrix', body: aGrid() },
      { method: 'DELETE', path: `/relationships/${link.id}`, status: 204 },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })
    await matrix.openCell(SOURCE, TARGET)

    await matrix.disconnect(link.id)

    expect(calls.some((call) => call.method === 'DELETE')).toBe(true)
  })

  it('forgets the open cell when it is closed', async () => {
    stubApi([{ path: '/matrix/cell', body: aCellDetail() }])
    const matrix = useMatrix()
    await matrix.openCell(SOURCE, TARGET)

    matrix.closeCell()

    expect(matrix.detail.value).toBeNull()
  })
})
