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
make install   # dépendances Python (uv), Node (npm), et backend/.env
# puis renseigne EA_NEO4J_PASSWORD dans backend/.env
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
| <http://127.0.0.1:8000/mcp> | Serveur MCP — le référentiel pour un agent |
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
| `make check` | Lint, types, client généré et tests sans base — ne modifie aucun fichier |
| `make lint` | Corrige le formatage et le lint du backend (`check` ne corrige rien) |
| `make audit` | `bandit`, `pip-audit`, `npm audit` |
| `make pg-backup` | Sauvegarde la base PostgreSQL du cluster dans `backups/` |
| `make db-backup-howto` | Rappelle la sauvegarde du graphe, hors ligne sur l'hôte |
| `make clean` | Supprime `.venv`, `node_modules`, caches et artefacts de build |

En cas de conflit de port, les ports sont surchargeables :

```bash
make run-be BE_PORT=8001
```

## Tests

```bash
make check                # lint + types + client généré + tests sans base, plancher de couverture 90 %
make audit                # bandit, pip-audit, npm audit (réseau requis)
make test-integration     # contre un Neo4j et un PostgreSQL jetables locaux, démarrés au besoin
make test-postgres        # seulement les tests PostgreSQL, contre le conteneur jetable
```

Les tests d'intégration détruisent ce qu'ils touchent : ils vident le graphe
entre chaque cas et annulent la chaîne de migrations. Ils tournent donc sur les
conteneurs jetables de [`docker-compose.yml`](docker-compose.yml), publiés sur
127.0.0.1, **jamais sur le cluster** — les fixtures refusent tout hôte qui n'est
pas local, et sautent le test en le disant. Docker est donc nécessaire pour
eux. Voir [`docs/adr/0024`](docs/adr/0024-tests-d-integration-sur-des-bases-jetables.md).

`make hooks` installe, si on le souhaite, les crochets `pre-commit`. La CI
(`.github/workflows/ci.yml`) rejoue les mêmes cibles — voir
[`docs/adr/0026`](docs/adr/0026-la-barriere-qualite.md).

Les sauvegardes des deux bases (`make pg-backup`, `make pg-restore`,
`make db-backup-howto`) sont décrites dans
[`docs/adr/0025`](docs/adr/0025-sauvegardes-des-deux-bases.md), encore à l'état
de proposition : aucune n'a été restaurée contre le cluster.

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

## Le serveur MCP

Le backend sert aussi le référentiel **à un agent**, en MCP, sur `/mcp` — voir
[`docs/adr/0014`](docs/adr/0014-serveur-mcp-pour-les-agents.md). Vingt-huit
outils : le CRUD des éléments, les liens, les deux parcours, le métamodèle, les
documents et l'adressage IP.
Ce ne sont pas des règles réécrites pour l'occasion : chaque outil appelle le
même service que l'API, donc un agent se voit refuser exactement ce qu'un
humain se verrait refuser.

`make run-be` suffit à le servir. Le [`.mcp.json`](.mcp.json) versionné y
branche Claude Code ; pour un autre client, l'adresse est
`http://127.0.0.1:8000/mcp` en *streamable HTTP*.

Un agent qui découvre le référentiel commence par `describe_metamodel` : les
61 types d'éléments et les 11 relations sont des listes fermées, et un nom qui
n'y figure pas est refusé.

> **`/mcp` n'est pas authentifié**, parce que rien ne l'est encore ici. Il ne
> répond donc, par défaut, qu'aux clients de **cette machine** : une requête
> venue d'ailleurs reçoit un 403, quels que soient ses en-têtes. Un agent
> distant passe par un tunnel SSH (`ssh -L 8000:127.0.0.1:8000 hôte`), ou
> `EA_MCP_ALLOW_REMOTE_CLIENTS=true` ouvre l'écriture à tout le réseau — voir
> [`docs/adr/0023`](docs/adr/0023-mcp-reserve-a-la-boucle-locale.md).
> `EA_MCP_ENABLED=false` le coupe entièrement.

## Documentation

- [`docs/README.md`](docs/README.md) — index de documentation et vue d'ensemble du système.
- [`CLAUDE.md`](CLAUDE.md) — conventions de la pile, TDD, règles de sécurité.
- [`docs/adr/`](docs/adr/) — décisions d'architecture et leurs conséquences.
