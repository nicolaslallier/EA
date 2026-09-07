# 12. L'écran du métamodèle, et la matrice servie par lignes

Date : 2026-09-07
Statut : Accepté

## Contexte

Le métamodèle ArchiMate 3.2 est déjà entièrement dans le backend
(`adr/0005`) : 61 types d'éléments, 11 types de relations, et les règles de
`domain/archimate/rules.py` qui *dérivent* la matrice 61×61 de l'annexe B au
lieu de la recopier. Deux endpoints l'exposaient : `/metamodel`, qui sert la
palette, et `/metamodel/relationships?source=&target=`, qui répond pour *une*
paire — ce dont le panneau de relations a besoin (`adr/0009`), et rien de plus.

Le SPA, lui, n'avait aucun écran pour ça : la section était déclarée *à venir*.
Or c'est le référentiel que tout le reste applique. Un architecte qui se voit
refuser un lien veut savoir *ce qui aurait été permis*, et un nouvel arrivant
veut lire le vocabulaire avant de modéliser. Sans écran, la seule façon de
répondre était d'ouvrir `rules.py`.

Trois questions se posaient.

**Que sait le SPA du métamodèle ?** La tentation permanente est de recopier les
11 relations et leurs familles dans un fichier TypeScript : c'est court, c'est
stable, et ça ne bouge presque jamais. C'est aussi une seconde implémentation
du métamodèle, libre de diverger de celle qui *décide*. Un écran qui promet un
lien que l'API refuse est pire que pas d'écran.

**Comment servir une matrice de 3721 cases ?** L'annexe B est une grille
61×61. La servir entière, c'est un payload que personne ne lit. La reconstruire
depuis `/metamodel/relationships`, c'est 61 requêtes pour une seule ligne.

**`relationship_types` était une liste de chaînes**, alors que `element_types`
était déjà une liste d'objets classés (couche, aspect). La famille d'une
relation, sa force, le sens dans lequel l'impact la parcourt — tout cela vit
dans `relations.py` et n'était exposé nulle part, alors que c'est exactement ce
qu'un écran du métamodèle doit expliquer.

## Décision

**Un endpoint qui sert une *ligne* de la matrice** : `GET
/metamodel/matrix?source=<type>` rend, pour chaque type cible, les relations
permises depuis ce type source. Une ligne, parce qu'un écran en montre une à la
fois : 61 cases au lieu de 3721, et une seule requête au lieu de 61. Les cases
sont calculées à l'appel par `permitted_relationships`, jamais stockées — elles
ne peuvent donc pas dériver de la règle qui refuse le lien.

**`relationship_types` devient une liste d'objets** (`RelationshipTypeRead`),
symétrique de `element_types` : la valeur, la famille, la force, et si l'impact
suit la flèche. Ce sont des faits d'ArchiMate, donc du backend. Le *libellé*
reste au client : le SPA les écrit en verbes français (« sert », « compose »),
et une étiquette anglaise de plus n'aurait servi à personne.

**Le SPA ne déclare rien du métamodèle sauf des mots.** `features/metamodel/`
contient des tables de traduction — `LAYER_LABELS`, `ASPECT_LABELS`,
`CATEGORY_LABELS`, et `RELATIONSHIP_LABELS` chez les relations — et pas une
seule règle. Les types, les familles, les forces, les cases : tout est demandé.

**La question vit dans l'URL** : `?source=` et `?relation=`, comme le voisinage
(`adr/0010`). Une case de la matrice est typiquement ce qu'on envoie à un
collègue pour trancher un désaccord de modélisation. Choisir un type source
*empile* — c'est un pas d'exploration, et cliquer une cible pour en faire la
nouvelle source se défait avec *Précédent* ; changer le filtre de relation
*remplace*.

**La palette est le sélecteur.** Les 61 types sont dessinés en pastilles aux
couleurs de couche (`LAYER_COLOURS`, déjà utilisées par le voisinage), groupées
par couche, et cliquer une pastille écrit `?source=`. Pas de `<select>` en plus
de la liste : la liste *est* le choix.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Recopier les 11 relations et leurs familles dans le SPA | Zéro requête, zéro endpoint | Seconde source de vérité ; un écran qui promet ce que l'API refuse | Écarté : c'est la règle centrale de `CLAUDE.md` |
| Servir la matrice complète (3721 cases) | Une requête pour tout, filtrage local instantané | Payload énorme pour une ligne lue ; invite à mettre la matrice en cache côté client, donc à la faire vieillir | Écarté |
| Reconstruire la ligne avec 61 appels à `/metamodel/relationships` | Aucun endpoint nouveau | 61 requêtes par type affiché | Écarté |
| Ajouter `relationships` à côté de `relationship_types` plutôt que d'enrichir ce dernier | Aucun client à retoucher | Deux champs décrivant la même chose, dont un mort | Écarté : le seul client est ce dépôt, et il tient dans un `.value` |
| Dessiner la matrice en grille 61×61 | Fidèle à l'annexe B | Illisible sur un écran, insupportable au clavier et au lecteur d'écran | Écarté : une ligne à la fois, en tableau |

## Conséquences

- **Le contrat a bougé** : `MetamodelRead.relationship_types` change de forme.
  `backend/openapi.json` et `frontend/src/api/schema.d.ts` sont régénérés, et
  le sélecteur « relation suivie » du voisinage lit désormais `type.value`.
  Aucun client hors de ce dépôt n'existe ; le jour où il y en aura un, ce genre
  de changement demandera une version.
- **`/metamodel/matrix` ne touche pas la base.** Comme le reste de
  `api/metamodel.py`, il répond depuis le domaine pur : il reste disponible
  quand le graphe est injoignable, et il est testable sans base.
- **La ligne non filtrée est peu discriminante** : `association` étant permise
  entre presque tout, « 61 types sur 61 » est la réponse habituelle. C'est le
  métamodèle qui est ainsi, pas l'écran ; le filtre par relation est ce qui rend
  la question intéressante (« 39 types sur 61 acceptent *sert* »).
- **Les jonctions restent affichées comme les autres types.** Une `Junction` ne
  modélise rien par elle-même et hérite de la légalité de ce qu'elle relaie ;
  ses lignes sont donc anormalement permissives. Le jour où cela trompe
  quelqu'un, c'est une note dans l'écran, pas une exception dans les règles.
- **Ce que l'écran n'est pas** : il ne montre ni les relations *dérivées*
  (chaîne réduite à son maillon le plus faible), ni les cardinalités, ni les
  vues standard d'ArchiMate. `strength` est publié, ce qui donne de quoi
  expliquer la dérivation le jour où on la calculera.
- **La section `metamodel` a maintenant une `view`** dans `router/sections.ts` :
  elle n'est plus grisée dans le menu. Il reste `impact` en *à venir*, ce que le
  test de `AppNav` exige (il vérifie qu'au moins une section annoncée n'a pas
  d'écran).

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md),
  [0007](0007-client-openapi-genere-pour-le-spa.md),
  [0008](0008-menu-et-routage-du-spa.md),
  [0009](0009-association-des-elements-dans-le-spa.md),
  [0010](0010-dessiner-le-voisinage-d-un-element.md)
- Code concerné : `backend/src/ea/api/metamodel.py`,
  `backend/src/ea/api/schemas.py` (`RelationshipTypeRead`,
  `RelationshipMatrixRead`),
  `frontend/src/features/metamodel/MetamodelSection.vue`,
  `frontend/src/features/metamodel/useMetamodelRules.ts`,
  `frontend/src/features/metamodel/useMetamodel.ts`
