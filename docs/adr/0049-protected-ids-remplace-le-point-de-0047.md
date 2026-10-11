---
titre: Les éléments protégés d'une restauration — remplace le point de 0047 sur protected_ids
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/repositories/baseline_store.py, backend/src/ea/domain/baselines.py, backend/src/ea/domain/approval.py, backend/src/ea/services/baselines.py
---

# 49. Les éléments protégés d'une restauration — remplace le point de 0047 sur protected_ids

Date : 2026-10-11
Statut : Proposition

Remplace un seul point de `docs/adr/0047` (« la porte `protected_ids` est vide
jusqu'à la tâche d'approbation »), que l'ADR 0048 a rendu périmé. Le reste de
0047 tient.

## Contexte

L'ADR 0047 a été écrit avant l'approbation et disait que `protected_ids`, la liste
des éléments qu'une restauration ne doit pas toucher, était vide, donc que
`restore_blocked` ne se produisait pas en pratique. L'ADR 0048 l'a branchée. On
ne réécrit pas l'histoire d'un ADR : le texte de 0047 reste tel quel, son statut
passe à « partiellement remplacée », et c'est ici qu'est dit ce qui vaut
aujourd'hui.

## Décision

Depuis l'ADR 0048, `protected_ids` contient les éléments approuvés ou verrouillés
(`is_frozen`), lus dans la transaction de restauration ; un plan qui les touche
est bloqué (`restore_blocked`, 409) jusqu'à ce qu'un approbateur les rouvre. Les
tables `baseline_*` ne copient pas l'état d'approbation : un élément recréé
revient en brouillon.

Ce que le code fait, précisément :

- `approved_or_locked` (`repositories/baseline_store.py`) lit les identifiants
  des éléments dont l'état n'est ni brouillon ni en revue, ou qui sont
  verrouillés, avec le même test que `is_frozen` écrit en SQL ; un état inconnu
  protège au lieu d'exposer.
- Il est lu à la fois pour calculer le plan et dans la transaction
  `SERIALIZABLE` qui l'applique : un verrou posé après l'instantané de cette
  transaction fait échouer la mise à jour de la ligne (conflit rejouable), il ne
  laisse pas écraser.
- `plan_restore` n'écrit rien sur un élément protégé et le place dans
  `plan.blocked`. Un `dry_run` renvoie ce plan tel quel ; l'appliquer donne
  `409 restore_blocked`, sans événement d'audit (log structuré
  `action=restore_blocked`).
- Même un approbateur est refusé : il doit rouvrir l'élément d'abord.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Éditer 0047 | Un seul texte | Réécrit l'histoire d'une décision | Écartée |
| Remettre les approuvés en revue pendant la restauration | Plus pratique | Défait des approbations en masse sans que personne ne les ait rouvertes | Écartée (0048) |

## Conséquences

- Les `baseline_*` ne portent pas l'approbation : restaurer ne rend jamais un
  élément approuvé, et un élément supprimé puis recréé par une restauration
  repart en brouillon, à relire.
- Une restauration d'un modèle riche en éléments approuvés est souvent bloquée ;
  c'est voulu, et l'écran affiche `plan.blocked`.

## Références

- ADR liés : [0047](0047-baselines-en-copie-normalisee.md), [0048](0048-approbation-et-verrouillage-des-elements.md)
- Code : `backend/src/ea/repositories/baseline_store.py`, `backend/src/ea/domain/baselines.py`
