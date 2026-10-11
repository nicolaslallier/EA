---
titre: La matrice de relations, calculée à la demande
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/matrix.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/api/schemas.py, backend/src/ea/domain/ports.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/mcp/server.py, frontend/src/features/matrix, frontend/src/router/sections.ts, CLAUDE.md
---

# 56. La matrice de relations, calculée à la demande

Date : 2026-10-10
Statut : Proposition

## Contexte

Sparx EA offre une *Relationship Matrix* : des éléments en lignes, des éléments
en colonnes, et dans chaque case les liens qui les unissent — avec la
possibilité d'en créer un sans quitter la grille. Le catalogue sait déjà tout
ce qu'il faut : `relationships` porte les liens, `domain/archimate/rules.py`
dit ce qui est permis entre deux types (`adr/0005`, `adr/0012`). Ce qui manque
est une *lecture croisée*, et la question de savoir où elle écrit.

Trois tensions se sont présentées. D'abord, une grille 61 × 61 n'est ni
lisible ni transportable : il faut des axes bornés. Ensuite, la tentation d'un
chemin d'écriture propre à la matrice (« créer dans la case ») recréerait les
règles que `ArchitectureService.connect` applique déjà — existence des deux
extrémités, boucle de containment. Enfin, `permitted_relationships` répond pour
deux *types* et ignore que les deux extrémités peuvent être le même élément.

## Décision

1. **La matrice est une vue calculée.** Aucune table, aucune migration : un
   résultat stocké serait une seconde vérité que chaque modification périmerait.
   `POST /matrix` (un POST parce que deux axes ne tiennent pas dans une URL, pas
   parce qu'il écrit) et `GET /matrix/cell` lisent `elements` et `relationships`.
2. **Un lien vit dans une seule case** : ligne = source, colonne = cible. Son
   inverse est la case symétrique, si les deux éléments sont sur les axes. Pas de
   symétrisation : une case a un sens d'une phrase.
3. **Écriture exclusivement par `/relationships`.** Créer un lien depuis une case
   est `POST /relationships`, le supprimer `DELETE /relationships/{id}` ; le
   métamodèle et les gardes de `services/` restent les seuls juges.
4. **Les types permis ne sont jamais calculés dans le SPA.** `GET /matrix/cell`
   répond `allowed` ; `allowed_between(source, target)` filtre
   `permitted_relationships` avec `permits(..., same_element=...)`, ce qui exclut
   la composition de soi sur soi sans restater une règle.
5. **Les axes sont des sélections** : éléments nommés (`element_ids`), ou le
   catalogue restreint par type, couche, nom. Plafond `MAX_MATRIX_AXIS = 50`,
   déclaré dans le domaine et réimporté par `api/schemas.py` et `mcp/server.py`.
   Des `element_ids` au-delà du plafond sont refusés (422 `matrix_axis_too_large`) ;
   un axe par critères est coupé aux 50 premiers par nom, avec le total.
6. **La réponse est creuse** : seules les cases portant un lien. `association`
   étant permise entre tous les types, « les cases où quelque chose est permis »
   serait toute la grille ; `blocked` ne liste que les paires sans lien où rien
   n'est permis (vide aujourd'hui, traité si le métamodèle évolue).
7. **Un outil MCP en lecture seule**, `get_relationship_matrix`, qui prend les
   mêmes `AxisSelect` que l'API.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker la matrice (table `matrix_cells`) | lecture O(1) | seconde vérité, périmée à chaque lien | écartée |
| Écrire un lien par `PUT /matrix/cell` | un seul appel côté SPA | contourne `connect` : règles à restater | écartée |
| Matrice complète 61 × 61 côté serveur | simple | 3 721 cases que personne ne lit | écartée |
| Marquer comme interdites les cases sans type permis | fidèle à Sparx | aucune case n'est interdite en ArchiMate 3.2 (`association`) | remplacée par `blocked` |
| Axes par paquet / requête enregistrée | usage Sparx | dépend du sous-projet 1 | repoussée, ajoutée sans changer l'API |

## Conséquences

- La grille ne coûte rien à stocker et ne peut pas dériver ; elle coûte une
  requête `links_between` bornée par 50 × 50.
- Un axe coupé à 50 oblige à affiner ; la pagination d'axes est hors périmètre.
- Les liens sont ordonnés par `created_at` dans une case : deux appels donnent la
  même grille.
- Quand les paquets et requêtes enregistrées existeront, `AxisSelection` gagnera
  deux champs ; le contrat des axes actuel ne change pas.
- La suppression d'un lien depuis une case demande une confirmation côté SPA,
  pas côté API (`DELETE` existant).

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0009](0009-association-des-elements-dans-le-spa.md), [0012](0012-ecran-du-metamodele.md), [0014](0014-serveur-mcp-pour-les-agents.md), [0032](0032-authentification-par-keycloak.md), [0033](0033-postgresql-seul-pour-le-graphe.md)
- Code concerné : `backend/src/ea/domain/matrix.py`, `backend/src/ea/services/analysis.py`, `frontend/src/features/matrix/`
