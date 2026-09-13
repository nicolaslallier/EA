---
titre: Le graphe Neo4j vit dans la stack Infra
date: 2026-09-13
statut: Accepté
affects: Makefile, docker-compose.yml, deploy/, backend/src/ea/core/config.py, backend/tests/integration/throwaway.py
---

# 30. Le graphe Neo4j vit dans la stack Infra

Supersède [`0006`](0006-neo4j-sur-le-cluster-docker.md). Amende
[`0024`](0024-tests-d-integration-sur-des-bases-jetables.md) — le garde du
graphe — et [`0025`](0025-sauvegardes-des-deux-bases.md) — le nom du conteneur
et le lieu de la sauvegarde. Amende aussi
[`0029`](0029-nouvelle-adresse-du-cluster-et-postgresql-sur-le-mac.md) — l'adresse
du graphe, que `0029` laissait sur 192.168.2.10.

## Contexte

`0006` plaçait le graphe sur l'hôte Docker 192.168.1.252, déployé en stack
Portainer depuis `deploy/neo4j.stack.yml`. Ce n'était plus vrai : cet hôte ne
répond plus, et le graphe réellement servi était un Neo4j **2026.07.1**
Community sur **192.168.2.10** — une machine que seul `backend/.env` désignait,
qu'aucun fichier versionné ne décrivait, et sur laquelle ce dépôt n'a pas
d'accès. `make db-stack`, `make db-backup-howto` et la version épinglée (5.26)
décrivaient donc un serveur qui n'existait pas.

Le dépôt [Infra](https://github.com/nicolaslallier/Infra) est déjà la pile
commune des applications sœurs : une stack Portainer sur Docker Desktop, avec
PostgreSQL, MinIO, RabbitMQ et la supervision, sous une règle d'entrée unique —
aucun service applicatif ne publie de port, nginx fronte tout, et le TCP passe
par son bloc `stream{}` lié à `127.0.0.1`.

## Décision

**Le graphe est le service `neo4j` de la stack Infra.** Déclaré dans le
`docker-compose.yml` de ce dépôt-là : image `neo4j:2026.07.1-community`
épinglée par tag et digest (la version d'où viennent les données), volume nommé
`neo4j-data`, aucun port publié. Bolt passe par le passthrough nginx
`127.0.0.1:7687`, qui devient la valeur par défaut de `neo4j_uri`. Le navigateur
Neo4j (7474) n'est pas exposé : `make db-shell` suffit.

| Avant | Maintenant |
|---|---|
| `deploy/neo4j.stack.yml`, collé dans Portainer | supprimé ; `make up` dans le dépôt Infra |
| `bolt://192.168.1.252:7687` par défaut | `bolt://127.0.0.1:7687` |
| `cypher-shell -a bolt://<hôte>` dans un conteneur | `--network infra-net -a bolt://neo4j:7687` — dans un conteneur, 127.0.0.1 est le conteneur |
| conteneur `ea-neo4j` | `infra-neo4j-1` |
| Neo4j de test en 5.26 (local et CI) | 2026.07.1, la version servie |
| le garde du graphe accepte tout hôte loopback | loopback **et** un port autre que 7687 |

**Le garde de `0024` ne suffisait plus, et c'est la conséquence qui compte.**
Il acceptait toute adresse loopback, au motif qu'elle ne peut pas être une autre
machine. Le graphe partagé est désormais *sur* cette machine, en loopback : un
`EA_ALLOW_DESTRUCTIVE_TESTS=1` lancé avec l'URI de `backend/.env` aurait vidé le
référentiel. `refuse_a_shared_graph` refuse donc aussi le port 7687 — et une URI
sans port, qui le désigne. Le Neo4j jetable publie sur 7688, en local comme en
CI.

**Les données sont copiées par Bolt, pas par un dump.** Le graphe tient en 15
éléments et 10 relations. `neo4j-admin database dump` exige le serveur source
arrêté et un accès à son hôte, qu'on n'a pas. Éléments et relations portent leur
propre `id` (chaîne) — celui que référence `element_documents` côté PostgreSQL —
donc une copie des labels, types et propriétés garde toutes les références ; les
contraintes et index sont réappliqués par l'application à son démarrage. Preuve
de la copie : mêmes ensembles d'`id` de nœuds et de relations des deux côtés.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder l'hôte dédié, remettre `deploy/neo4j.stack.yml` à jour pour 192.168.2.10 | Rien ne bouge dans Infra | Une machine hors de toute description versionnée, sans accès ni supervision | Écarté |
| Publier 7687 sur le service `neo4j` lui-même | Un bloc nginx de moins | Viole la règle d'entrée unique d'Infra | Écarté |
| Lier le passthrough à `${LAN_IP}` | Un autre poste joint le graphe sans tunnel | Bolt sur tout le LAN, le mot de passe pour seule barrière — la raison même qui garde PostgreSQL sur 127.0.0.1 | Écarté — tunnel SSH |
| Dump puis load hors ligne | Copie binaire exacte | Arrêt du serveur source, accès à son hôte | Écarté |
| Garde : n'accepter que le port 7688 | Liste blanche, comme l'hôte | Casse `NEO4J_TEST_BOLT_PORT`, surchargeable | Écarté — le port refusé est celui qu'Infra fixe |

## Conséquences

- **Le graphe dépend du Mac qui porte la stack Infra.** Docker Desktop arrêté,
  pas de graphe. Un autre poste passe par un tunnel :
  `ssh -L 7687:127.0.0.1:7687 <le Mac>`.
- **Chaque `make up` d'Infra recrée tous ses conteneurs**, Neo4j compris :
  quelques dizaines de secondes sans graphe.
- **`make clean CONFIRM=1` côté Infra supprime `neo4j-data`** avec les autres
  volumes. C'est le référentiel.
- **`0025` reste une proposition** : ses commandes nomment `ea-neo4j` et
  `/srv/backups` sur 192.168.1.252 ; `make db-backup-howto` porte la version à
  jour, qui n'a pas plus été restaurée qu'avant.
- **PostgreSQL n'est pas déplacé par cet ADR** : `0029` l'a déjà mis dans la
  même stack Infra, sur `127.0.0.1:5432`.
- **L'instance de 192.168.2.10 reste le retour arrière** tant que le nouveau
  graphe n'a pas servi ; l'éteindre est une décision à part.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md), [0006](0006-neo4j-sur-le-cluster-docker.md), [0024](0024-tests-d-integration-sur-des-bases-jetables.md), [0025](0025-sauvegardes-des-deux-bases.md)
- Code concerné : `backend/tests/integration/throwaway.py`, `backend/src/ea/core/config.py`, `Makefile`
- Dépôt Infra : `docker-compose.yml` (service `neo4j`), `nginx/stream.d/neo4j.conf`
