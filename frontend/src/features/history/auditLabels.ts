// Translation tables, and not one rule: what counts as a change, and which
// action or kind of entity exists, is the backend's. The types come from the
// generated schema and the `Record`s are exhaustive, so the day the backend
// adds an action this file stops compiling instead of showing a raw code.
import type { components } from '../../api/schema'

export type AuditAction = components['schemas']['AuditAction']
export type EntityKind = components['schemas']['EntityKind']

export const ACTION_LABELS: Record<AuditAction, string> = {
  'element.created': 'Élément créé',
  'element.updated': 'Élément modifié',
  'element.deleted': 'Élément supprimé',
  'element.submitted': 'Élément soumis à revue',
  'element.approved': 'Élément approuvé',
  'element.rejected': 'Élément rejeté',
  'element.reopened': 'Élément rouvert',
  'element.locked': 'Élément verrouillé',
  'element.unlocked': 'Élément déverrouillé',
  'element.stereotypes_changed': 'Stéréotypes d\'un élément modifiés',
  'profile.created': 'Profil créé',
  'profile.updated': 'Profil modifié',
  'profile.deleted': 'Profil supprimé',
  'template.created': 'Modèle créé',
  'template.updated': 'Modèle modifié',
  'template.deleted': 'Modèle supprimé',
  'relationship.created': 'Relation créée',
  'relationship.updated': 'Relation modifiée',
  'relationship.deleted': 'Relation supprimée',
  'diagram.created': 'Diagramme créé',
  'diagram.updated': 'Diagramme renommé',
  'diagram.deleted': 'Diagramme supprimé',
  'diagram.layout_replaced': 'Diagramme mis en page',
  'diagram.generated': 'Vue générée',
  'diagram.frozen': 'Vue figée',
  'diagram.unfrozen': 'Vue dégelée',
  'document.attached': 'Document attaché',
  'document.revised': 'Document révisé',
  'document.discarded': 'Document retiré',
  'package.created': 'Paquet créé',
  'package.updated': 'Paquet modifié',
  'package.deleted': 'Paquet supprimé',
  'package.elements_moved': 'Éléments rangés dans un paquet',
  'saved_query.created': 'Requête enregistrée',
  'saved_query.updated': 'Requête modifiée',
  'saved_query.deleted': 'Requête supprimée',
  'baseline.created': 'Baseline capturée',
  'baseline.deleted': 'Baseline supprimée',
  'baseline.restored': 'Baseline restaurée',
  'external_reference.linked': 'Référence externe liée',
  'external_reference.unlinked': 'Référence externe retirée',
  'external_reference.refreshed': 'Référence externe actualisée',
  'package.published': 'Package publié',
  'package.reopened': 'Package rouvert',
  'package.write_refused_frozen': 'Écriture refusée : package gelé',
}

export const KIND_LABELS: Record<EntityKind, string> = {
  element: 'Élément',
  relationship: 'Relation',
  package: 'Paquet',
  saved_query: 'Requête enregistrée',
  diagram: 'Diagramme',
  document: 'Document',
  baseline: 'Baseline',
  external_reference: 'Référence externe',
  profile: 'Profil',
  template: 'Modèle de diagramme',
}

/** A hand-written URL may name anything: only what the labels know is a filter. */
export function isAction(value: string): value is AuditAction {
  return Object.hasOwn(ACTION_LABELS, value)
}

export function isKind(value: string): value is EntityKind {
  return Object.hasOwn(KIND_LABELS, value)
}
