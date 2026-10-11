---
titre: Exigences et traçabilité — un profil, un test relié par realization, une couverture calculée côté serveur
date: 2026-10-11
statut: Proposition   # Proposition | Accepté | Supersédé par : NNNN
affects: domain/requirements.py, services/requirements.py, repositories/architecture_store.py, api/requirements.py, mcp/server.py, frontend/src/features/requirements/
---

# 64. Exigences et traçabilité

## Contexte

ArchiMate porte déjà le noyau d'une gestion d'exigences : les types `requirement` et `constraint`, et la relation `realization` qui va de la conception vers l'exigence. Ce qui manquait était de répondre à « cette exigence est-elle réalisée, par quoi, et testée par quoi ? » sans parcourir le catalogue à la main. La spec du sous-projet 9 proposait un profil du sous-projet 6, un test en `deliverable`, une CTE de couverture et l'exclusion des tests des vagues d'impact.

La lecture du code a corrigé quatre points. Les clés de propriété ne peuvent pas contenir de point (`domain/model.py`), donc `req.status` devient `req_status`. L'analyse d'impact suit `realization` de la source vers la cible (ADR 0013) : l'impact d'une exigence est ce qu'elle réalise, non ce qui la réalise. `impacted_by` part d'un seul élément : une couverture de N exigences en ferait N requêtes. Enfin la présence d'une valeur est déjà, pour l'IPAM, le marqueur d'appartenance à une convention (ADR 0020).

## Décision

Aucune table, aucune migration, aucune dépendance. La couverture est une lecture, calculée à la demande, jamais stockée.

| Notion | Représentation |
|---|---|
| Exigence | élément `requirement` ou `constraint` |
| Statut, priorité, source, identifiant | clés `req_status`, `req_priority`, `req_source`, `req_id` de `properties` |
| Vocabulaires | `proposed, approved, implemented, verified, rejected` ; `must, should, could` ; une valeur absente ou illisible est comptée `unset` |
| Cas de test | élément `deliverable` ou `artifact` portant la clé `test_result` (`not_run, passed, failed, blocked`) et, facultativement, `test_last_run` |
| Lien de traçabilité | `realization`, de la conception ou du test vers l'exigence |

La couverture suit les `realization` entrantes à travers les chaînes d'exigences, avec un ensemble d'éléments déjà vus (une boucle se termine). Un élément non motivationnel qui n'est pas un test est de la conception ; un test est un `deliverable`/`artifact` avec `test_result` ; les autres éléments de motivation (partie prenante, pilote) ne comptent pour rien. Un écart `design` est une exigence sans conception, un écart `verification` une exigence réalisée sans test. **Vérifiée ne veut pas dire « a un test ».** Seul un test dont `test_result` vaut exactement `passed` vérifie ; un `failed` met l'exigence `failing`, et `not_run`, `blocked` ou une valeur inconnue la laissent `unverified` (la réponse porte `verified`, `failing`, `unverified`, dont la somme est le nombre d'exigences actives, et `verification` par exigence). L'écart `verification` couvre donc aussi une exigence en échec. Une **jonction** (ADR 0062) entre une conception et une exigence est traversée, jamais comptée comme conception : la lecture suit les `realization` à travers les jonctions (dix sauts au plus, chacune suivie une fois, donc une boucle se termine), et une jonction sans élément réel derrière ne rend pas l'exigence réalisée.

Une exigence `rejected` n'entre ni dans les taux ni dans les écarts, et un taux sur zéro exigence active vaut `null`.

Une seule lecture de dépôt, `RequirementRepository.traceability()`, ramène les exigences, les `realization` qui les visent et leurs sources ; le calcul est pur (`domain/requirements.py`). Elle est **bornée avant d'être faite** (5 000 exigences, 20 000 réalisations, lus en SQL avec `LIMIT` + 1) et refusée en 422 `scope_too_large` au-delà plutôt que coupée : un taux calculé sur une page serait faux. Le calcul est replié hors de la boucle d'événements, sous une place de la même admission que les analyses (`AnalysisBusyError`, 429). `RequirementService.coverage` exige un appelant (`require_caller`), ne fait aucune écriture, et sert `GET /requirements/coverage?status=&priority=`, l'outil MCP `get_coverage` et la section SPA `/analyse/couverture`. Le SPA ne recalcule aucun taux et dérive les options de filtre des clés de la réponse.

Le **profil** « Exigence » / « Cas de test » du sous-projet 6 (validation des valeurs, formulaire) et le **filtre de la matrice** du sous-projet 4 (Tasks 8 et 9 du plan) ne sont pas livrés ici : ces sous-projets ne sont pas dans `main`. La couverture, elle, ne lit aucune de leurs tables ; la validation des valeurs est faite par le domaine (`validate_requirement_properties`), à chaque écriture d'éléments (API, MCP, import) : `req_status`, `req_priority` et `test_result` doivent être dans leur vocabulaire, à la casse et aux blancs près, et sont stockés en forme canonique (422 `invalid_requirement`, qui nomme la clé et jamais la valeur). L'écriture et la couverture lisent par la même fonction (`canonical`) : aucune valeur n'est acceptée par l'une et lue autrement par l'autre ; `ok`, `success`, `PASS` sont refusés à l'écriture et, venant de données antérieures, comptés non vérifiés.

L'exclusion des tests des vagues d'impact, prévue par la spec, est inutile : avec la convention test → exigence, un test est une source de `realization`, donc l'impact d'une exigence ou de sa conception ne l'atteint jamais. Un test intégration épingle ce comportement. Réponse à « que casse-t-on si je change cette exigence » : `designed_by` et `tested_by` de la couverture, pas l'impact.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Table `requirements` et `test_runs` | historique, campagnes | seconde vérité à côté du graphe, migration, ADR de plus | Écartée : un résultat = une propriété à jour |
| Détecter un test par jointure sur `element_stereotypes` | profil explicite | le cœur de C3 dépendrait du schéma du sous-projet 6 | Écartée : marqueur par présence de `test_result` |
| CTE par exigence avec `impacted_by` | réutilise l'existant | N requêtes, mauvaise direction (écart 5) | Écartée |
| Exclure les tests de l'impact | prudence | sans objet avec la direction des `realization` | Écartée, comportement épinglé par test |
| Couverture stockée et rafraîchie | lecture rapide | périmée à chaque modification | Écartée |

## Conséquences

Un test sans `test_result` n'existe pas pour la couverture : le profil le déclare obligatoire, mais `required` n'est pas bloquant à l'écriture (spec 06), donc un `deliverable` créé sans cette clé est compté comme de la conception. Il n'y a ni historique de résultats, ni campagne, ni workflow d'approbation. Au-delà des bornes, il faudra une requête récursive ou un filtre par paquet (sous-projet 1). Un filtre inconnu est un 422 qui ne répète pas la valeur reçue. Les statuts sont des codes anglais ASCII, les libellés français vivent dans le SPA. L'outil MCP `get_coverage` (lecture seule) s'ajoute aux autres. À surveiller : la définition de « conception » (tout élément non motivationnel qui n'est pas un test) peut devenir trop large si le catalogue contient des éléments de couche implémentation qui ne sont pas de la conception.

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0013](0013-ecran-d-analyse-d-impact.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0032](0032-authentification-par-keycloak.md)
- Code concerné : `backend/src/ea/domain/requirements.py`, `backend/src/ea/services/requirements.py`, `backend/src/ea/api/requirements.py`, `frontend/src/features/requirements/`
- Spec : `docs/superpowers/specs/2026-10-10-09-gros-morceaux-design.md`
