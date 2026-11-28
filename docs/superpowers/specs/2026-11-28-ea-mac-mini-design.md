# EA sur le Mac mini — conception courte

Date : 2026-11-28

## Intention

Déployer la stack « ea » sur le Mac mini (Portainer, environnement 7,
`192.168.2.68`). Elle n'existe plus sur aucun environnement :
`https://ea.infra.famillelallier.net` répond 502. La raison du choix du mini
n'a pas été précisée.

**But** : la même URL, le même certificat et le même realm Keycloak, avec
`/health` à `ok`.

**Hors but** : déplacer PostgreSQL, S3, Keycloak ou nginx ; chiffrer le trajet
LAN ; réparer le runner `runner-ea`.

## Approches

- **A (retenue)** : le mini publie deux ports, le nginx du laptop reste devant.
  Petit changement, déjà utilisé par Keycloak et Grafana sur le mini. HTTP et
  PostgreSQL circulent en clair sur le LAN.
- **B** : un nginx TLS sur le mini et le DNS vers lui. Protège le trajet des
  utilisateurs, pas PostgreSQL ni S3 ; une clé privée de plus à gérer.
- **Tunnel entre les deux Docker** : trop de pièces pour une application.

## Changements dans EA

- `deploy/ea.stack.yml` :
  - `api` publie `${EA_API_PORT:-8301}:8000`, `web` publie `${EA_WEB_PORT:-8300}:80` ;
  - défauts `EA_POSTGRES_HOST=192.168.2.10`, `EA_S3_ENDPOINT=192.168.2.10:8333` ;
  - `extra_hosts` de `api` : `keycloak.famillelallier.net:192.168.2.10`
    (le CA de l'Infra reste vérifié).
- `scripts/portainer-stack.sh` : vise l'environnement 7 par défaut
  (`PORTAINER_ENDPOINT_ID` l'emporte) ; une stack `ea` déclarée sur un autre
  environnement fait échouer la commande au lieu d'être redéployée de travers.
- `deploy/ea.env.example`, textes du `Makefile` : mêmes valeurs. `app-ps` et
  `app-logs` lisent le Docker local : ils ne verront plus la stack, et le disent.
- `test_deploy_stack.py` d'abord (rouge), puis la stack et le script.
- `docs/adr/0078` (remplace en partie 0027) et `CLAUDE.md`.

## Changement dans Infra (PR séparée)

`nginx/conf.d/ea.conf` : `ea-api:8000` devient `192.168.2.68:8301`,
`ea-web:80` devient `192.168.2.68:8300`. Chemins, limites et MCP inchangés.

## Sécurité, acceptée avec A

- Sans TLS entre les machines : requêtes et jetons des utilisateurs, trafic
  PostgreSQL et S3. Le mode d'authentification PostgreSQL est à vérifier.
- Les ports 8300 et 8301 sont joignables par tout le LAN, hors nginx : le jeton
  reste exigé, les limites de taille de nginx ne s'appliquent pas. Le routeur ne
  doit pas les rediriger.

## Ordre de mise en service

1. Vérifier, en lecture : base et rôle `ea` sur le PostgreSQL du laptop (et si
   la base contient des données), pgvector, bucket `ea-catalogue`.
   **Si la base manque ou est vide, s'arrêter et en parler** : ses données ont
   pu rester dans un volume du mini.
2. PR EA, CI verte, merge avec accord (Portainer clone `main`).
3. `make app-up INFRA_DIR=~/Code/Infra`, puis `/health` sur `.68:8301`.
   C'est là qu'on prouve que le mini joint le LAN (non vérifié à ce jour).
4. PR Infra, merge avec accord, puis vérification par l'URL publique.

Retour arrière : `make app-delete CONFIRM=yes` et annulation de la PR Infra.
La base est migrée au démarrage : une sauvegarde (`make pg-backup`) précède
l'étape 3.

## Laissé à l'implémentation

La forme exacte des tests et des messages. Le déploiement automatique reste en
panne tant que `runner-ea` redémarre en boucle ; le polling Git de Portainer,
posé par `app-up`, redéploie quand même sur `main`.
