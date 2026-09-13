---
titre: Les tests d'intégration tournent sur des bases jetables, jamais sur le cluster
date: 2026-09-13
statut: Accepté
affects: docker-compose.yml, Makefile (test-integration, test-postgres, pg-up, db-test-up), backend/tests/integration/conftest.py, backend/tests/integration/throwaway.py, backend/tests/unit/test_deploy_stack.py
---

# 24. Les tests d'intégration tournent sur des bases jetables, jamais sur le cluster

Date : 2026-09-13
Statut : Accepté

Supersède **le volet « tests »** de
[`0006`](0006-neo4j-sur-le-cluster-docker.md) : le graphe applicatif reste une
instance unique sur le cluster, mais les tests ne la touchent plus. Le reste de
`0006` est inchangé.

## Contexte

Les tests d'intégration détruisent ce qu'ils touchent, et c'est voulu : Neo4j
Community ne sert qu'une base, donc isoler deux cas veut dire
`MATCH (n:Element) DETACH DELETE n` entre eux ; les tests relationnels, eux,
appliquent puis annulent toute la chaîne de migrations (`alembic downgrade
base`), ce qui supprime les tables.

Deux défauts rendaient cela dangereux.

**Le graphe.** `0006` a sorti Neo4j du poste en assumant une dette, qu'il
nommait lui-même : `make test-integration` vidait les `:Element` du graphe
*partagé*. Le seul garde était `EA_ALLOW_DESTRUCTIVE_TESTS=1`, qui dit « je le
veux » mais pas « où ». Un avertissement en rouge ne remplace pas une base
dédiée, et `0006` renvoyait la question à plus tard.

**PostgreSQL, en pire.** Le Makefile pointait bien `test-postgres` sur le
conteneur jetable, et la docstring de `postgres_engine` affirmait que la base
était locale. Elle ne l'était que sous `make` : la fixture construisait
`Settings(debug=True, postgres_enabled=True)`, qui lit `backend/.env`, dont
l'hôte est le cluster (192.168.1.252). Un `uv run pytest tests/integration`
lancé à la main visait donc la base partagée, et finissait par `alembic
downgrade base`. Seul le faux mot de passe `test-password`, injecté par
`tests/conftest.py` quand `EA_POSTGRES_PASSWORD` est absent de l'environnement,
empêchait la connexion — une protection par accident, qui tombe le jour où
quelqu'un exporte le vrai mot de passe dans son shell.

Dans les deux cas, l'adresse par défaut de `Settings` *est* le cluster : c'est
le bon choix pour l'application (`0006`, `0015`), et c'est précisément ce qui
rend un test destructeur mal configuré catastrophique plutôt qu'inoffensif.

## Décision

**Les deux bases de test sont des conteneurs jetables de `docker-compose.yml`,
publiés sur 127.0.0.1 uniquement, et une fixture refuse toute autre adresse.**

| | Graphe | PostgreSQL |
|---|---|---|
| Conteneur | `neo4j:5.26-community` — la même image que la stack du cluster | `pgvector/pgvector:pg17`, inchangé |
| Publication | `127.0.0.1:${NEO4J_TEST_BOLT_PORT:-7688}` (Bolt seul, pas de navigateur) | `127.0.0.1:${POSTGRES_TEST_PORT:-5432}` |
| Démarrage | `make db-test-up` | `make pg-up` |
| Garde | `EA_ALLOW_DESTRUCTIVE_TESTS=1` **et** un hôte loopback dans `EA_NEO4J_URI` | un hôte loopback dans `EA_POSTGRES_HOST` |
| Fixture gardée | `graph_driver` | `postgres_engine`, dont dépend `alembic_config` |

**La règle est une adresse, pas un réglage.** Loopback (`127.0.0.0/8`, `::1`,
`localhost`) ne peut pas être une autre machine ; c'est la seule chose
acceptée. `0.0.0.0` est refusé — c'est une adresse d'écoute, pas une
destination — et `host.docker.internal` aussi, puisque c'est le Mac vu d'un
conteneur. La décision est prise par des fonctions pures
(`tests/integration/throwaway.py`), testées sans base, et une fixture qui
refuse *saute* le test avec un message qui nomme l'hôte et la cible à lancer :
une suite lancée hors de `make` reste utile, elle ne détruit rien.

