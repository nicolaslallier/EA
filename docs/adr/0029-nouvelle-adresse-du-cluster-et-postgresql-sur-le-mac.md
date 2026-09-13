---
titre: Le cluster change d'adresse, et PostgreSQL le quitte pour la stack Infra du Mac
date: 2026-09-13
statut: Accepté
affects: Makefile, backend/src/ea/core/config.py, backend/.env.example, frontend/.env.example, deploy/neo4j.stack.yml, deploy/postgres.stack.yml, docker-compose.yml, .github/workflows/ci.yml, backend/tests/integration/throwaway.py, README.md, CLAUDE.md
---

# 27. Le cluster change d'adresse, et PostgreSQL le quitte

Date : 2026-09-13
Statut : Accepté

Supersède **en partie** [`0006`](0006-neo4j-sur-le-cluster-docker.md) (l'adresse
du cluster), [`0015`](0015-socle-postgresql-sqlalchemy-alembic.md) (l'emplacement
de PostgreSQL) et [`0024`](0024-tests-d-integration-sur-des-bases-jetables.md)
(le port du PostgreSQL jetable, et ce que son garde refuse). Le reste de ces
trois ADR est inchangé.

## Contexte

Toutes les valeurs par défaut du dépôt nommaient `192.168.1.252`. Le
2026-09-13, aucun de ses ports ne répondait — ni Portainer (9000), ni Neo4j
(7474, 7687), ni PostgreSQL (5432), ni LM Studio (1234). Le `backend/.env` du
développeur, lui, pointait déjà ailleurs, et c'est là que tournait le projet :

| Service | Où il répond | Constaté le 2026-09-13 |
|---|---|---|
| Neo4j | `192.168.2.10:7474`, `:7687` | répond |
| LM Studio | `192.168.2.10:1234` | répond |
| PostgreSQL de l'application | `127.0.0.1:5432`, la stack `~/OpenCode/Infra` du Mac | répond |
| Ancien PostgreSQL | `192.168.2.10:5432` | répond, mais n'est plus la base : laissé intact au transfert, il diverge depuis |
| Portainer | `192.168.2.10:9000` | **ne répond pas** ; l'URL est gardée en supposant un arrêt passager |

Un développeur sans `.env` — ou un `.env` semé depuis `.env.example` — visait
donc une machine éteinte. `0006` justifiait l'adresse par défaut par
« atteindre le graphe partagé plutôt qu'un `localhost` qui ne répond pas » :
l'argument ne tient que si l'adresse est juste.

Le même jour, la base `ea` a été transférée dans la stack Infra du Mac
(`make provision-app app=ea` côté Infra : rôle `ea` à moindre privilège,
extension `vector` créée par le superutilisateur), derrière le NGINX d'Infra
en passthrough TCP sur `127.0.0.1:5432`.

**Ce transfert ouvre un trou dans le garde de `0024`.** Ce garde acceptait
tout PostgreSQL *loopback*, au motif que loopback « ne peut pas être une autre
machine », donc ne peut être que le conteneur jetable. Or la base partagée est
désormais sur loopback, et sur le port que le conteneur jetable publiait aussi
(5432). Un `uv run pytest tests/integration` lancé à la main lit
`backend/.env`, trouve `127.0.0.1:5432`, passe le garde, et finit par
`alembic downgrade base` sur la vraie base. Seul le faux mot de passe injecté
par `tests/conftest.py` l'en empêche — exactement la « protection par
accident » que `0024` avait fermée.

## Décision

**Les valeurs par défaut suivent ce qui tourne.**

| Réglage | Avant | Maintenant |
|---|---|---|
| `neo4j_uri`, `EA_NEO4J_URI`, `NEO4J_HOST` | `192.168.1.252` | `192.168.2.10` |
| `embeddings_base_url`, `EA_EMBEDDINGS_BASE_URL`, `EMBEDDINGS_URL` | `192.168.1.252:1234` | `192.168.2.10:1234` |
| `postgres_host`, `EA_POSTGRES_HOST`, `POSTGRES_HOST` | `192.168.1.252` | `127.0.0.1` |
| `NEO4J_ADVERTISED_HOST` (défaut de `deploy/neo4j.stack.yml`) | `192.168.1.252` | `192.168.2.10` |
| `PORTAINER_STACKS` | `192.168.1.252:9000` | `192.168.2.10:9000` (dérivé de `NEO4J_HOST`) |

