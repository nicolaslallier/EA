# 10. Dessiner le voisinage d'un élément

Date : 2026-09-07
Statut : Accepté

## Contexte

`docs/adr/0009` s'est terminé sur une dette explicite : « les traversées
`/neighbourhood` et `/impact` restent sans interface. Elles demandent un
dessin, pas un tableau, et c'est un autre travail. » C'est ce travail.

Le backend répond depuis le début. `GET /elements/{id}/neighbourhood?depth=`
renvoie un `GraphRead` — les nœuds *et* les arêtes entre eux — en suivant les
liens dans les deux sens, jusqu'à `MAX_TRAVERSAL_DEPTH = 10`. Rien dans le SPA
n'y accédait : c'était la seule des deux raisons d'avoir choisi un graphe
(`adr/0004`) qui n'avait aucun écran.

Trois questions se posaient, et aucune n'était tranchée par les ADR précédents.

**Avec quoi dessiner ?** Un graphe se dessine d'ordinaire avec une
bibliothèque — d3-force, Cytoscape, vis-network. Aucune n'est dans la pile
(`CLAUDE.md`), donc chacune est une décision structurelle à part entière.

**Quelle disposition ?** Un placement par forces est le réflexe, mais il répond
à une autre question que celle posée : il montre des *grappes*, pas des
*distances*, et il place le même sous-graphe ailleurs à chaque exécution — donc
aucun test ne peut affirmer une coordonnée, et deux captures d'écran du même
élément ne se comparent pas.

**Où vit la question ?** « Quel élément, à quelle profondeur, en suivant quelles
relations » est un état. Dans un `ref` local, il meurt avec le composant et ne
s'envoie pas à un collègue.

## Décision

**Du SVG écrit à la main, sans bibliothèque de rendu de graphe.** Le
sous-graphe est borné par construction — la profondeur est plafonnée côté API —
et il n'a besoin ni de simulation physique, ni de moteur de layout. Ce qui
resterait à écrire par-dessus une bibliothèque (la sémantique ArchiMate, les
couleurs de couche, l'accessibilité au clavier) coûte plus cher que la
géométrie elle-même.

**Des anneaux concentriques, un par saut**, le sujet au centre
(`features/neighbourhood/layout.ts`).

* La lecture est immédiate et c'est exactement la question : un élément du
  deuxième anneau est à deux relations.
* Le calcul est **pur et déterministe** — pas d'itération, pas de hasard, pas
  d'animation. Il est donc testé unitairement sans monter le moindre composant,
  et le même sous-graphe se dessine toujours pareil.
* Le parcours de largeur ignore le sens des flèches, comme la traversée qu'il
  affiche : « ce qui entoure » n'est pas une question de direction.
* Un anneau encombré **s'écarte** au lieu de superposer ses boîtes : son rayon
  est le plus grand du pas régulier et de la circonférence qu'il lui faut. Un
  hub de vingt voisins reste lisible.
* Deux liens entre les mêmes éléments s'incurvent de part et d'autre de la
  droite, un lien réflexif devient une boucle : aucune arête ne se cache
  derrière une autre.

**La question vit dans l'URL** : `/analyse/voisinage?element=…&depth=…&relation=…`.
Trois conséquences, et c'est pour elles que c'est fait : la vue s'envoie par
lien, le bouton *Précédent* remonte l'exploration, et le composant n'a qu'un
seul chemin de code — que l'élément vienne du sélecteur, d'un clic sur le
dessin ou de la barre d'adresse. Changer de sujet *empile* une entrée
d'historique ; tourner un bouton sur le même sujet la *remplace*, sinon dix
changements de profondeur enterrent l'élément précédent.

**Un clic sur un voisin déplace le centre.** Explorer une architecture est une
marche, pas une requête ; c'est toute l'interaction de l'écran.

**Les couleurs sont celles d'ArchiMate**, une par couche, déclarées à côté de
`LAYER_LABELS` dans `features/metamodel/useMetamodel.ts`. Ce sont des fonds
clairs dans les deux thèmes, donc une boîte impose son encre foncée au lieu
d'hériter de `var(--text)` qui s'inverse la nuit.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Cytoscape.js, d3-force, vis-network | Layouts, zoom et pan tout faits | Dépendance structurelle pour ~150 lignes d'arithmétique ; placement non déterministe donc intestable ; accessibilité clavier à reconstruire par-dessus un `<canvas>` | Écarté |
| Mermaid ou Graphviz (WASM) | Dessine seul, à partir d'un texte | Aucune interaction — or le clic qui déplace le centre *est* la fonctionnalité ; rendu opaque au test | Écarté |
| Un tableau des voisins | Trivial, déjà écrit deux fois | Ne répond pas à la question : « ce qui entoure » est une forme, pas une liste. C'est déjà l'écran *Relations* | Écarté |
| Placement par forces écrit à la main | Belles grappes | Le hasard et l'animation, sans la bibliothèque | Écarté |
| SVG + anneaux concentriques | Déterministe, testable, accessible, zéro dépendance | Il faut écrire la géométrie | **Retenu** |

## Conséquences

- **Aucune dépendance nouvelle.** `make check` couvre la géométrie comme le
  reste : `layout.ts` est un module de fonctions pures et ses tests affirment
  des coordonnées.
- **Le dessin ne monte pas à l'échelle indéfiniment.** Passé quelques dizaines
  de boîtes, les anneaux se chevauchent malgré leur écartement et les intitulés
  deviennent illisibles. Les deux garde-fous d'aujourd'hui sont la profondeur et
  le filtre par type de relation, tous deux côté serveur. Le jour où un
  voisinage utile en dépasse le cadre, il faudra une fenêtre panoramique et du
  regroupement — et ce sera un autre ADR, pas une rustine ici.
- **Pas de déplacement à la souris** : la zone défile, et trois boutons règlent
  l'échelle. Le dessin arrive rétréci juste assez pour se montrer, jamais en
  deçà du seuil où un nom cesse d'être lisible ; *Ajuster* va plus loin, à la
  demande.
- **`RELATIONSHIP_LABELS` a quitté `RelationshipPanel.vue`** pour
  `features/relationships/labels.ts` : deux écrans écrivent désormais les mêmes
  liens, et la même arête doit se lire « sert » des deux côtés.
- **`MAX_TRAVERSAL_DEPTH` est répété** dans le frontend (`MAX_DEPTH`). Les
  bornes numériques d'OpenAPI ne survivent pas au générateur de types, et cette
  constante borne un `<select>`, pas une validation : au-delà, c'est l'API qui
  refuse, et c'est son message que l'utilisateur lit.
- **`/impact` devient facile.** Il répond le même `GraphRead` et se dessinerait
  avec les mêmes composants, l'anneau signifiant alors « à quelle distance
  l'impact se propage ». C'est l'étape suivante, et elle n'aura pas à décider
  tout ceci une deuxième fois.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0008](0008-menu-et-routage-du-spa.md),
  [0009](0009-association-des-elements-dans-le-spa.md)
- Code concerné : `frontend/src/features/neighbourhood/`,
  `frontend/src/router/sections.ts`,
  `backend/src/ea/api/architecture.py` (`read_neighbourhood`)
