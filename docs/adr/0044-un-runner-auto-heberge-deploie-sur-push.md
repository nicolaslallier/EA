---
titre: Un runner auto-hébergé déploie EA sur chaque push dans main
date: 2026-10-11
statut: Proposition
affects: .github/workflows/deploy.yml, docker-compose.runner.yml, Makefile
---

# 44. Un runner auto-hébergé déploie EA sur chaque push dans main

Date : 2026-10-11
Statut : Proposition — rien ne se déploie tant que le runner n'est pas démarré
(`make runner-up`) et les deux secrets posés.

## Contexte

`docs/adr/0041` a écarté le runner auto-hébergé parce que le repo est public.
Or les dépôts Infra, DarkAngel et Heaven, publics aussi, déploient ainsi : le
risque (PR de fork exécutant son workflow sur le runner) se neutralise en
n'ayant **aucun** déclencheur `pull_request` sur le workflow qui vise ce runner,
et le polling de 0041 laissait un déploiement en retard de 5 min, sans trace
dans GitHub.

## Décision

`.github/workflows/deploy.yml` tourne sur `[self-hosted, ea]` à chaque push dans
`main` (et à la main), et lance `scripts/portainer-stack.sh up` avec la clé
`PORTAINER_API_KEY` et le contenu de `deploy/ea.env` (`EA_STACK_ENV`), tous deux
secrets du dépôt. Le runner (`docker-compose.runner.yml`) est éphémère, hors de
la stack, et ne sert que ce dépôt. Le polling de 0041 reste en filet : Portainer
ne redéploie que sur un commit qu'il n'a pas déjà déployé.

## Conséquences

- Le déploiement se voit dans l'onglet Actions, avec son échec.
- Le runner tient le socket Docker : aucun `pull_request` sur ce workflow, et la
  protection de branche reste la barrière d'un commit rouge.
- Les secrets doublent `deploy/ea.env` : un changement de ce fichier se répète
  dans le secret `EA_STACK_ENV`.
