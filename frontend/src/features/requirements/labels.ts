// Translation tables, and not one rule: which statuses, priorities and gaps
// exist is the API's — the screen lists the keys the answer carries and only
// puts words on them, exactly as `LAYER_LABELS` does for the layers.

export const STATUS_LABELS: Record<string, string> = {
  proposed: 'Proposée',
  approved: 'Approuvée',
  implemented: 'Implémentée',
  verified: 'Vérifiée',
  rejected: 'Rejetée',
  unset: 'Sans statut',
}

export const PRIORITY_LABELS: Record<string, string> = {
  must: 'Indispensable',
  should: 'Importante',
  could: 'Souhaitable',
  unset: 'Sans priorité',
}

export const GAP_LABELS: Record<string, string> = {
  design: 'Non réalisée',
  verification: 'Non testée',
  none: 'Aucun écart',
}

/** The words for a key, or the key itself when a newer API sends one this table lacks. */
export function labelOf(table: Record<string, string>, key: string): string {
  return table[key] ?? key
}
