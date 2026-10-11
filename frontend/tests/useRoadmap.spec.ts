import { afterEach, describe, expect, it, vi } from 'vitest'

import { useRoadmap } from '../src/features/roadmap/useRoadmap'
import { aRoadmapItem, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const BODY = {
  items: [aRoadmapItem({ valid_from: '2026-06-01', valid_until: '2027-12-31' })],
  edges: [],
  window_from: '2026-06-01',
  window_to: '2027-12-31',
}

describe('useRoadmap', () => {
  it('asks for the window it is given, and for none by default', async () => {
    const calls = stubApi([{ path: '/roadmap', body: BODY }])
    const roadmap = useRoadmap()

    await roadmap.load()
    expect(calls.at(-1)?.url.searchParams.has('from')).toBe(false)

    await roadmap.load({ from: '2026-01-01', to: '2026-12-31' })
    expect(calls.at(-1)?.url.searchParams.get('from')).toBe('2026-01-01')
    expect(calls.at(-1)?.url.searchParams.get('to')).toBe('2026-12-31')
  })

  it('exposes the window the server resolved', async () => {
    stubApi([{ path: '/roadmap', body: BODY }])
    const roadmap = useRoadmap()

    await roadmap.load()

    expect(roadmap.window.value).toEqual({ from: '2026-06-01', to: '2027-12-31' })
  })

  it('has no window when nothing is dated', async () => {
    stubApi([
      {
        path: '/roadmap',
        body: { items: [aRoadmapItem()], edges: [], window_from: null, window_to: null },
      },
    ])
    const roadmap = useRoadmap()

    await roadmap.load()

    expect(roadmap.window.value).toBeNull()
    expect(roadmap.roadmap.value?.items).toHaveLength(1)
  })

  it('says why the API refused a window', async () => {
    stubApi([
      {
        path: '/roadmap',
        status: 422,
        body: { error: 'invalid_timeline', detail: 'the roadmap window is wider than 20 years' },
      },
    ])
    const roadmap = useRoadmap()

    await roadmap.load({ from: '2000-01-01', to: '2030-01-01' })

    expect(roadmap.status.value).toBe('error')
    expect(roadmap.error.value).toContain('20 years')
    expect(roadmap.roadmap.value).toBeNull()
  })
})
