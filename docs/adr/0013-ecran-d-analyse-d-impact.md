# 13. L'écran d'analyse d'impact, et le dessin partagé par les deux parcours

Date : 2026-09-07
Statut : Accepté

## Contexte

`/impact` est la deuxième des deux raisons d'avoir choisi un graphe
(`adr/0004`) — « si ce serveur tombe, quels processus métier s'arrêtent ? » —
et la dernière section du menu restée *à venir*. Le backend y répond depuis le
début : `GET /elements/{id}/impact?depth=` parcourt chaque saut **dans le sens
où la dépendance court**, qui n'est pas toujours celui de la flèche, et renvoie
un `GraphRead`.

`adr/0010` avait prédit que ce serait facile : « il répond le même `GraphRead`
et se dessinerait avec les mêmes composants, l'anneau signifiant alors à quelle
distance l'impact se propage ». C'est vrai pour la géométrie, et faux pour deux
choses qu'il fallait trancher.

**Le `GraphRead` ne porte pas les distances.** La réponse contient les éléments
atteints et toutes les arêtes entre eux, mais pas « à combien de sauts ». Or
`layout.ts` calculait lui-même les sauts, **en ignorant le sens des flèches** —
ce qui est la bonne question pour un voisinage et la mauvaise pour un impact :
un élément que la panne n'atteint jamais s'y retrouverait sur le premier
anneau, à côté de ceux qu'elle casse.

**Le sens de propagation est une donnée du métamodèle.** `SERVING` porte la
panne dans le sens de sa flèche, `COMPOSITION` à contresens. Onze booléens
recopiés en TypeScript seraient un second métamodèle, libre de dessiner une
cascade que l'API n'a jamais parcourue — exactement ce que `adr/0012` interdit.

## Décision

**Le dessin est partagé, la distance est injectée.** `layout.ts` devient
`src/lib/graphLayout.ts` et `NeighbourhoodGraph.vue` devient
`src/components/GraphDiagram.vue` : deux écrans dessinent la même forme et en
disent deux choses différentes. `layout(graph, rootId, hops?)` **accepte** la
carte des distances au lieu d'exiger de la calculer ; à défaut, elle retombe
sur le parcours non orienté, qui est la question du voisinage.

**La cascade est recalculée côté client, sur les arêtes que le serveur a
renvoyées** (`features/impact/propagation.ts`), avec `impact_follows_direction`
**passé en paramètre**. Ce booléen est servi par `/metamodel` pour chacun des
onze types, et `useMetamodel` gagne `followsArrow()` — le seul endroit du SPA
qui le lit. Le module reste pur et déterministe, comme la géométrie : ses vagues
se testent sans monter le moindre composant.

Le parcours est **en largeur**, donc la distance d'un élément est la plus courte
chaîne de dépendances qui l'atteint — la réponse à « à quelle distance est-ce
que ça casse », que la plus longue surestimerait.

**La réponse est donnée deux fois, exprès.** Le dessin montre la *forme* de la
cascade ; la liste en dessous est le verdict qu'on lit à voix haute en comité de
changement, par vagues, du plus proche au plus lointain, et **par ordre
alphabétique à l'intérieur d'une vague** — c'est une liste, l'ordre du serveur
n'y veut rien dire. Le dessin, lui, garde cet ordre : il le rend stable d'un
chargement à l'autre.

**Un lien qui n'explique aucune distance est dessiné en pointillé.** Une arête
appartient à la cascade quand c'est elle qui met son extrémité un saut plus
loin. Les autres — entre deux éléments de la même vague, ou revenant vers
l'intérieur — sont du contexte : les effacer mentirait, les dessiner pleines
laisserait croire qu'elles sont des étapes.

**La question vit dans l'URL**, `?element=&depth=&relation=`, comme le voisinage
(`adr/0010`) : changer de sujet empile une entrée d'historique, tourner un
bouton la remplace. La profondeur par défaut est **5**, celle de l'API : un
impact qu'on plafonnerait à un saut ne répondrait pas à la question posée.

