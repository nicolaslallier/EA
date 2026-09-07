---
titre: Le socle relationnel — SQLAlchemy 2 async et Alembic sur le PostgreSQL du cluster
date: 2026-09-07
statut: Accepté
affects: backend/src/ea/db/, backend/src/ea/core/config.py, backend/src/ea/api/dependencies.py, backend/src/ea/main.py, backend/alembic.ini, backend/migrations/, Makefile, docker-compose.yml
---

# 15. Le socle relationnel, sans première table

Date : 2026-09-07
Statut : Accepté

Complète [`0004`](0004-neo4j-pour-le-graphe-d-architecture.md), qui a partagé
les rôles entre les deux bases et a différé les dépendances relationnelles
« à la première table, pas avant ».

## Contexte

`0004` a tranché : le graphe d'architecture est dans Neo4j, et PostgreSQL garde
l'authentification, l'audit et les traitements planifiés. Il a aussi décidé de
ne rien installer tant qu'aucune table n'existerait — une prudence justifiée à
l'époque, puisque rien de non-graphe n'était en vue.

Cette prudence a un coût qui se paie d'un coup. La première table à écrire sera
celle des utilisateurs, dans le même changement que l'authentification : le jour
où quelqu'un s'y met, il doit choisir un pilote asynchrone, écrire le cycle de
vie d'un pool, décider comment Alembic obtient son URL sans la mettre dans un
fichier versionné, fixer une convention de nommage des contraintes — et, au
milieu de tout ça, concevoir un schéma d'authentification. Les deux moitiés se
gênent : une erreur d'échafaudage ressemble à une erreur de conception, et
inversement.

L'échafaudage est aussi ce qui se décide *mal* sous pression. La convention de
nommage des contraintes, en particulier, doit être posée avant la première
table : la changer ensuite renomme des contraintes déjà déployées.

## Décision

**Le socle relationnel est posé maintenant, et il ne crée aucune table.**

| Élément | Choix |
|---|---|
| Pilote | `asyncpg`, via `postgresql+asyncpg` |
| ORM | SQLAlchemy 2, `DeclarativeBase`, tout en asynchrone |
| Migrations | Alembic, `env.py` asynchrone, une révision racine vide |
| Emplacement | Une instance unique sur le cluster Docker, 192.168.1.252:5432 |
| Ouverture | `EA_POSTGRES_ENABLED`, à `false` jusqu'à la première table |

Quatre points portent l'essentiel.

**Le DSN est construit, jamais concaténé.** `dsn_of()` passe par
`URL.create()`. Un mot de passe contenant `@`, `/` ou `:` — ce qu'un mot de
passe généré contient presque toujours — recollé à la main dans une URL
n'échoue pas : il adresse *une autre base*. En prime, l'objet `URL` s'affiche
avec le mot de passe masqué, donc le moteur construit dessus ne peut pas
l'imprimer dans une trace.

**Alembic lit ses identifiants dans `Settings`, pas dans `alembic.ini`.** Le
fichier versionné ne porte aucune chaîne de connexion ; `migrations/env.py`
appelle `get_settings()`. Une migration tourne donc exactement avec les
identifiants de l'application. En mode hors ligne (`--sql`), l'URL est passée
avec le mot de passe masqué : un script généré ou un journal de CI ne peut pas
le transporter.

**Les contraintes sont nommées par convention, dès aujourd'hui.** Sans elle,
PostgreSQL invente un nom que l'`downgrade` généré ne sait pas viser : la
migration n'est pas réversible. La convention vit dans `db/base.py` et doit y
rester figée.

**La base ouverte est un drapeau, pas une constante.** `EA_POSTGRES_ENABLED`
vaut `false` : aucune table n'existe, et un processus qui remplirait un pool
vers une base que personne ne lit échouerait à démarrer partout où le cluster
est hors de portée. La première table le bascule à `true` dans le même commit.

### Où tourne la base

