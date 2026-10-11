---
titre: Approbation et verrouillage des éléments, rôle ea-approver
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/approval.py, backend/src/ea/domain/auth.py, backend/src/ea/services/architecture.py, backend/src/ea/api/architecture.py, backend/src/ea/mcp/server.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/repositories/conflicts.py, backend/migrations/versions/0013_element_approval.py, frontend/src/features/elements
---

# 48. Approbation et verrouillage des éléments, rôle ea-approver

Date : 2026-10-11
Statut : Proposition

## Contexte

Le catalogue sert de référence : certains éléments doivent être relus puis
figés. Le rôle `ea-editor` écrit (`docs/adr/0032`) et n'a aucun moyen de dire
« cet élément a été revu ». Les agents MCP, le pipeline `alimenter-catalogue`
et le SPA écrivent par les mêmes services : la règle est dans `services/`, pas
dans un écran. Le journal d'audit (`docs/adr/0046`) et les baselines
(`docs/adr/0047`) disent qui a changé quoi et permettent de revenir en arrière ;
ils ne disent pas qu'une version précise a été relue par une autre personne.

## Décision

**États.** Un état par élément : `draft` → `in_review` → `approved`, plus un
drapeau `locked` permis seulement sur un élément approuvé. `submit` (brouillon
vers revue) est à l'éditeur ; `approve`, `reject`, `reopen`, `lock` et `unlock`
sont à l'approbateur — le déverrouillage aussi, et lui seul. Les transitions
offertes sont une table pure (`domain/approval.py`) : le rôle manquant est un
`403`, l'état qui ne l'offre pas un `409 invalid_transition`.

**Rôle.** `ea-approver` (`APPROVER_ROLE`) **n'implique pas `ea-editor`** : les
rôles de royaume se cumulent, ils ne se contiennent pas. Un approbateur seul
approuve mais n'écrit pas ; pour les deux, il faut les deux rôles.
`GET /me` expose `can_approve`.

**Colonnes, pas `properties`.** L'état, le verrou, `approved_by_sub`,
`approved_at`, la `revision` et `last_modified_by_sub` sont des colonnes de
`elements` (migration `0013`) : ni l'unicité (type, nom) ni les index IPAM ne
sont touchés, et un `PATCH` qui remplace `properties` ne peut pas effacer
l'état. `CHECK` sur les trois valeurs et sur `locked ⇒ approved`. L'auteur du
dernier changement et la révision sont mis à jour **sous verrou de ligne par
toutes les voies d'écriture**, dans la transaction qui écrit. Les éléments
existants deviennent des brouillons non verrouillés ; `last_modified_by_sub`
est repris du journal, ou vaut `backfill` pour un élément qu'il ignore.

**L'approbation est liée à la révision relue.** `revision` compte les
changements du contenu. `approve` exige `expected_revision` (celle que
l'approbateur a lue quand la fiche s'est chargée) : une autre, ou aucune, donne
`409 approval_stale` et rien n'est approuvé. Le SPA relit la fiche, le dit, et
laisse l'approbateur relire avant de recommencer — jamais de nouvelle
approbation automatique. `ElementRead` expose `revision`, `approval_state`,
`locked`, `approved_by`, `approved_at` et `allowed_transitions` ; le SPA
dessine un bouton par transition listée et ne restate aucune règle.

**Séparation des tâches, fail-closed.** Qui a fait le dernier changement ne peut
pas approuver. La comparaison porte sur le `sub` Keycloak. Si l'auteur du
dernier changement est inconnu, c'est un refus, jamais une dispense. Seul
`EA_APPROVAL_SEPARATION=false`, explicite, la désactive (dépôt à un seul
utilisateur) ; l'application le journalise au démarrage
(`action=separation_disabled`). Le sujet réservé `ea-system` désigne le système
(script, migration) ; `backfill` l'auteur inconnu d'une ligne antérieure au
journal. (La spec nommait ce réglage `EA_APPROVAL_FOUR_EYES` ; le code et ce
texte disent `EA_APPROVAL_SEPARATION`.)

