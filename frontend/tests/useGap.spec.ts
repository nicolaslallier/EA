import { afterEach, describe, expect, it, vi } from 'vitest'

import { useGap } from '../src/features/roadmap/useGap'
import { anElement, aPlateau, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const AS_IS = aPlateau({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'As-Is' })
const TO_BE = aPlateau({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'To-Be' })
const OLD = anElement({ id: '11111111-aaaa-4aaa-8aaa-111111111111', name: 'Legacy' })
const GAP = anElement({
  id: '22222222-aaaa-4aaa-8aaa-222222222222',
  name: 'Écart As-Is → To-Be',
  element_type: 'gap',
  layer: 'implementation_migration',
  aspect: 'passive_structure',
})

const COMPARISON = {
  a: anElement({ id: AS_IS.id, name: 'As-Is', element_type: 'plateau' }),
  b: anElement({ id: TO_BE.id, name: 'To-Be', element_type: 'plateau' }),
  only_in_a: [OLD],
  only_in_b: [],
  common: [],
  gaps: [],
}

describe('useGap', () => {
  it('lists the plateaus', async () => {
    stubApi([{ path: '/plateaus', body: [AS_IS, TO_BE] }])
    const gap = useGap()

    await gap.loadPlateaus()

    expect(gap.plateaus.value.map((p) => p.name)).toEqual(['As-Is', 'To-Be'])
  })

  it('compares two plateaus by their ids', async () => {
    const calls = stubApi([{ path: '/plateaus/gap', body: COMPARISON }])
    const gap = useGap()

    await gap.compare(AS_IS.id, TO_BE.id)

    expect(calls.at(-1)?.url.searchParams.get('a')).toBe(AS_IS.id)
    expect(calls.at(-1)?.url.searchParams.get('b')).toBe(TO_BE.id)
    expect(gap.comparison.value?.only_in_a[0].name).toBe('Legacy')
  })

  it('reports a refused comparison in words and shows no stale comparison', async () => {
    stubApi([{ path: '/plateaus/gap', body: COMPARISON }])
    const gap = useGap()
    await gap.compare(AS_IS.id, TO_BE.id)

    stubApi([
      {
        path: '/plateaus/gap',
        status: 422,
        body: { error: 'invalid_timeline', detail: 'a plateau cannot be compared with itself' },
      },
    ])
    await gap.compare(AS_IS.id, AS_IS.id)

    expect(gap.status.value).toBe('error')
    expect(gap.error.value).toBe('a plateau cannot be compared with itself')
    expect(gap.comparison.value).toBeNull()
  })

  it('shows the comparison asked last, whichever answer arrives first', async () => {
    const calls = deferApi()
    const gap = useGap()

    const first = gap.compare(AS_IS.id, TO_BE.id)
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = gap.compare(TO_BE.id, AS_IS.id)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer({ ...COMPARISON, only_in_a: [], only_in_b: [OLD] })
    calls[0].answer(COMPARISON)
    await Promise.all([first, second])

    expect(gap.comparison.value?.only_in_b[0].name).toBe('Legacy')
  })

  it('creates the gap and then reads the comparison again', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/plateaus/gap', status: 201, body: GAP },
      { path: '/plateaus/gap', body: { ...COMPARISON, gaps: [GAP] } },
    ])
    const gap = useGap()

    await gap.createGap(AS_IS.id, TO_BE.id)

    expect(calls[0].method).toBe('POST')
    expect(calls[0].body).toEqual({ a: AS_IS.id, b: TO_BE.id })
    expect(gap.comparison.value?.gaps).toHaveLength(1)
    expect(gap.createError.value).toBe('')
  })

  it("keeps the API's refusal of a duplicate gap", async () => {
    stubApi([
      {
        method: 'POST',
        path: '/plateaus/gap',
        status: 409,
        body: { error: 'duplicate_gap', detail: 'a gap is already drawn between these two plateaus' },
      },
    ])
    const gap = useGap()

    await gap.createGap(AS_IS.id, TO_BE.id)

    expect(gap.createError.value).toContain('already drawn')
  })
})
