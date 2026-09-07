// One row of the ArchiMate matrix: what a source type may point at, and how.
//
// Appendix B of the specification is a 61x61 grid, and the backend derives it
// from the same rules it rejects an illegal link with. Recomputing it here
// would mean a second implementation of the metamodel, free to disagree with
// the one that decides — so this asks, and asks for one row at a time.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'
import type { ElementType, RelationshipType } from './useMetamodel'

export type RelationshipRule = components['schemas']['RelationshipRuleRead']

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function useMetamodelRules() {
  /** The type the rows start from; '' while nothing has been asked. */
  const source = ref<ElementType | ''>('')
  const rules = ref<RelationshipRule[]>([])
  const status = ref<Status>('idle')
  const error = ref('')

  const byTarget = computed(
    () => new Map(rules.value.map((rule) => [rule.target, rule.relationships])),
  )

  /** What `source` may open toward one target — empty when nothing may. */
  function allowed(target: ElementType | string): RelationshipType[] {
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
    status.value = 'loading'
    error.value = ''
    try {
      const row = unwrap(
        await api.GET('/metamodel/matrix', { params: { query: { source: sourceType } } }),
      )
      rules.value = row.rules
      status.value = 'ready'
    } catch (caught) {
      rules.value = []
      error.value = messageOf(caught)
      status.value = 'error'
    }
  }

  function clear(): void {
    source.value = ''
    rules.value = []
    status.value = 'idle'
    error.value = ''
  }

  return { source, rules, status, error, allowed, reach, load, clear }
}
