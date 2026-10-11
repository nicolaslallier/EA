// The roadmap as the server answers it: plateaus, work packages and
// deliverables with their lifespans, and the window they span. Which elements
// overlap the window, and which window the data itself implies, are the
// server's to decide; the drawing is `lib/roadmapLayout.ts`'s.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { DayWindow } from '../../lib/roadmapLayout'

export type RoadmapRead = components['schemas']['RoadmapRead']
export type { RoadmapItemRead } from '../../lib/roadmapLayout'

export function useRoadmap() {
  const roadmap = ref<RoadmapRead | null>(null)
  const request = useLatestRequest()

  const window = computed<DayWindow | null>(() => {
    const found = roadmap.value
    return found?.window_from && found.window_to
      ? { from: found.window_from, to: found.window_to }
      : null
  })

  async function load(query: { from?: string; to?: string } = {}): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/roadmap', {
            params: {
              query: {
                ...(query.from ? { from: query.from } : {}),
                ...(query.to ? { to: query.to } : {}),
              },
            },
            signal,
          }),
        ),
      (answer) => {
        roadmap.value = answer
      },
      () => {
        roadmap.value = null
      },
    )
  }

  return { roadmap, window, status: request.status, error: request.error, load }
}
