// Translation tables, and not one rule: which transitions exist and who may take
// them is the API's (`allowed_transitions`). A new state or transition on the
// backend is a TypeScript error here, since both records are exhaustive.
import type { components } from '../../api/schema'

export type ApprovalState = components['schemas']['ApprovalState']
export type Transition = components['schemas']['Transition']

export const STATE_LABELS: Record<ApprovalState, string> = {
  draft: 'Brouillon',
  in_review: 'En revue',
  approved: 'Approuvé',
}

export const TRANSITION_LABELS: Record<Transition, string> = {
  submit: 'Soumettre à revue',
  approve: 'Approuver',
  reject: 'Rejeter',
  reopen: 'Rouvrir',
  lock: 'Verrouiller',
  unlock: 'Déverrouiller',
}
