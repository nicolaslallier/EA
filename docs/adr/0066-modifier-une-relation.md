---
titre: Modifier une relation — `PATCH /relationships/{id}`, jamais le type ni les extrémités
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/model.py, backend/src/ea/domain/ports.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/services/architecture.py, backend/src/ea/api/architecture.py, backend/src/ea/mcp/server.py
---

# 66. Modifier une relation

Date : 2026-10-11
Statut : Proposition

## Contexte

Une relation ne pouvait être que créée ou supprimée : changer son nom, ses propriétés
ou son type d'accès voulait dire la supprimer et la recréer, avec un nouvel
identifiant, deux événements d'audit sans lien et deux revues des extrémités. Le
sous-projet 9 en fait un prérequis (probabilité d'une branche de simulation, colonnes
d'une clé étrangère). L'utilisateur a validé l'ajout.

## Décision

`PATCH /relationships/{id}`, `ArchitectureService.update_relationship` et l'outil MCP
`update_relationship` (annoté « écrit, non destructif », comme `update_element`).

- **Modifiable** : `name`, `properties`, `access_type` (seulement sur un `access`),
  `directed`. **Jamais** le type ni la source ni la cible : un autre type ou une autre
  extrémité est une autre relation, que le métamodèle doit juger — `DELETE` puis `POST`.
  Le corps refuse tout autre champ (422).
- **`properties` est remplacé en bloc** quand il est donné, pas fusionné — comme pour
  un élément (`PATCH /elements`) : lire, modifier, réécrire. Un champ omis garde sa
  valeur. Les dates de validité sont revalidées (`validate_lifespan_properties`).
- **Verrous.** Le dépôt verrouille d'abord les deux éléments reliés (ordre croissant
  des identifiants, `FOR NO KEY UPDATE`), puis la relation (`FOR UPDATE`) : l'ordre de
  `delete_relationship`, le seul qui ne s'étreint pas avec une suppression. Le patch
  pur est appliqué à la ligne **relue sous verrou**, jamais à une copie lue plus tôt.
- **Revue.** Les deux extrémités passent par `guard_element_write(…, LINK)` :
  verrouillée, elle refuse (409 `element_locked`, rien n'est écrit) ; approuvée, elle
  repasse `in_review` dans la même transaction, avec son événement. Modifier une
  relation est modifier un lien (ADR 0048).
- **Audit.** Un événement `relationship.updated`, `before` et `after` lus sous verrou, dans
  la même transaction. Un patch qui ne change rien n'écrit rien : ni verrou d'extrémité,
  ni événement, ni retour en revue.
- **404** relation inconnue ; **409** extrémité verrouillée ou conflit de sérialisation
  (`write_conflict`, rejouable) ; **422** `access_type` sur une relation qui n'est pas un
  accès, date mal formée, champ inconnu.
- **Pas de numéro de révision.** Une relation n'a pas de colonne `revision`, et en
  fabriquer une demande une migration sur une table peuplée pour un besoin que le verrou
  de ligne couvre déjà : deux patchs concurrents s'enchaînent (le second relit ce que le
  premier a validé) et aucun n'en défait un autre quand ils portent sur des champs
  différents. Deux patchs de `properties` restent « le dernier gagne » ; l'appelant qui
  lit puis réécrit le sait. Si le besoin naît, c'est une colonne et un ADR.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Supprimer puis recréer | rien à écrire | autre identifiant, deux événements, deux revues, fenêtre sans lien | Écartée |
| Fusionner `properties` | pratique | deux sémantiques de `PATCH` dans l'API ; on ne peut plus retirer une clé | Écartée |
| `expected_revision` | refuse le « dernier gagne » | colonne + migration sur `relationships` | Reportée |
| Autoriser le changement de type | un clic | contourne `permits()` et la matrice | Écartée |

## Conséquences

Le client OpenAPI et le test de gardes (`WRITES`, audit, `replayable`) comptent une
écriture de plus. Une action d'audit nouvelle (`relationship.updated`) entre dans l'énumération
servie ; le SPA affiche son libellé. Aucun écran SPA de modification n'est livré ici : le
point d'entrée est l'API et l'agent.

## Références

- ADR liés : [0046](0046-journal-d-audit-append-only.md) (toute écriture porte `audit=`), [0048](0048-approbation-et-verrouillage-des-elements.md), [0065](0065-la-jonction-est-un-element.md)
- Code concerné : `backend/src/ea/repositories/architecture_store.py` (`update_relationship`), `backend/src/ea/services/architecture.py`
