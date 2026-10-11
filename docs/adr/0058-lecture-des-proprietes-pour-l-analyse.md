---
titre: Lire les propriétés pour l'analyse, et colorer côté serveur
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/analytics.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/api/schemas.py, backend/src/ea/mcp/server.py, frontend/src/features/dashboard, frontend/src/lib/heatmapLayout.ts, CLAUDE.md
---

# 58. Lire les propriétés pour l'analyse, et colorer côté serveur

Date : 2026-10-10
Statut : Proposition

## Contexte

Le tableau de bord veut répondre à « combien de composants en fin de vie, par
couche ? », « quel coût cumulé ? », « où est le risque ? ». Ces trois quantités
(`lifecycle`, `cost`, `risk`) sont des clés de `properties`, une `jsonb` dont les
valeurs sont des chaînes (`adr/0033`). Trois questions se posent : faut-il les
typer, où se fait l'agrégation, et qui décide de la couleur.

Typer le stockage obligerait une migration de données et un schéma de
propriétés, pour trois clés. Calculer les couleurs dans le SPA y mettrait des
seuils, c'est-à-dire une règle, ce que `adr/0012` interdit. Et une bibliothèque
de graphiques pour une grille de rectangles ajouterait une dépendance
(`CLAUDE.md`, « Drawing a graph »).

## Décision

1. **Le stockage ne change pas.** `properties` reste `Mapping[str, str]`. Le
   domaine gagne un *catalogue de lectures* (`ANALYTIC_PROPERTIES`) : `lifecycle`
   et `risk` sont des énumérations ordonnées, `cost` un nombre. Toute autre clé est
   un libellé nominal : on la compte, on ne l'additionne pas.
2. **Une valeur illisible n'est jamais ignorée en silence.** Un nombre accepte `.`
   ou `,` décimal et l'espace, l'espace insécable (U+00A0) et l'espace fine
   insécable (U+202F) comme séparateurs de milliers ; tout le reste (`1 200 €`,
   `1.200,50`, un exposant) est illisible, parce que deviner une graphie
   transformerait une faute de frappe en total faux. Les illisibles sont comptés
   (`unreadable`), isolés dans un seau (`__invalid__` pour une énumération,
   `__unset__` pour une valeur absente) et signalés par une règle de qualité
   `unreadable_property` (information). Écrire reste libre : valider à l'écriture
   est un chantier ultérieur.
3. **L'agrégation se fait en SQL, la classification une seule fois.** La requête
   groupe par la valeur *brute* de chaque dimension et renvoie des sommes
   partielles ; `summarise` replie les graphies (casse, accents, ordre déclaré),
   ordonne, coupe à `MAX_BUCKETS = 200` et colore. `PostgreSQL lower()` dépend de
   la locale de la base : le laisser décider de ce qu'est « Élevé » ferait diverger
   le SQL et la référence en mémoire (`group_elements`), contre laquelle un test
   d'intégration compare sur des valeurs sales. La clé de propriété suit le motif
   de `domain/model.py` puis est un paramètre lié ; `GROUP BY` se fait par rang de
   colonne ; le cast numérique est derrière un `CASE` sur le motif.
4. **Le serveur décide de la couleur, sous la forme d'un `level` de 0 à 4.** Pour
   une énumération déclarée comptée seule, c'est le rang dans l'ordre déclaré ; sinon
   le quantile de la valeur du seau (les valeurs égales partagent un niveau, une
   valeur absente n'en a pas). Le SPA ne fait que `level → couleur`
   (`LEVEL_FILLS`, avec une encre propre à chaque fond) et imprime la valeur dans
   la case, pour ne pas dépendre de la seule teinte.
5. **Pas de bibliothèque de graphiques.** La carte de chaleur est du SVG écrit à
   la main sur un module de géométrie pur (`lib/heatmapLayout.ts`), comme le
   voisinage (`adr/0010`). Un diagramme à barres n'est pas fourni : un tableau
   trié suffit.
6. **Garde-fous** : un délai (`statement_timeout`, 5 s, local à la transaction) borne une
   requête coûteuse ; son dépassement (SQLSTATE 57014) devient une erreur de domaine, 503
   `analysis_timeout`. Plus de 5 000 groupes distincts sont refusés (422 `scope_too_large`),
   jamais coupés : des sommes sur une partie se liraient comme le tout. Un nombre est lisible
   jusqu'à 30 chiffres de chaque côté de la virgule et 64 caractères bruts, le même motif en SQL
   et en Python : un `9` répété 400 fois ne devient ni `inf` ni un dépassement de `numeric`.
   Un périmètre (paquet, requête) au-delà de 5 000 éléments est refusé de la même façon,
   à un seul endroit (`AnalysisService._scope`), pour la matrice, les contrôles et les agrégats.

## Limites connues

- La carte de chaleur est un SVG : les cases sont des liens et portent un `<title>`, mais la
  navigation au clavier d'une grille de rectangles reste à faire.
- Une case dont la dimension est `property:<clé>` ne descend pas vers le catalogue (il ne filtre
  que sur la couche et le type).
- `lifecycle` et `risk` sont comparés par casse et accents repliés ; une graphie hors de la liste
  est comptée `__invalid__`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Typer les propriétés (colonnes ou schéma) | validation à l'écriture | migration de données, schéma à maintenir pour trois clés | écartée, retournable |
| Classer en SQL (`lower`, `CASE`) | un seul passage | dépend de la locale ; deux implémentations à garder égales | écartée |
| Couleur calculée dans le SPA | moins de champs | seuils = règle dans TypeScript | écartée |
| Teinte (`#rrggbb`) envoyée par le serveur | SPA minimal | le serveur connaîtrait le thème clair/sombre | écartée |
| Bibliothèque de graphiques | barres, axes, animations gratuits | dépendance pour une grille de rectangles | écartée |
| Index GIN sur `elements.properties` | vitesse | à justifier par un `EXPLAIN` | repoussée |

## Conséquences

- `cost` saisi « 1 200 € » est visible (compté, signalé) mais ne participe pas
  aux sommes ; la correction est dans la donnée.
- Ajouter une clé lisible (par exemple `criticality`) est une ligne de
  `ANALYTIC_PROPERTIES` et un test.
- La carte ne montre pas la tendance : rien n'est stocké, donc aucun historique.
- La requête groupée lit toute la table des éléments ; à l'échelle visée (quelques
  milliers) c'est suffisant. Un index GIN est à décider sur un `EXPLAIN`, pas avant.
- Une énumération ordonnée comptée avec une autre mesure que `count` est colorée
  par quantile, pas par rang : la couleur suit alors la valeur affichée.
- Les périmètres par paquet ou par requête enregistrée, et la dimension `package`,
  passent par le paramètre `scope` du port (même résolveur que la matrice et les contrôles).
- Le regroupement final (`summarise`) tourne dans un thread, sous le même plafond
  d'admission que les contrôles (`adr/0057`) : une agrégation ne bloque pas la boucle.

## Références

- ADR liés : [0010](0010-dessiner-le-voisinage-d-un-element.md), [0012](0012-ecran-du-metamodele.md), [0033](0033-postgresql-seul-pour-le-graphe.md), [0056](0056-matrice-de-relations.md), [0057](0057-controles-de-qualite-du-modele.md)
- Code concerné : `backend/src/ea/domain/analytics.py`, `frontend/src/lib/heatmapLayout.ts`
