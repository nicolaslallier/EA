# EA — Enterprise Architecture

Référentiel d'architecture d'entreprise : un backend FastAPI qui stocke un
modèle **ArchiMate 3.2** dans une base de données **graphe (Neo4j)**, et un
frontend Vue 3.

Le modèle d'architecture est un graphe et les questions qu'on lui pose sont des
parcours : « si ce serveur tombe, quels processus métier s'arrêtent ? ». C'est la
raison du choix de Neo4j — voir
[`docs/adr/0004`](docs/adr/0004-neo4j-pour-le-graphe-d-architecture.md).

## Prérequis

Sur macOS :

```bash
brew install make node uv
```

`make` est déjà fourni par macOS (GNU Make 3.81) — c'est suffisant. Docker
Desktop sert à ouvrir un `cypher-shell` sur le graphe ; le graphe lui-même
tourne sur le cluster Docker, pas ici.

## Démarrage

```bash
make install   # dépendances Python (uv) et Node (npm)
cp backend/.env.example backend/.env   # puis renseigne EA_NEO4J_PASSWORD
make db-ping   # vérifie que le graphe du cluster répond
make run       # backend + frontend en parallèle
```

Le graphe n'est pas démarré par ces commandes : c'est une instance unique,
déployée sur le cluster Docker (192.168.1.252) depuis
[`deploy/neo4j.stack.yml`](deploy/neo4j.stack.yml) — voir
[`docs/adr/0006`](docs/adr/0006-neo4j-sur-le-cluster-docker.md). `make db-stack`
rappelle la marche à suivre pour la (re)déployer, et le mot de passe se demande
à qui l'a déployée : il ne figure dans aucun fichier versionné.

Puis ouvre <http://localhost:5173>. La page affiche l'état du backend : si elle
indique « Backend: ok », les deux services communiquent.

| URL | Service |
|---|---|
| <http://localhost:5173> | Frontend Vite |
| <http://127.0.0.1:8000/health> | Endpoint de santé |
| <http://127.0.0.1:8000/docs> | Documentation OpenAPI |
| <http://192.168.1.252:7474> | Navigateur Neo4j, sur le cluster (`neo4j`) |
| <http://192.168.1.252:9000/#!/9/docker/stacks> | Portainer — la stack du graphe |

## Commandes

`make help` liste les cibles. Les principales :

| Cible | Effet |
|---|---|
| `make install` | Installe tout (backend + frontend) |
| `make run` | Lance les deux serveurs, logs entrelacés, `Ctrl-C` arrête tout |
| `make run-be` | Backend seul |
| `make run-fe` | Frontend seul |
| `make db-ping` | Vérifie que le graphe du cluster répond |
| `make db-stack` | Rappelle comment déployer la stack Neo4j sur le cluster |
| `make db-shell` | Ouvre un `cypher-shell` sur le graphe |
| `make db-reset` | Vide le graphe partagé — `CONFIRM=yes` obligatoire |
| `make check` | Lint, types et tests — ce que la CI vérifiera |
| `make clean` | Supprime `.venv`, `node_modules`, caches et artefacts de build |

En cas de conflit de port, les ports sont surchargeables :

```bash
make run-be BE_PORT=8001
```

## Tests

```bash
make check                # lint + types + tests sans base de données
make test-integration     # contre le vrai Neo4j — vide le graphe local
cd frontend && npm test -- --run && npm run typecheck
```

Les tests d'intégration effacent le contenu du graphe entre chaque cas : Neo4j
Community ne sert qu'une seule base, il n'y a donc ni schéma de test séparé ni
transaction à annuler. **Ce graphe est celui du cluster, partagé** : ne lance
pas `make test-integration` pendant que quelqu'un modélise. Ils ne s'exécutent que si `EA_ALLOW_DESTRUCTIVE_TESTS=1`
est positionné, ce que seule la cible `make test-integration` fait ; un
`uv run pytest` nu les saute.

## Configuration

Toute la configuration vient de l'environnement. Copie les exemples fournis, ne
committe jamais les fichiers réels :

```bash
cp backend/.env.example  backend/.env
cp frontend/.env.example frontend/.env.local
```

## L'API

| Route | Ce qu'elle fait |
|---|---|
| `POST /elements` | Ajoute un élément d'architecture |
| `GET /elements` | Parcourt le catalogue (filtres type, couche, recherche) |
| `POST /relationships` | Relie deux éléments, si ArchiMate l'autorise |
| `GET /elements/{id}/neighbourhood` | Le sous-graphe autour d'un élément |
| `GET /elements/{id}/impact` | Ce qui dépend d'un élément, transitivement |
| `GET /metamodel` | Les 61 types d'éléments et les 11 relations |
| `GET /metamodel/relationships` | Les liens légaux entre deux types donnés |

Un lien interdit par le métamodèle est refusé en 422 avec la règle enfreinte :

```
access: business_process -> application_service is not permitted
— the ArchiMate 3.2 metamodel does not allow this relationship
```

## Documentation

- [`docs/README.md`](docs/README.md) — index de documentation et vue d'ensemble du système.
- [`CLAUDE.md`](CLAUDE.md) — conventions de la pile, TDD, règles de sécurité.
- [`docs/adr/`](docs/adr/) — décisions d'architecture et leurs conséquences.
