// How a relationship reads in a sentence.
//
// The wire values stay the ArchiMate ones; these are the verbs of "source
// <verbe> cible", so a row — or an edge on a drawing — is a sentence rather
// than a code. They live in their own module because two screens write the
// same links: the relations panel and the neighbourhood diagram.
import type { AccessType, RelationshipType } from './useElementRelationships'

export const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  composition: 'compose',
  aggregation: 'agrège',
  assignment: 'est affecté à',
  realization: 'réalise',
  serving: 'sert',
  access: 'accède à',
  influence: 'influence',
  association: 'est associé à',
  triggering: 'déclenche',
  flow: 'alimente',
  specialization: 'spécialise',
}

export const ACCESS_LABELS: Record<AccessType, string> = {
  access: 'accède',
  read: 'lit',
  write: 'écrit',
  read_write: 'lit et écrit',
}

/** How a stored link reads, qualifier included where it carries meaning. */
export function verbOf(
  relationship: RelationshipType,
  access?: AccessType | null,
): string {
  if (relationship === 'access' && access) {
    return ACCESS_LABELS[access]
  }
  return RELATIONSHIP_LABELS[relationship]
}