**PostgreSQL n'est plus sur le cluster.** `deploy/postgres.stack.yml` n'est
plus déployé nulle part : son en-tête le dit, et `make pg-stack` explique la
provision côté Infra au lieu de la stack Portainer. Le supprimer est une
décision à part, qui touche aussi Dependabot et `make pg-vector-check`.

**Pour PostgreSQL, le garde refuse aussi le port partagé, même sur loopback.**
`refuse_a_shared_postgres` refuse un hôte qui n'est pas loopback, *et* le port
que `Settings` donne par défaut (`SHARED_POSTGRES_PORT`, lu dans
`Settings.model_fields` : il suit la base dans le même commit). Le conteneur
jetable publie sur `127.0.0.1:5433` — en local (`docker-compose.yml`,
`POSTGRES_TEST_PORT`) comme en CI —, de même que le Neo4j jetable publie sur
7688 à côté du 7687 partagé. Rien ne change pour le garde du graphe : le graphe
partagé n'est pas sur loopback.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder le garde loopback seul | Aucun changement | La base partagée passe le garde : c'est le trou décrit plus haut | Écarté |
| Refuser `5432` écrit en dur dans le garde | Une ligne | Liste noire qui vieillit — l'objection que `0024` faisait au refus de `192.168.1.252` par son nom | Écarté : le port est lu dans le défaut de `Settings`, il bouge avec la base |
| Publier le conteneur jetable sur `127.0.0.2` | Le port reste 5432 | macOS n'a pas cet alias par défaut ; `is_loopback` l'accepte déjà, donc le garde ne distinguerait toujours rien | Écarté |
| Distinguer par le nom de la base | Positif plutôt que négatif | Les deux s'appellent `ea`, et renommer celle de test touche compose, CI et fixtures pour un gain nul face au port | Écarté |
| Laisser `postgres_host` à une adresse du LAN | Un poste distant atteindrait la base sans `.env` | Aucune adresse du LAN ne sert cette base : le NGINX d'Infra écoute sur 127.0.0.1 | Écarté |

## Conséquences

- **`127.0.0.1` par défaut veut dire « ce poste ».** Un backend lancé ailleurs
  que sur le Mac qui porte la stack Infra ne trouve plus PostgreSQL sans son
  propre `.env` — et ne démarre pas, puisque `EA_POSTGRES_ENABLED` est vrai.
  C'est le comportement voulu par `0015` : une panne bruyante au démarrage.
- **L'adresse annoncée par Bolt n'est lue qu'au déploiement.** Le défaut de
  `NEO4J_ADVERTISED_HOST` ne change rien au conteneur qui tourne ; il vaut
  pour le prochain redéploiement de la stack.
- **Portainer ne répondait pas au moment de cette décision.** `make db-stack`
  affiche une URL non vérifiée ; à corriger si le port ou l'endpoint a changé.
- **L'ancienne base de `192.168.2.10` diverge.** Toute écriture qui y arrive
  après le transfert est perdue pour l'application. La décommissionner reste
  à faire.
- **Les versions divergent.** La base Infra est un pgvector sous PostgreSQL 18,
  alors que `docker-compose.yml` et la CI testent sur `pgvector/pgvector:pg17`
  — le faux vert que `0024` redoutait pour Neo4j. À aligner dans un changement
  à part.
- **Les sauvegardes de `0025` suivent sans modification** : `pg-backup` et
  `pg-restore` lisent `POSTGRES_HOST`. La procédure hors ligne du graphe, elle,
  s'exécute désormais sur `192.168.2.10`.
- **Un `POSTGRES_TEST_PORT=5432` passé à la main** fait sauter les tests
  PostgreSQL au lieu de les lancer, avec un message qui nomme le port refusé.

## Références

- ADR liés : [0006](0006-neo4j-sur-le-cluster-docker.md), [0015](0015-socle-postgresql-sqlalchemy-alembic.md), [0019](0019-recherche-semantique-sur-les-documents.md), [0024](0024-tests-d-integration-sur-des-bases-jetables.md), [0025](0025-sauvegardes-des-deux-bases.md)
- Code concerné : `backend/src/ea/core/config.py`, `backend/tests/integration/throwaway.py`, `Makefile`, `docker-compose.yml`, `.github/workflows/ci.yml`
