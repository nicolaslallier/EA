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
  /** Nested screens, mounted inside this section's view (which holds a `<RouterView>`). */
  children?: RouteRecordRaw[]
  /** A reading screen: the shell drops the editing menu around it (docs/adr/0064). */
  reader?: boolean
}

export const GROUPS: readonly SectionGroup[] = [
  { id: 'model', label: 'Modèle' },
  { id: 'analysis', label: 'Analyse' },
  { id: 'storage', label: 'Stockage' },
  { id: 'governance', label: 'Gouvernance' },
  { id: 'reading', label: 'Lecture' },
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
    view: () => import('../features/relationships/RelationshipsSection.vue'),
  },
  {
    path: '/metamodele',
    name: 'metamodel',
    label: 'Métamodèle',
    summary: "Les types d'éléments et de relations d'ArchiMate 3.2.",
    group: 'model',
    view: () => import('../features/metamodel/MetamodelSection.vue'),
  },
  {
    path: '/diagrammes',
    name: 'diagrams',
    label: 'Diagrammes',
    summary: 'Composer et enregistrer des vues : glisser des éléments, les relier.',
    group: 'model',
    view: () => import('../features/diagrams/DiagramsSection.vue'),
  },
  {
    path: '/ipam',
    name: 'ipam',
    label: 'Adressage IP',
    summary: "Les sous-réseaux, ce qu'ils contiennent, et ce qui répond sur une adresse.",
    group: 'model',
    view: () => import('../features/ipam/IpamSection.vue'),
  },
  {
    path: '/echanges',
    name: 'exchanges',
    label: 'Échanges',
    summary: 'Ouvrir et échanger le modèle avec Sparx EA et Archi : XML ArchiMate Exchange et CSV.',
    group: 'model',
    view: () => import('../features/exchanges/ExchangesScreen.vue'),
  },
  {
    path: '/analyse/voisinage',
    name: 'neighbourhood',
    label: 'Voisinage',
    summary: "Le sous-graphe autour d'un élément, à profondeur choisie.",
    group: 'analysis',
    view: () => import('../features/neighbourhood/NeighbourhoodSection.vue'),
  },
  {
    path: '/analyse/impact',
    name: 'impact',
    label: 'Analyse d’impact',
    summary: "Ce qui dépend d'un élément, de proche en proche.",
    group: 'analysis',
    view: () => import('../features/impact/ImpactSection.vue'),
  },
  {
    path: '/analyse/couverture',
    name: 'coverage',
    label: 'Couverture des exigences',
    summary: 'Les exigences sans réalisation et sans test.',
    group: 'analysis',
    view: () => import('../features/requirements/CoverageSection.vue'),
  },
  {
    path: '/analyse/feuille-de-route',
    name: 'roadmap',
    label: 'Feuille de route',
    summary: "Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.",
    group: 'analysis',
    view: () => import('../features/roadmap/RoadmapSection.vue'),
  },
  {
    path: '/analyse/matrice',
    name: 'matrix',
    label: 'Matrice',
    summary: 'Croiser deux ensembles d’éléments : qui est relié à quoi, et créer le lien.',
    group: 'analysis',
    view: () => import('../features/matrix/MatrixSection.vue'),
  },
  {
    path: '/analyse/qualite',
    name: 'quality',
    label: 'Qualité',
    summary: 'Orphelins, doublons, nommage, données manquantes : ce qui cloche dans le modèle.',
    group: 'analysis',
    view: () => import('../features/quality/QualitySection.vue'),
  },
  {
    path: '/analyse/tableau-de-bord',
    name: 'dashboard',
    label: 'Tableau de bord',
    summary: 'Répartition du modèle et carte de chaleur : couverture, cycle de vie, coût, risque.',
    group: 'analysis',
    view: () => import('../features/dashboard/DashboardSection.vue'),
  },
  {
    path: '/recherche',
    name: 'search',
    label: 'Recherche',
    summary: 'Chercher par type, paquet, attributs et relations ; garder les requêtes utiles.',
    group: 'analysis',
    view: () => import('../features/search/SearchSection.vue'),
  },
  {
    path: '/fichiers',
    name: 'files',
    label: 'Fichiers',
    summary: 'Déposer, parcourir, télécharger et supprimer des fichiers dans MinIO.',
    group: 'storage',
    view: () => import('../features/files/FilesSection.vue'),
  },
  {
    path: '/history',
    name: 'history',
    label: 'Historique',
    summary: 'Qui a changé quoi, et quand : le journal d’audit du catalogue.',
    group: 'governance',
    view: () => import('../features/history/HistorySection.vue'),
  },
  {
    path: '/baselines',
    name: 'baselines',
    label: 'Baselines',
    summary: 'Figer le modèle, comparer deux états, restaurer.',
    group: 'governance',
    view: () => import('../features/baselines/BaselinesSection.vue'),
  },
  {
    path: '/view',
    name: 'view',
    label: 'Consultation',
    summary: 'Lire le modèle sans pouvoir le modifier : éléments, relations, diagrammes.',
    group: 'reading',
    reader: true,
    view: () => import('../features/view/ViewShell.vue'),
    children: [
      { path: '', name: 'view-home', component: () => import('../features/view/ViewHome.vue') },
      {
        path: 'package/:id',
        name: 'view-package',
        component: () => import('../features/view/ViewPackage.vue'),
      },
      {
        path: 'element/:id',
        name: 'view-element',
        component: () => import('../features/view/ViewElement.vue'),
      },
      {
        path: 'diagram/:id',
        name: 'view-diagram',
        component: () => import('../features/view/ViewDiagram.vue'),
      },
    ],
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