**Une seule porte vers les migrations.** `alembic_config` dépend de
`postgres_engine` : un test ne peut obtenir une configuration Alembic qu'après
le garde. `env.py` lit `Settings` comme tout le reste, donc un `Config(...)`
construit ailleurs migrerait ce que `backend/.env` désigne ; un test le
vérifie (aucun `Config(` hors de `conftest.py`).

**`make test-integration` démarre les deux conteneurs et pointe dessus.** Il ne
lit plus le mot de passe du cluster, et coupe `EA_EMBEDDINGS_ENABLED` : la
suite entière ne dépend plus d'aucune machine du cluster. `make test-postgres`
et `make pg-up` passent au conteneur les mêmes port et mot de passe qu'aux
tests — le port `5432` était écrit en dur d'un côté et surchargeable de
l'autre.

Le mot de passe du Neo4j de test est jetable et versionné, comme celui du
PostgreSQL de test. Le healthcheck le lit dans l'environnement de la commande,
jamais en argument (`ps`), et sous un nom qui ne commence pas par `NEO4J_`,
que l'image prendrait pour un réglage de `neo4j.conf`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Un second Neo4j sur le cluster, dédié aux tests (l'option que `0006` réservait) | Aucun Docker local à faire tourner | Toujours une machine partagée : deux développeurs qui lancent la suite en même temps se vident mutuellement la base ; et le garde ne peut plus être « loopback », il redevient une liste d'hôtes à tenir à jour | Écarté |
| Garder le cluster, durcir le seul drapeau (`EA_ALLOW_DESTRUCTIVE_TESTS`) | Rien à changer à l'outillage | Le drapeau dit « je le veux », pas « où » ; c'est exactement le défaut à corriger | Écarté |
| Refuser l'hôte du cluster nommément (`192.168.1.252`) | Ferme le trou d'aujourd'hui | Liste noire : le jour où le cluster change d'adresse, ou qu'une seconde base partagée apparaît, le garde ne voit rien | Écarté — la liste blanche loopback ne vieillit pas |
| testcontainers (conteneurs lancés par pytest) | Aucun `make` à lancer avant | Une dépendance de plus, un démon Docker requis par `pytest` lui-même, et un démarrage de Neo4j par session de test | Écarté pour l'instant |
| Neo4j avec `NEO4J_AUTH=none` en test | Aucun mot de passe à gérer | Le chemin d'authentification du pilote ne serait plus exercé, alors qu'il l'est en production | Écarté |

## Conséquences

- **Docker devient nécessaire pour les tests d'intégration du graphe**, comme il
  l'était déjà pour ceux de PostgreSQL. Hors Docker, la suite saute ces tests
  avec un message qui dit quoi lancer.
- **Le graphe partagé n'est plus jamais vidé par un test.** `make db-reset`
  (derrière `CONFIRM=yes`) reste la seule cible qui le fait.
- **`docker-compose.yml` déclare de nouveau un Neo4j**, ce que `0006` avait
  retiré. Le risque que `0006` voulait écarter — un graphe local contre lequel
  quelqu'un modélise — est tenu par `tests/unit/test_deploy_stack.py` : ports
  publiés sur 127.0.0.1 seulement, pas de navigateur, pas de volume nommé.
- **Une image de test qui diverge de celle du cluster serait un faux vert.** Le
  tag est le même aujourd'hui (`5.26-community`) ; une montée de version se
  fait dans les deux fichiers à la fois.
- **Les tests écrits contre le cluster par habitude sautent désormais.** C'est
  voulu : le message nomme l'hôte refusé et `make test-integration`.

## Références

- ADR liés : [0006](0006-neo4j-sur-le-cluster-docker.md), [0015](0015-socle-postgresql-sqlalchemy-alembic.md), [0017](0017-documents-markdown-attaches-aux-elements.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md)
- Code concerné : `backend/tests/integration/conftest.py`, `backend/tests/integration/throwaway.py`, `docker-compose.yml`, `Makefile`
