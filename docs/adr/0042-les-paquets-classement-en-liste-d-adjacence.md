---
titre: Les paquets — un classement en liste d'adjacence, distinct du grouping ArchiMate
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/packages.py, backend/src/ea/domain/errors.py, backend/src/ea/domain/ports.py, backend/src/ea/domain/model.py, backend/src/ea/db/models/package.py, backend/migrations/versions/0009_packages.py, backend/src/ea/repositories/package_store.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/services/packages.py, backend/src/ea/services/diagrams.py, backend/src/ea/api/packages.py, backend/src/ea/api/schemas.py, backend/src/ea/api/errors.py, frontend/src/features/packages, frontend/src/features/elements, frontend/src/features/diagrams
---

# 42. Les paquets — un classement en liste d'adjacence, distinct du grouping ArchiMate

Date : 2026-10-10
Statut : Proposition

## Contexte

Le catalogue est une liste plate : à quelques centaines d'éléments et une
dizaine de diagrammes, personne ne retrouve plus rien sans taper un nom. Sparx
EA résout cela par son *Project Browser*, un arbre de paquets où l'on range ce
que l'on modélise. Nous voulons la même chose, avec une contrainte que Sparx
n'a pas : les mêmes éléments sont lus par un agent (MCP), par un pipeline qui
écrit à la racine, et par des traversées (voisinage, impact) qui ne doivent
jamais être perturbées par la façon dont une équipe range ses dossiers.

ArchiMate a déjà un concept voisin, le `grouping`. C'est un *fait* du graphe : il
porte des relations (composition, agrégation), apparaît dans le voisinage et
l'impact, et son existence change ce que le modèle dit. Un dossier, lui, ne dit
rien du système : le déplacer ne doit changer aucune réponse d'analyse.

## Décision

Un **paquet** est une ligne de `packages(id, parent_id, name, description,
created_at, updated_at)` ; `elements.package_id` et `diagrams.package_id` sont
des clés étrangères nullables (`NULL` = « à la racine »). Un élément ou un
diagramme est dans **au plus un** paquet.

