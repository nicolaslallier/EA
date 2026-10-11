// The quality screen's state: one page of findings, the counts of the run, and
// the next page on request. Every filter change is a new question, so a late
// answer to the previous one is dropped (lib/latest.ts).
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { RuleCode, Severity } from './labels'

export type QualityReportRead = components['schemas']['QualityReportRead']
export type FindingRead = components['schemas']['FindingRead']

export type QualityFilters = { severity: Severity | ''; code: RuleCode | '' }

export function useQuality() {
  const report = ref<QualityReportRead | null>(null)
  const findings = ref<FindingRead[]>([])
  const listing = useLatestRequest()

  function query(filters: QualityFilters, cursor: number) {
    // An empty filter is left out: `severity=` would be a filter matching nothing.
    return {
      ...(filters.severity ? { severity: filters.severity } : {}),
      ...(filters.code ? { code: filters.code } : {}),
      ...(cursor > 0 ? { cursor } : {}),
    }
  }

  async function load(filters: QualityFilters): Promise<void> {
    await listing.run(
      async (signal) =>
        unwrap(await api.GET('/quality', { params: { query: query(filters, 0) }, signal })),
      (answer) => {
        report.value = answer
        findings.value = answer.findings
      },
      () => {
        report.value = null
        findings.value = []
      },
    )
  }

  async function more(filters: QualityFilters): Promise<void> {
    const cursor = report.value?.next_cursor
    if (cursor === null || cursor === undefined) {
      return
    }
    await listing.run(
      async (signal) =>
        unwrap(await api.GET('/quality', { params: { query: query(filters, cursor) }, signal })),
      (answer) => {
        report.value = answer
        findings.value = [...findings.value, ...answer.findings]
      },
    )
  }

  return { report, findings, status: listing.status, error: listing.error, load, more }
}
