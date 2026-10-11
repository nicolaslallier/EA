import { afterEach, describe, expect, it, vi } from 'vitest'

import { useDashboard } from '../src/features/dashboard/useDashboard'
import { aHeatmap, anAggregate, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ASK = { by: 'layer', cols: 'property:lifecycle', metric: 'count', property: '' } as const

describe('useDashboard', () => {
  it('asks for the tiles and the heat map with the question given', async () => {
    const calls = stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', body: aHeatmap() },
    ])
    const dashboard = useDashboard()

    await dashboard.load(ASK)

    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('rows')).toBe('layer')
    expect(heat?.url.searchParams.get('cols')).toBe('property:lifecycle')
    expect(heat?.url.searchParams.has('property')).toBe(false)
    expect(dashboard.tiles.value?.total).toBe(3)
    expect(dashboard.heatmap.value?.cells).toHaveLength(2)
  })

  it('sends the property only when a metric reads one', async () => {
    const calls = stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', body: aHeatmap() },
    ])
    const dashboard = useDashboard()

    await dashboard.load({ ...ASK, metric: 'sum', property: 'cost' })

    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('metric')).toBe('sum')
    expect(heat?.url.searchParams.get('property')).toBe('cost')
  })

  it('reports a refusal in words and clears the pictures', async () => {
    stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', status: 422, body: { error: 'invalid_analysis', detail: 'dimension inconnue' } },
    ])
    const dashboard = useDashboard()

    await dashboard.load(ASK)

    expect(dashboard.error.value).toBe('dimension inconnue')
    expect(dashboard.heatmap.value).toBeNull()
  })
})
