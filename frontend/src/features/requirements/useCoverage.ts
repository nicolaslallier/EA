// The coverage screen's state: the requirements, what realizes and tests each,
// and the rates — all computed by the server. Not one rate is recomputed here:
// a percentage worked out in the browser would be a second answer to a question
// the server has already answered, free to disagree with it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type CoverageRead = components['schemas']['CoverageRead']
export type RequirementTraceRead = components['schemas']['RequirementTraceRead']

const EMPTY: CoverageRead = {
  total: 0,
  active: 0,
  designed: 0,
  verified: 0,
  design_rate: null,
  verification_rate: null,
  by_status: {},
  by_priority: {},
  requirements: [],
}

export type CoverageFilter = { status?: string; priority?: string }

export function useCoverage() {
  const coverage = ref<CoverageRead>(EMPTY)
  // The filters change faster than the network answers: only the last question
  // may reach the screen.
  const request = useLatestRequest()

  async function load(filter: CoverageFilter = {}): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/requirements/coverage', {
            params: {
              query: { status: filter.status || undefined, priority: filter.priority || undefined },
            },
            signal,
          }),
        ),
      (answer) => {
        coverage.value = answer
      },
    )
  }

  return { coverage, status: request.status, error: request.error, load }
}
