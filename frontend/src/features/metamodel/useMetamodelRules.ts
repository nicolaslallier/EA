// One row of the ArchiMate matrix: what a source type may point at, and how.
//
// Appendix B of the specification is a 61x61 grid, and the backend derives it
// from the same rules it rejects an illegal link with. Recomputing it here
// would mean a second implementation of the metamodel, free to disagree with
// the one that decides — so this asks, and asks for one row at a time.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { ElementType, RelationshipType } from './useMetamodel'

export type RelationshipRule = components['schemas']['RelationshipRuleRead']

export function useMetamodelRules() {
  /** The type the rows start from; '' while nothing has been asked. */
  const source = ref<ElementType | ''>('')
  const rules = ref<RelationshipRule[]>([])
  // Walking the matrix is a click per source type; only the last row counts.
  const row = useLatestRequest()
  const { status, error } = row

  const byTarget = computed(
    () => new Map(rules.value.map((rule) => [rule.target, rule.relationships])),
  )

  /** What `source` may open toward one target — empty when nothing may. */
  function allowed(target: string): RelationshipType[] {
    return byTarget.value.get(target as ElementType) ?? []
  }

  /** How many target types one relationship reaches; '' counts every reachable type. */
  function reach(relationship: RelationshipType | ''): number {
    return rules.value.filter((rule) =>
      relationship ? rule.relationships.includes(relationship) : rule.relationships.length > 0,
    ).length
  }

  async function load(sourceType: ElementType): Promise<void> {
    source.value = sourceType
    await row.run(
      async (signal) =>
        unwrap(
          await api.GET('/metamodel/matrix', { params: { query: { source: sourceType } }, signal }),
        ),
      (answer) => {
        rules.value = answer.rules
      },
      () => {
        rules.value = []
      },
    )
  }

  function clear(): void {
    row.cancel()
    source.value = ''
    rules.value = []
  }

  return { source, rules, status, error, allowed, reach, load, clear }
}
