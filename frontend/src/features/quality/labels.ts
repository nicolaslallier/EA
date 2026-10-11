// How a rule and a severity read. Translation only: whether an element is an
// orphan, or a name a duplicate, is decided by the API — these tables restate
// no rule (docs/adr/0012).
import type { components } from '../../api/schema'

export type RuleCode = components['schemas']['RuleCode']
export type Severity = components['schemas']['Severity']

export const RULE_LABELS: Record<RuleCode, string> = {
  orphan: 'Orphelin',
  duplicate_probable: 'Doublon probable',
  naming: 'Nommage',
  missing_property: 'Propriété manquante',
  no_description: 'Sans description',
  inconsistent_relationship: 'Relation incohérente',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  error: 'Erreur',
  warning: 'Avertissement',
  info: 'Information',
}