| Point | Choix |
|---|---|
| Modèle | Liste d'adjacence ; l'arbre est servi à plat (`GET /packages`, au plus 2 000 paquets) et le SPA le monte. |
| Noms | Frères uniques sans tenir compte de la casse, par deux index partiels (`uq_packages_root_name`, `uq_packages_child_name`) — pas de `NULLS NOT DISTINCT`, qui dépendrait de la version de PostgreSQL de l'Infra. Pas de `/` dans un nom, pour rendre un chemin `A / B / C`. |
| Suppression | Les trois clés sont `ON DELETE RESTRICT`. Un paquet non vide est refusé (`409 package_not_empty`) ; `?move_contents_to=<id\|root>` déplace le contenu direct dans la même transaction. Jamais de `CASCADE` (il détruirait des faits du graphe sur un geste de rangement) ni de `SET NULL` (il disperserait tout à la racine sans que personne l'ait décidé). |
| Cycles et profondeur | `check_tree()`, fonction pure du domaine sur la liste à plat : pas de cycle, 12 niveaux au plus. Le service l'appelle pour rendre un refus lisible tôt ; **le dépôt la rappelle sous verrou** avant toute écriture de `parent_id` (voir « Concurrence »). |
| Sous-arbre | Le filtre d'éléments par paquet inclut les sous-paquets par une CTE récursive à paramètre lié (`_subtree`). |
| Rangement | `Element.package_id` n'est pas dans `element_row()` : un `save_element` n'écrase jamais un rangement fait entre-temps. Un déplacement ne touche pas `updated_at`. |
| Unicité des éléments | `UniqueConstraint(element_type, name)` reste globale : le pipeline `alimenter-catalogue` et l'IPAM s'y appuient. |
| Droits | Ceux du rôle global : lecture = tout appelant, écriture = `ea-editor`. Pas de droits par paquet. |

## Concurrence

Lire la liste, la valider, puis écrire dans une autre transaction laisse deux
déplacements concurrents (A sous B, B sous A) passer chacun la vérification et
fermer un cycle ; il en va de même pour la profondeur. Chaque écriture du dépôt
qui touche à la forme de l'arbre (`add`, `save`, `delete` avec re-parentage du
contenu) ouvre donc **une** transaction qui prend
`pg_advisory_xact_lock(TREE_LOCK)` (constante nommée, un seul arbre, un seul
verrou, libéré au commit ou au rollback), relit la liste à plat *sous* ce
verrou (READ COMMITTED : elle voit ce que le détenteur précédent a validé),
applique `check_tree()` à l'arbre résultant, puis écrit. Le dépôt n'écrit jamais
`parent_id` sans cette garde, y compris pour `move_contents_to`. Les tests
d'intégration lancent deux déplacements concurrents (`asyncio.gather`) et
prouvent qu'un seul passe et que l'autre reçoit `CyclicPackageError` (ou
`PackageTooDeepError` pour la profondeur). La suppression sans `move_contents_to` passe `require_empty=True` au dépôt
(`into=None` seul voudrait dire « à la racine ») : dans la même transaction, le
dépôt recompte enfants, éléments et diagrammes, ne déplace rien, et lève
`PackageNotEmptyError` si le paquet contient quoi que ce soit. Un rangement
validé entre ce comptage et le `DELETE` est arrêté par les clés `RESTRICT` : le
rangement ou la suppression gagne, jamais un re-racinement silencieux (test
d'intégration `asyncio.gather` suppression contre rangement).
L'unicité des noms de frères reste
garantie par les index partiels. Le plafond `MAX_PACKAGES` est revérifié par `add` sous le même verrou
(le service le vérifie aussi, pour un refus rapide).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Utiliser le `grouping` ArchiMate | Aucun nouveau concept | Un fait du graphe : apparaît dans voisinage et impact, exige des relations pour ranger | Écartée |
| Table de fermeture ou `ltree` | Sous-arbre en une jointure | Deuxième vérité à tenir cohérente ; inutile à quelques milliers d'éléments | Écartée, à mesurer avant de revenir dessus |
| Un élément dans plusieurs paquets | Souple | Le rôle des « vues multiples » est celui des diagrammes ; l'arbre perd son sens de dossier | Écartée |
| `ON DELETE CASCADE` / `SET NULL` | Suppression en un geste | Détruit ou disperse sans décision | Écartée |
| Détection de cycle par CTE sur les ancêtres | Une seule requête | Moins testable que la fonction pure ; même fenêtre de course sans verrou | Écartée au profit de `check_tree()` sous verrou consultatif |
| `NULLS NOT DISTINCT` | Un seul index | Version de PostgreSQL exigée | Écartée |

## Conséquences

- L'arbre est chargé en entier : au-delà de 2 000 paquets il faudra le servir
  paresseusement (le plafond est une erreur `422 too_many_packages`, pas un
  silence).
- Les écritures sur l'arbre sont sérialisées par un verrou consultatif : cela
  suffit pour un arbre de 2 000 paquets rangé à la main, pas pour une écriture en
  masse.
- `Element` gagne un champ ; tout code qui reconstruit un `Element` à la main
  doit passer par `dataclasses.replace`.
- Le pipeline crée toujours à la racine ; un paramètre `package` est possible
  plus tard par l'API.
- Les requêtes enregistrées (ADR suivant) nomment un paquet par son identifiant,
  sans clé étrangère : un paquet supprimé les laisse intactes et les fait
  répondre `422 unknown_package`.

## Références

- ADR liés : [0033](0033-postgresql-seul-pour-le-graphe.md), [0031](0031-diagrammes-enregistres.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md)
- Spec : `docs/superpowers/specs/2026-10-10-01-referentiel-structure-design.md`
- Code concerné : `backend/src/ea/domain/packages.py`, `backend/src/ea/repositories/package_store.py`, `backend/src/ea/services/packages.py`, `backend/src/ea/api/packages.py`
