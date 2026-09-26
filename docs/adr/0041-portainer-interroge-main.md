---
titre: Portainer interroge main, et pousser sur main c'est déployer
date: 2026-09-26
statut: Proposition
affects: scripts/portainer-stack.sh, CLAUDE.md
---

# 41. Portainer interroge main, et pousser sur main c'est déployer

Date : 2026-09-26
Statut : Proposition — rien n'est actif tant que `make app-up` n'a pas reposé
les réglages Git de la stack.

## Contexte

Le déploiement était un geste manuel : `make app-up` sur le Mac, après la fusion
dans `main`. La demande : déployer depuis GitHub Actions. Deux faits l'empêchent
tel quel.

- **GitHub ne joint pas ce Portainer.** Il n'écoute que sur `infra-net` et le
  réseau local ; `portainer.infra.famillelallier.net` n'a pas d'entrée dans le
  DNS public. Un runner hébergé par GitHub n'a aucune route vers lui.
- **Le repo est public.** Un runner auto-hébergé sur le Mac est le remède
  habituel, mais une PR venue d'un fork exécute *son* fichier de workflow, donc
  peut viser ce runner — posé à côté de la clé Portainer, qui est root sur le
  démon Docker. GitHub déconseille ce montage pour un repo public.

## Décision

**Portainer interroge `main` toutes les `POLL_INTERVAL` (5 min) et redéploie
sur tout nouveau commit** — les *GitOps updates* par polling, fonction de
l'édition Community. `scripts/portainer-stack.sh up` les pose : dans le corps de
la création pour une stack neuve, par `PUT /stacks/{id}/git` avant le
redéploiement pour une stack existante (le corps du redéploiement ne les porte
pas). Rien n'entre sur le Mac, aucun runner, aucun secret chez GitHub.

Les images sont reconstruites à chaque redéploiement parce que les deux services
de `deploy/ea.stack.yml` déclarent `pull_policy: build` ; rien d'autre n'est à
régler pour qu'un commit change ce qui tourne.

## Conséquences

- **Un commit rouge sur `main` se déploie.** Portainer ne lit pas la CI. C'était
  déjà la seule barrière manquante (*Nothing blocks a red merge yet*, `CLAUDE.md`) :
  la protection de branche qui exige la CI verte devient la vraie barrière du
  déploiement, et elle est à activer.
- `make app-up` reste nécessaire pour ce que Portainer ne peut pas deviner : une
  variable de `deploy/ea.env` ajoutée ou changée. Le polling redéploie avec les
  variables que Portainer détient déjà.
- Un déploiement arrive jusqu'à 5 min après la fusion, pas au moment de la fusion.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Runner auto-hébergé sur le Mac, `make app-up` après la CI verte | Déploie seulement le vert | Repo public : une PR de fork peut viser le runner, voisin de la clé root Docker | Écarté |
| Repo privé + runner auto-hébergé | Sûr, déploie seulement le vert | Change la visibilité du repo ; Portainer doit alors s'authentifier à GitHub | Écarté pour l'instant |
| Webhook Portainer appelé par une Action | Déclenché au moment de la fusion | Réservé à l'édition Business, et Portainer injoignable depuis GitHub de toute façon | Écarté |
