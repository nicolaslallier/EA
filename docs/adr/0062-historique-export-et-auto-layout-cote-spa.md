---
titre: Historique, export et auto-layout du constructeur de diagrammes, côté SPA
date: 2026-10-11
statut: Proposition
affects: frontend/src/lib/diagramGeometry.ts, frontend/src/lib/diagramHistory.ts, frontend/src/lib/diagramExport.ts, frontend/src/lib/autoLayout.ts, frontend/src/features/diagrams/useDiagram.ts, frontend/src/features/diagrams/DiagramCanvas.vue
---

# 62. Historique, export et auto-layout côté SPA

Date : 2026-10-11
Statut : Proposition

## Contexte

[`0031`](0031-diagrammes-enregistres.md) et [`0035`](0035-taille-des-boites-de-diagramme.md) donnent un
constructeur qui pose des boîtes, les déplace, les redimensionne et enregistre après chaque geste.
Un architecte attend davantage : sélectionner plusieurs boîtes, zoomer et se déplacer sur la toile,
revenir en arrière, aligner, obtenir une image à joindre à un document, et — pour les vues générées —
une mise en page automatique. Chacun de ces besoins tente une dépendance (une bibliothèque de diagrammes,
une bibliothèque d'annulation, `html2canvas`, `dagre`). [`0010`](0010-dessiner-le-voisinage-d-un-element.md)
et [`0013`](0013-ecran-d-analyse-d-impact.md) ont déjà posé la règle : le graphe se dessine à la main,
sur des modules purs testés sans monter de composant.

## Décision

**Tout cela s'écrit dans `frontend/src/lib/`, en fonctions pures testées sans DOM, et n'ajoute aucune
dépendance.**

| Sujet | Choix |
|---|---|
| Géométrie | `diagramGeometry.ts` : zoom centré sous le curseur (`zoomAt`), pan, rectangle de sélection, alignement et répartition. Le zoom reste dans [0,4 ; 2] |
| Historique | `diagramHistory.ts` : pile de commandes `{do, undo}` déjà appliquées, plafond 100, fusion des micro-gestes par clé (flèches du clavier), un glisser = une entrée |
| Portée de l'annulation | Les boîtes (et, plus tard, notes, groupes, tracés). **Pas le graphe** : une relation créée depuis un lien est un fait, `DELETE /relationships` la retire, explicitement |
| Durée de vie | Mémoire de la session d'édition ; vidée à l'ouverture d'un diagramme et après un conflit de révision. Un historique rechargé annulerait la disposition d'un collègue |
| Annuler et enregistrer | Annuler restaure l'état local puis demande l'enregistrement habituel (anti-rebond) : il n'existe pas de « défaire côté serveur » |
| Export | `serializeSvg` fabrique un SVG autonome (palette claire fixe, styles en ligne, texte échappé, couleurs `#rrggbb` validées, ni `<script>` ni lien externe). Le PNG est ce SVG dessiné sur un `<canvas>` (1x/2x) |
| Auto-layout | `autoLayout.ts` (lot des vues générées) : rangées par couche ArchiMate, tri par barycentre, déterministe ; il ne partage avec les anneaux du voisinage que `BOX_WIDTH`/`BOX_HEIGHT` |
| Clavier | Chaque geste a un équivalent : flèches ±1 px (Maj ±10), Alt+flèches redimensionnent, Ctrl/Cmd+A, Suppr, Ctrl+Z / Ctrl+Maj+Z |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Une bibliothèque de diagrammes (JointJS, React Flow, Cytoscape) | Sélection, zoom, annulation et routage fournis | Dépendance lourde, modèle de données propre qui concurrence celui des vues ; contredit 0010/0013 | Écarté |
| `html2canvas` ou copie du DOM pour l'export | Aucun code de sérialisation | Rend la page telle que le thème sombre la colore, polices et variables CSS incluses : une image qui change avec le visiteur | Écarté |
| Annulation par instantané du serveur (versions) | Survit au rechargement | Un historique de versions est le sujet d'un autre sous-projet ; ici le besoin est le droit à l'erreur de la séance | Écarté |
| Auto-layout par force (type `d3-force`) | Beau sur un graphe quelconque | Non déterministe, dépendance ; ArchiMate a une lecture en couches que le dessin doit montrer | Écarté |
| Annuler aussi les relations créées | Un seul « Annuler » | Une annulation silencieuse d'un fait du graphe, vu par les autres éditeurs et par l'analyse d'impact | Écarté |

## Conséquences

- **Le PNG dépend du navigateur** (`canvas`, polices) ; le SVG est la référence. `rasterize` n'est pas
  testable sous jsdom : il est couvert par un module simulé dans les specs de la section.
- **L'historique est borné à 100 entrées** et perdu au rechargement : c'est voulu.
- **Un conflit de révision vide l'historique** : l'état local « recollé » repart d'une base neuve.
- **Pas de Playwright** : les parcours (annuler, exporter) sont couverts par des composants Vitest.
- Les modules de `lib/` n'importent rien de `features/` : la scène d'export est construite côté
  `features/diagrams/exportScene.ts`, qui connaît les couleurs de couche et les verbes de relation.

## Références

- ADR liés : [0010](0010-dessiner-le-voisinage-d-un-element.md), [0013](0013-ecran-d-analyse-d-impact.md),
  [0031](0031-diagrammes-enregistres.md), [0035](0035-taille-des-boites-de-diagramme.md)
- Code concerné : `frontend/src/lib/diagramGeometry.ts`, `frontend/src/lib/diagramHistory.ts`,
  `frontend/src/lib/diagramExport.ts`, `frontend/src/features/diagrams/useDiagram.ts`
