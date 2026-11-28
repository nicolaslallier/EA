---
titre: Les images sont construites par GitHub Actions et tirées de GHCR
date: 2026-11-28
statut: Proposition
affects: deploy/ea.stack.yml, .github/workflows/images.yml, .github/workflows/deploy.yml, scripts/portainer-stack.sh, deploy/ea.env.example, Makefile, CLAUDE.md
---

# 79. Les images sont construites par GitHub Actions et tirées de GHCR

Date : 2026-11-28
Statut : Proposition — aucune image n'a encore été poussée ; les paquets GHCR
n'existent qu'après le premier passage du workflow Images sur `main`.

## Contexte

`deploy/ea.stack.yml` construisait ses deux images sur la cible (`build:` et
`pull_policy: build`, [0041](0041-portainer-interroge-main.md)). Depuis
[0078](0078-ea-tourne-sur-le-mac-mini.md), la cible est le Mac mini,
environnement Portainer 7, un environnement **Agent**
(`tcp://192.168.2.68:9001`). Le build de compose y échoue :

```
compose build operation failed: listing workers for Build: failed to list workers:
... http2: frame too large, note that the frame header looked like an HTTP/1.1 header
```

BuildKit ouvre une session gRPC (HTTP/2) vers dockerd ; le proxy de l'agent ne
relaie que du HTTP/1.1. Cela marchait sur l'environnement 3 parce qu'il parlait
au socket Unix local. Tous les Mac de la maison sont des environnements Agent :
construire sur la cible est une impasse, pas une panne.

Les dépôts DarkAngel et AI ont déjà le remède : construire dans GitHub Actions,
pousser sur GHCR, et la stack ne fait que tirer.

## Décision

- **`.github/workflows/images.yml`** (runners hébergés par GitHub, aucun
  déclencheur de PR) construit `ghcr.io/nicolaslallier/ea-backend` et
  `ea-frontend` à chaque push sur `main` (hors `docs/` et `*.md`), chaque
  architecture sur un runner natif (`ubuntu-latest` amd64, `ubuntu-24.04-arm`
  arm64 — le mini est arm64), puis les réunit en un index multi-architecture
  sous `sha-<commit>` (immuable) et `latest`. Les trois `VITE_*` du SPA sont
  passés là, en clair : ils sont publics et Vite les écrit dans le bundle.
- **La stack ne construit plus rien.** Chaque service tire
  `ghcr.io/nicolaslallier/ea-<composant>:${EA_IMAGE_TAG:-latest}@${EA_<COMPOSANT>_DIGEST:?}` —
  le motif de la stack d'AI. Le digest décide ; le tag n'est qu'une étiquette
  lisible dans Portainer. La règle « épinglé par tag *et* digest » tient donc,
  mais le digest de nos propres images vit dans les variables de la stack, pas
  dans un commit.
- **`scripts/portainer-stack.sh up` résout les digests.** Il lit sur GHCR, avec
  un jeton anonyme, le digest que `EA_IMAGE_TAG` désigne (`latest` par défaut)
  et l'envoie à Portainer avec les autres variables. Un `EA_*_DIGEST` donné
  (terminal, puis `deploy/ea.env`) est gardé tel quel : c'est le retour arrière.
  Sans digest lisible, il s'arrête avant d'appeler Portainer.
- **Le workflow Deploy suit Images** (`workflow_run`, run réussi venu d'un
  `push`) et déploie `sha-<commit>` — jamais `latest`, qui peut déjà désigner un
  commit plus récent. À la main (`workflow_dispatch`), il déploie `latest`.
- **Le polling GitOps de [0041](0041-portainer-interroge-main.md) est retiré.**
  Il redéploierait chaque nouveau commit de `main` avec les digests que
  Portainer détient déjà : l'ancien code sous le nouveau fichier de stack.
  `up` envoie désormais ses réglages Git sans `AutoUpdate`, ce qui arrête le
  polling qu'un `up` antérieur avait posé. Déployer, c'est `make app-up` ou le
  workflow Deploy.
- **Paquets publics.** Le dépôt est public et les images ne contiennent aucun
  secret ; le mini les tire sans identifiants, et le script lit les digests sans
  jeton. Un paquet GHCR d'utilisateur naît privé : sa visibilité se change une
  fois, à la main.

Modifie [0041](0041-portainer-interroge-main.md) (plus de `pull_policy: build`,
plus de polling) et [0078](0078-ea-tourne-sur-le-mac-mini.md) (la stack ne
construit plus sur le mini). [0044](0044-un-runner-auto-heberge-deploie-sur-push.md)
et [0045](0045-le-runner-appelle-portainer-par-son-url.md) restent : seul le
déclencheur de Deploy change.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder le build sur la cible, via un environnement non-Agent | Aucun changement de chaîne | Aucun Mac n'est autre chose qu'un Agent ; exposer le socket Docker du mini serait pire | Écartée |
| Tag `latest` sans digest, `pull_policy: always` (DarkAngel) | Le plus simple | Le polling peut redéployer avant que l'image soit poussée ; viole l'épinglage par digest | Écartée |
| Digest committé dans `deploy/ea.stack.yml` par le workflow | Le polling redéploie le bon code | Un bot qui pousse sur `main` (ou une PR de plus à chaque commit) ; la protection de branche le gênerait | Écartée |
| Construire dans Deploy, sur le runner `ea` | Un seul workflow | Deploy est désactivé et le dépôt n'a aucun runner : plus aucune image ; et un build de plus sur le runner qui tient les secrets | Écartée |
| Paquets privés | Images invisibles | Identifiants de registre dans Portainer et dans le script, pour un code déjà public | Écartée |

## Conséquences

- **À faire à la main, une fois** : après le premier passage d'Images, rendre
  publics les paquets `ea-backend` et `ea-frontend` (GitHub → Packages →
  Package settings → Change visibility). Tant qu'ils sont privés, `make app-up`
  s'arrête sur « aucun digest ».
- Un commit fusionné ne change plus rien en production tant que Deploy est
  désactivé : il faut `make app-up` (qui prend `latest`). C'est voulu — le
  polling ne pouvait plus déployer le bon code.
- Changer de realm ou de client pour le SPA est une modification de
  `images.yml` puis un nouveau build ; `EA_AUTH_ISSUER` ne règle plus que l'API.
- Un commit de `docs/` seul ne produit pas d'image `sha-<commit>` : Deploy ne
  se déclenche pas non plus, et `latest` reste sur le dernier build.
- Le build se voit dans l'onglet Actions ; un Dockerfile cassé échoue là, plus
  au déploiement.
- Le runner `ea` exécute désormais un workflow déclenché par `workflow_run`.
  Images n'a aucun déclencheur de PR et Deploy vérifie `event == 'push'` : une
  PR de fork ne peut pas l'atteindre par ce chemin.

## Références

- ADR liés : [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md),
  [0041](0041-portainer-interroge-main.md), [0044](0044-un-runner-auto-heberge-deploie-sur-push.md),
  [0045](0045-le-runner-appelle-portainer-par-son-url.md), [0078](0078-ea-tourne-sur-le-mac-mini.md)
- Dépôts frères : `DarkAngel/.github/workflows/deploy.yml`, `AI/deploy/portainer-stack.yml`
