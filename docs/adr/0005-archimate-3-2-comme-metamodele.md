# 5. ArchiMate 3.2 comme métamodèle du référentiel

Date : 2026-09-07
Statut : Accepté

## Contexte

Un référentiel d'architecture doit décider ce qu'est un « élément ». Trois
possibilités : un métamodèle maison, un sous-ensemble d'ArchiMate, ou ArchiMate
complet.

Un métamodèle maison est rapide à écrire et ne s'échange avec rien. ArchiMate
est la norme de l'Open Group ; c'est le vocabulaire des outils du marché
(Archi, BiZZdesign, Sparx) et celui que connaissent déjà les architectes.

## Décision

Le métamodèle est **ArchiMate 3.2 complet** : les 61 types d'éléments et les 11
types de relations, avec la validation des couples autorisés.

Concrètement :

- `domain/archimate/taxonomy.py` déclare les 61 types. Chacun porte sa couche,
  son aspect et son raffinement (interface, service, événement, collaboration,
  connecteur), qui sont les coordonnées dont dépendent toutes les règles ;
- `domain/archimate/relations.py` déclare les 11 relations, leur famille et
  leur force ;
- `domain/archimate/rules.py` répond à « ce lien est-il légal entre ces deux
  types ? ».

### La matrice des relations autorisées est calculée, pas recopiée

L'annexe B de la spécification publie une matrice de 61 × 61 cases. Cette
matrice n'est pas écrite à la main par ses auteurs : elle est **dérivée** des
règles structurelles du métamodèle. `rules.py` encode ces règles directement —
« l'accès va d'un comportement vers une structure passive », « l'affectation va
d'une structure active vers ce qu'elle exécute », « la réalisation ne monte
jamais l'échelle d'abstraction à l'envers ».

Ce choix a trois conséquences assumées :

1. un refus est **explicable** : le message d'erreur nomme la règle enfreinte,
   au lieu de dire qu'une case vaut zéro ;
2. les 3 721 cases ne peuvent pas diverger entre elles, puisqu'elles n'existent
   pas comme données ;
3. **la couverture n'est pas prouvée case par case.** Les couples que la
   spécification autorise sans qu'ils découlent des règles générales — un
   Produit qui agrège les Services qu'il assemble, un Artefact qui réalise le
   Composant qu'il empaquette — sont listés dans `_EXTRA_ALLOWED`. C'est le
   point d'extension : ajouter un couple, c'est une ligne et un test. Si un
   usage réel fait apparaître un couple manquant, c'est là qu'il se corrige.

Un couple refusé à tort se voit tout de suite (l'utilisateur ne peut pas créer
un lien légitime) ; un couple accepté à tort passe inaperçu. Les règles sont
donc écrites du côté permissif quand la spécification est ambiguë, et
`ASSOCIATION`, que la norme autorise entre n'importe quels éléments, sert de
porte de sortie.

### Ce que le métamodèle refuse aussi

- une jonction n'est pas un élément catalogable : c'est un connecteur ;
- un élément ne se compose ni ne se spécialise lui-même ;
- le type d'un élément ne se modifie pas après création — cela invaliderait des
  relations déjà stockées, ce qui est une migration et non une édition.

La cyclicité de la composition, elle, ne se décide pas à partir de deux types :
elle se vérifie contre le graphe, dans `services/`.

## Conséquences

- Le frontend ne redéclare jamais les 61 types : il interroge `GET /metamodel`
  et `GET /metamodel/relationships?source=…&target=…`, servis par le code même
  qui valide les écritures.
- Les valeurs des énumérations (`application_component`, `serving`) sont la
  forme de transport *et* de stockage. Les renommer est une rupture de contrat.
- L'export vers le format d'échange ArchiMate (Open Exchange File) n'est pas
  implémenté, mais rien ne s'y oppose : la taxonomie est complète.
- Un ADR ultérieur pourra remplacer `_EXTRA_ALLOWED` par la matrice de l'annexe
  B chargée en données si le besoin d'une conformité prouvée case par case
  apparaît. La forme de `permits()` ne changerait pas.