**Un clic sur un élément impacté poursuit l'enquête à partir de lui.**

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Faire renvoyer les distances par l'API (`hops` sur chaque élément) | Une seule vérité, calculée là où le parcours a lieu | Change le schéma partagé par les deux parcours et le panneau de relations pour un besoin d'affichage ; le client refait de toute façon un parcours pour savoir quelle arête explique quoi | Écarté — à reconsidérer si les deux calculs divergent |
| Recopier `impact_follows_direction` dans une table TypeScript | Aucun appel réseau, pas d'état de chargement | Un second métamodèle, libre de dessiner une cascade que l'API n'a pas parcourue (`adr/0012`) | Écarté |
| Réutiliser `hopsFrom` non orienté pour l'impact | Zéro code | Répond à une autre question : place sur le premier anneau ce que la panne n'atteint pas | Écarté |
| Dupliquer le SVG dans `features/impact/` | Aucun refactoring | ~250 lignes de dessin en double, qui divergeront | Écarté |
| Une liste seule, sans dessin | Trivial | La propagation est une forme ; c'est déjà l'écran *Relations* | Écarté |
| Dessin partagé + distances injectées | Une géométrie, deux questions ; tout reste testable sans montage | Un déplacement de fichiers et trois props de plus | **Retenu** |

## Conséquences

- **Toutes les sections déclarées ont un écran.** Le test de `AppNav` qui
  exigeait « au moins une section annoncée sans écran » (`adr/0012`) ne pouvait
  plus rien affirmer. Le menu prend désormais ses entrées en **prop**, par
  défaut `menu()`, et le test lui fournit un catalogue contenant une section non
  construite : le badge *à venir* reste couvert le jour où la prochaine section
  sera déclarée avant d'être écrite.
- **`layout.ts` et `NeighbourhoodGraph.vue` ont changé d'adresse.**
  `src/lib/graphLayout.ts` et `src/components/GraphDiagram.vue` : la géométrie
  et le dessin ne sont plus la propriété d'un écran. `GraphDiagram` prend
  maintenant `hops`, `inert`, `caption` et `focusAction` ; tous ont une valeur
  par défaut, donc le voisinage n'a changé que d'import.
- **Deux parcours en largeur coexistent**, l'un orienté et l'autre pas. Ce n'est
  pas une duplication : ce sont deux questions. Ils vivent côte à côte,
  `hopsFrom` dans la géométrie et `propagate` dans l'impact, chacun testé pour
  ce qu'il affirme.
- **Le client peut, en théorie, diverger du serveur.** Les deux appliquent la
  même règle aux mêmes arêtes, mais rien ne le vérifie de bout en bout tant que
  Playwright n'est pas en place. Le symptôme serait visible et non silencieux :
  un élément que le client n'atteint pas est dessiné sur un anneau *au-delà* du
  dernier, jamais perdu.
- **Le palier d'illisibilité de `adr/0010` est plus proche ici.** Un impact à
  cinq sauts ramène plus de boîtes qu'un voisinage à un saut. Les garde-fous
  restent la profondeur et le filtre par type de relation, tous deux côté
  serveur ; la liste, elle, reste lisible à n'importe quelle taille. Le jour où
  ça ne suffira plus, ce sera une vue panoramique et du regroupement — un autre
  ADR, pas une rustine.
- **La cascade se redessine quand le métamodèle arrive.** `/metamodel` et
  `/impact` sont demandés en parallèle ; tant que le premier n'a pas répondu,
  `followsArrow` répond « suit la flèche », ce qu'ont neuf types sur onze, et le
  `computed` refait la cascade dès que la palette atterrit.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0005](0005-archimate-3-2-comme-metamodele.md),
  [0008](0008-menu-et-routage-du-spa.md),
  [0010](0010-dessiner-le-voisinage-d-un-element.md),
  [0012](0012-ecran-du-metamodele.md)
- Code concerné : `frontend/src/features/impact/`,
  `frontend/src/lib/graphLayout.ts`,
  `frontend/src/components/GraphDiagram.vue`,
  `frontend/src/components/AppNav.vue`,
  `frontend/src/features/metamodel/useMetamodel.ts` (`followsArrow`),
  `frontend/src/router/sections.ts`,
  `backend/src/ea/api/architecture.py` (`read_impact`),
  `backend/src/ea/domain/archimate/relations.py` (`impact_follows_direction`)