Sur le cluster, comme le graphe ([`0006`](0006-neo4j-sur-le-cluster-docker.md)),
et pour les mêmes raisons : une instance unique, que personne ne démarre depuis
son poste. `make pg-ping`, `pg-shell`, `pg-migrate` s'y connectent. Le mot de
passe est un vrai secret partagé — `.env.example` le laisse vide, exactement
comme celui de Neo4j.

`docker-compose.yml` garde malgré tout un conteneur PostgreSQL, et il ne sert
qu'à une chose : les tests d'intégration relationnels appliquent puis annulent
la chaîne de migrations (`alembic downgrade base`), ce qu'on ne fait pas sur une
base partagée. `make test-postgres` le démarre et pointe dessus explicitement.
C'est la différence avec le graphe, dont les tests d'intégration *vident* bien
l'instance partagée derrière `EA_ALLOW_DESTRUCTIVE_TESTS` — faute d'une seconde
instance à leur donner.

### La révision racine est vide, et c'est voulu

`0001_baseline` ne crée rien. Elle donne à la chaîne une racine, pour que la
première vraie migration ait un `down_revision` à nommer, et pour que
`alembic upgrade head` sur une base neuve soit déjà une commande qui fait
quelque chose : créer et estampiller `alembic_version`. C'est aussi ce qui rend
le socle *testable* aujourd'hui — `tests/integration/test_postgres.py` monte et
redescend la chaîne — au lieu d'être un code que rien n'exécute avant des mois.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Attendre la première table, comme `0004` le prévoyait | Aucune ligne inutile ; la décision reste ouverte | Concevoir l'auth et l'échafaudage dans le même changement ; la convention de nommage se fige alors sous pression | Écarté |
| Poser le socle *avec* une table `users` | Prouve tout de suite la chaîne complète | Invente un modèle d'authentification que personne n'a conçu, dans un commit qui ne parle pas d'auth | Écarté |
| `psycopg` 3 async plutôt qu'`asyncpg` | Un seul pilote pour sync et async ; messages d'erreur plus lisibles | `asyncpg` est le pilote que SQLAlchemy documente en premier pour l'async, et le plus rapide | Écarté, réversible : le pilote tient dans une constante |
| Garder la base en local (docker-compose) | Aucun secret partagé, isolation totale | Ce n'est pas là qu'elle tourne | Écarté par les faits |

## Conséquences

- **`db/models/__init__.py` est le seul point d'import des tables.** Alembic
  autogénère en comparant `Base.metadata` à la base réelle ; un modèle que ce
  paquet n'importe pas est un modèle qu'autogenerate propose de **supprimer**.
- **La convention de nommage est figée.** La changer après la première table
  déployée renomme des contraintes existantes, ce qu'aucune migration générée
  ne fera pour vous.
- **`test_no_table_is_mapped_yet` échouera** le jour de la première table. C'est
  le rappel d'en générer la migration, pas une ligne à supprimer.
- **Deux bases, deux disciplines.** Le graphe réapplique ses contraintes à
  chaque démarrage (`db/schema.py`) ; PostgreSQL exige une révision versionnée
  par changement. Le même dépôt porte les deux modèles, et `pg-migrate` est
  l'étape que le graphe n'a pas.
- **`pg-migrate` vise la base partagée.** Une migration appliquée là l'est pour
  tout le monde, sans retour arrière automatique. La cible l'annonce en rouge.
- **À surveiller :** le jour où l'API ouvre vraiment le pool
  (`EA_POSTGRES_ENABLED=true`), un cluster injoignable devient un démarrage
  impossible. C'est le comportement voulu — la même règle que pour le graphe —
  mais il n'a encore jamais été observé en conditions réelles.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0006](0006-neo4j-sur-le-cluster-docker.md)
- Code concerné : `backend/src/ea/db/postgres.py`, `backend/src/ea/db/base.py`,
  `backend/src/ea/db/models/`, `backend/migrations/env.py`,
  `backend/src/ea/core/config.py`