**Ce qui est une modification de contenu.** Les champs, les propriétés, les
liens (y compris ceux emportés en cascade par la suppression d'un voisin), les
documents attachés, le rangement en paquet et la restauration d'une baseline.
La mise en page d'un diagramme n'en est pas une : une boîte déplacée ne touche
pas l'élément. Sur un élément `approved` :

| Écriture | Qui | Conséquence |
|---|---|---|
| `update_element`, `delete_element` | approbateur seulement | l'élément repasse `in_review` (une suppression le supprime) |
| `connect`, `disconnect` (lien) | tout éditeur | l'élément repasse `in_review`, dans la même transaction |
| document attaché, remplacé ou retiré | personne | rouvrir l'élément d'abord |
| rangement en paquet | personne | rouvrir l'élément d'abord |
| restauration d'une baseline | personne, approbateur compris | refusée si elle touche un élément approuvé : il faut le rouvrir avant |

Un élément `locked` n'accepte rien, de personne, sauf `unlock`. `in_review`
et `draft` restent modifiables. Les **paquets** (l'arbre) et les **requêtes
enregistrées** sont hors approbation : ce ne sont pas des éléments.

**Une seule garde.** `guard_element_write` (`domain/approval.py`) est la règle
que chaque voie d'écriture interroge, avec l'élément lu sous son verrou de
ligne ; un test (`test_review_guard.py`) fait échouer toute écriture sur
`elements` qui ne la déclare pas.

**Traces.** Un refus n'écrit **aucun** événement d'audit (la transaction est
annulée) mais un log structuré : `action=lock_refused` pour une écriture, 
`action=transition_refused` pour une transition. Une transition réussie écrit
son événement (`element.approved`, `element.locked`…) avec l'auteur du jeton.
Un `40001` ou `40P01` (sérialisation, interblocage) sur n'importe quel écrivain
devient un `409 write_conflict`, rejouable. `approved_by` (le `sub` Keycloak)
est visible de tout lecteur : c'est une information d'audit, pas un secret.

**MCP.** L'outil `transition_element` (51e outil) applique la même table : un
agent qui est `ea-editor` soumet, il n'approuve pas.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| État dans `properties` | Aucune migration | Un `PATCH` l'efface ; interfère avec l'unicité et l'IPAM | Écartée |
| `ea-approver` implique `ea-editor` | Un rôle de moins à donner | Rôles de royaume non cumulables ; un approbateur n'a pas à écrire | Écartée |
| Approuver sans nommer la révision | API plus simple | On approuverait un contenu que l'approbateur n'a pas vu | Écartée au profit de `expected_revision` |
| Séparation des tâches qui se tait quand l'auteur est inconnu | Jamais de refus surprenant | Une donnée manquante lèverait la règle | Écartée : fail-closed |
| Approuver aussi relations, diagrammes, documents | Couverture totale | L'élément seul porte l'état ; les autres en sont des dépendances | Écartée |

## Conséquences

Limites connues, dites sans détour :

- **Un éditeur simple peut annuler unilatéralement une approbation** en ajoutant
  ou en retirant un lien sur l'élément : celui-ci repasse en revue. C'est tracé
  par l'audit, et cet éditeur ne peut pas ensuite le ré-approuver (il n'a pas le
  rôle) ; mais l'approbation est perdue. Choix assumé : un lien est un fait sur
  le graphe qu'on ne veut pas bloquer, et le laisser passer sans effet sur
  l'état serait approuver en silence.
- **Le pipeline `alimenter-catalogue` est éditeur** : il est donc refusé sur un
  élément approuvé. C'est voulu — une alimentation automatique ne défait pas une
  relecture.
- Une restauration refuse tout élément approuvé, même pour un approbateur : le
  plan est bloqué (`restore_blocked`) jusqu'à ce qu'il rouvre l'élément. C'est
  moins pratique qu'une remise en revue automatique, mais une restauration en
  masse ne doit pas défaire des approbations sans que personne ne les ait
  rouvertes.
- La séparation des tâches se juge sur le dernier modifieur, pas sur tous : un
  approbateur qui a modifié l'élément il y a trois révisions, puis dont un autre
  a changé une virgule, peut l'approuver.
- Il faut ajouter le rôle de royaume `ea-approver` à l'import du realm `ea`
  (dépôt Infra, **hors de ce repo**) et l'assigner : tant que ce n'est pas fait,
  personne ne peut approuver en production (`docs/adr/0032` est encore une
  Proposition : le realm n'est pas importé). Les réglages (`EA_APPROVAL_SEPARATION`)
  sont lus au démarrage : `make app-restart` les fait relire.
- Les tables `baseline_*` ne copient pas les colonnes d'approbation : une
  restauration recrée un élément en brouillon, et remet en revue celui qu'elle
  modifie.
- Le SPA n'a pas été vérifié à l'écran (Keycloak injoignable là où il a été
  écrit) : il est couvert par Vitest et par les tests d'API.
- Playwright n'existe pas : aucun test de bout en bout du parcours.

## Références

- ADR liés : [0032](0032-authentification-par-keycloak.md), [0046](0046-journal-d-audit-append-only.md), [0047](0047-baselines-en-copie-normalisee.md), [0042](0042-les-paquets-classement-en-liste-d-adjacence.md)
- Code : `backend/src/ea/domain/approval.py`, `backend/src/ea/services/architecture.py`, `backend/migrations/versions/0013_element_approval.py`, `frontend/src/features/elements/ApprovalBanner.vue`
