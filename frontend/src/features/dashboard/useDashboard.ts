// The dashboard's state: the tiles (a count per bucket) and the heat map, two
// answers to one question. Each change of the question aborts the previous
// pair (lib/latest.ts), so the pictures always answer what the URL says now.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type AggregateRead = components['schemas']['AggregateRead']
export type HeatmapRead = components['schemas']['HeatmapRead']
export type Metric = components['schemas']['Metric']

export type DashboardQuestion = {
  /** The rows dimension: `layer`, `element_type` or `property:<key>`. */
  by: string
  /** The columns dimension. */
  cols: string
  metric: Metric
  /** The property a metric other than `count` reads; '' for none. */
  property: string
}

export function useDashboard() {
  const tiles = ref<AggregateRead | null>(null)
  const heatmap = ref<HeatmapRead | null>(null)
  const question = useLatestRequest()

  async function load(asked: DashboardQuestion): Promise<void> {
    // The property is left out for a count: an unused `property=` would be
    // validated for nothing, and a blank one is not a key.
    const property =
      asked.metric !== 'count' && asked.property ? { property: asked.property } : {}
    await question.run(
      async (signal) => {
        const [counted, crossed] = await Promise.all([
          api.GET('/analytics/aggregate', {
            params: { query: { by: asked.by, metric: 'count' } },
            signal,
          }),
          api.GET('/analytics/heatmap', {
            params: {
              query: { rows: asked.by, cols: asked.cols, metric: asked.metric, ...property },
            },
            signal,
          }),
        ])
        return { counted: unwrap(counted), crossed: unwrap(crossed) }
      },
      (answer) => {
        tiles.value = answer.counted
        heatmap.value = answer.crossed
      },
      () => {
        tiles.value = null
        heatmap.value = null
      },
    )
  }

  return { tiles, heatmap, status: question.status, error: question.error, load }
}
