---
titre: Les fichiers déployés vivent dans le SeaweedFS de l'Infra, et la stack se déploie depuis un autre hôte
date: 2026-10-11
statut: Proposition
affects: deploy/ea.stack.yml, deploy/ea.env.example, CLAUDE.md, backend/tests/unit/test_deploy_stack.py
---

# 56. Les fichiers déployés vivent dans le SeaweedFS de l'Infra

## Contexte

`docs/adr/0036` rangeait les fichiers dans le MinIO de l'Infra, joint sous le nom
`minio:9000` sur `infra-net`. L'Infra a depuis remplacé MinIO par SeaweedFS (le
service `s3`, API S3 sur le port 8333), et la stack Docker de l'Infra tourne sur
l'ordinateur Windows (192.168.2.10), non plus sur le Mac.

Le 502 du 11 octobre 2026 en est la somme de trois écarts, que rien dans le dépôt
ne signalait :

- le mot de passe PostgreSQL de `deploy/ea.env` (copie du `.env` de l'Infra *du
  Mac*) n'était pas celui du rôle `ea` créé depuis le `.env` de l'Infra *de
  Windows* : l'API s'arrêtait dans Alembic, avant uvicorn ;
- `INFRA_CA_CERT` était un chemin du Mac. Sur l'hôte Docker qui monte le
  fichier, il n'existait pas ; Docker y a créé un *dossier*, et l'API s'arrêtait à
  `IsADirectoryError` en construisant son client Keycloak ;
- `EA_S3_ENDPOINT` visait `minio:9000`, un nom qui n'existe plus : la section
  *Fichiers* était dégradée (`/health` : `"degraded":["files"]`).

## Décision

- La stack joint le SeaweedFS de l'Infra : `EA_S3_ENDPOINT` vaut `s3:8333` par
  défaut, en HTTP, comme `postgres`. Le client reste le SDK `minio` de l'ADR 0036,
  qui parle le S3 standard ; seul le point de terminaison change.
- L'identité est celle que crée `make s3-provision app=ea bucket=ea-catalogue` de
  l'Infra : clé d'accès `ea`, secret `EA_S3_SECRET_KEY` du `.env` de l'Infra,
  lecture-écriture sur le seul bucket `ea-catalogue`.
- `INFRA_CA_CERT` est un chemin **vu de l'hôte Docker**, pas du poste de
  l'opérateur. `deploy/ea.env.example` le dit, avec la forme Docker Desktop
  (`/run/desktop/mnt/host/c/Users/…`) et le symptôme d'un mauvais chemin.
- Le `.env` de l'Infra de l'hôte Docker fait foi. Une copie ailleurs a dérivé
  (18 réglages manquants le 11 octobre) : on n'y lit pas un secret.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder `minio` comme alias réseau sur le SeaweedFS | Rien à changer dans EA | Un nom qui ment ; cache le changement de serveur | Écartée |
| Une variable d'environnement seule, sans changer le défaut | Moins de fichiers touchés | Le défaut du dépôt vise un service mort ; le prochain `app-up` sans surcharge retombe en panne (cf. ADR 0038) | Écartée |
| Un client S3 différent (boto3) | Cible le S3 de plein droit | Une dépendance de plus pour un point de terminaison qui change ; le SDK `minio` fonctionne | Écartée |

## Conséquences

- Le test de la stack (`test_deploy_stack.py`) fixe le nouveau défaut.
- L'ADR 0036 reste vrai sur le fond (une seule porte, pas de lien présigné, une
  ligne `file_metadata` par objet) ; seul le serveur derrière le point de
  terminaison a changé.
- `make test-integration` démarre toujours un MinIO jetable (`make minio-up`) : le
  SDK est le même, c'est le serveur de test qui diffère du serveur déployé. Un
  écart à surveiller, pas à corriger ici.
- Pas encore vérifié de bout en bout : seul `/health` a été relu après le
  déploiement. Un dépôt, un téléchargement et une suppression dans la section
  *Fichiers* du SPA restent à faire.
