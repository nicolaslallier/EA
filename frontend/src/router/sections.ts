// The catalogue of sections the application offers.
//
// This file is the single place a section is declared: adding one means adding
// an entry here, and both the router and the menu follow. A section whose
// `view` is missing is one the backend already serves but the SPA does not
// draw yet — the menu announces it, greyed out, and the router refuses to
// pretend it exists.
import type { RouteRecordRaw } from 'vue-router'

/** A heading in the menu, with its sections underneath. */
export type SectionGroup = {
  id: string
  label: string
}

export type Section = {
  /** The URL of the section, and its identity in the menu. */
  path: string
  /** The route name, so navigation never hardcodes a path twice. */
  name: string
  label: string
  /** One line explaining the section; the menu shows it as a tooltip. */
  summary: string
  /** The `id` of the group this section belongs to. */
  group: SectionGroup['id']
  /** The screen, lazily loaded. Absent means "declared, not built yet". */
  view?: RouteRecordRaw['component']
}

export const GROUPS: readonly SectionGroup[] = [
  { id: 'model', label: 'Modèle' },
  { id: 'analysis', label: 'Analyse' },
]

export const SECTIONS: readonly Section[] = [
  {
    path: '/elements',
    name: 'elements',
    label: 'Éléments',
    summary: "Parcourir, créer, modifier et supprimer les éléments d'architecture.",
    group: 'model',
    view: () => import('../features/elements/ElementCatalogue.vue'),
  },
  {
    path: '/relations',
    name: 'relationships',
    label: 'Relations',
    summary: 'Relier les éléments, sous les règles du métamodèle.',
    group: 'model',
  },
  {
    path: '/metamodele',
    name: 'metamodel',
    label: 'Métamodèle',
    summary: "Les types d'éléments et de relations d'ArchiMate 3.2.",
    group: 'model',
  },
  {
    path: '/analyse/voisinage',
    name: 'neighbourhood',
    label: 'Voisinage',
    summary: "Le sous-graphe autour d'un élément, à profondeur choisie.",
    group: 'analysis',
  },
  {
    path: '/analyse/impact',
    name: 'impact',
    label: 'Analyse d’impact',
    summary: "Ce qui dépend d'un élément, de proche en proche.",
    group: 'analysis',
  },
]

/** A section that really has a screen — the only kind the router mounts. */
export type BuiltSection = Section & { view: NonNullable<Section['view']> }

export function isBuilt(section: Section): section is BuiltSection {
  return section.view !== undefined
}

/** Where `/` lands: the first section that actually has a screen. */
export const HOME: string = SECTIONS.find(isBuilt)?.path ?? '/'

export type MenuEntry = {
  group: SectionGroup
  sections: Section[]
}

/** The sections grouped for display, in the declared order, empty groups dropped. */
export function menu(): MenuEntry[] {
  return GROUPS.map((group) => ({
    group,
    sections: SECTIONS.filter((section) => section.group === group.id),
  })).filter((entry) => entry.sections.length > 0)
}
