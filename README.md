# EA — Enterprise Architecture

Référentiel d'architecture d'entreprise : un backend FastAPI qui stocke un
modèle **ArchiMate 3.2** dans **PostgreSQL**, et un
frontend Vue 3.

Le modèle d'architecture est un graphe et les questions qu'on lui pose sont des
parcours : « si ce serveur tombe, quels processus métier s'arrêtent ? ». Le
graphe est stocké en tables et parcouru par des requêtes récursives — voir
[`docs/adr/0033`](docs/adr/0033-postgresql-seul-pour-le-graphe.md).

## Prérequis

Sur macOS :

```bash
brew install make node uv
```

`make` est déjà fourni par macOS (GNU Make 3.81) — c'est suffisant.

## Démarrage

```bash
make install   # dépendances Python (uv), Node (npm), et backend/.env
# Renseigne EA_POSTGRES_PASSWORD dans backend/.env
make pg-ping   # vérifie que la base partagée répond
make run       # backend + frontend en parallèle
```

PostgreSQL n'est pas démarré ici, mais il n'est pas sur le cluster : la
base `ea` vit dans la stack `~/OpenCode/Infra` de ce Mac, sur 127.0.0.1:5432 —
voir [`docs/adr/0029`](docs/adr/0029-nouvelle-adresse-du-cluster-et-postgresql-sur-le-mac.md) et `make pg-stack`.

Puis ouvre <http://localhost:5173>. La page affiche l'état du backend : si elle
indique « Backend: ok », les deux services communiquent.

| URL | Service |
|---|---|
| <http://localhost:5173> | Frontend Vite |
| <http://127.0.0.1:8000/health> | Endpoint de santé |
| <http://127.0.0.1:8000/docs> | Documentation OpenAPI |
| <http://127.0.0.1:8000/mcp> | Serveur MCP — le référentiel pour un agent |
| <https://ea.infra.famillelallier.net> | L'application déployée, derrière le NGINX de l'Infra — `make app-stack` |

## Commandes

`make help` liste les cibles. Les principales :

| Cible | Effet |
|---|---|
| `make install` | Installe tout (backend + frontend) |
| `make run` | Lance les deux serveurs, logs entrelacés, `Ctrl-C` arrête tout |
| `make run-be` | Backend seul |
| `make run-fe` | Frontend seul |
| `make check` | Lint, types, client généré et tests sans base — ne modifie aucun fichier |
| `make lint` | Corrige le formatage et le lint du backend (`check` ne corrige rien) |
| `make audit` | `bandit`, `pip-audit`, `npm audit` |
| `make pg-backup` | Sauvegarde la base PostgreSQL partagée dans `backups/` |
| `make clean` | Supprime `.venv`, `node_modules`, caches et artefacts de build |

En cas de conflit de port, les ports sont surchargeables :

```bash
make run-be BE_PORT=8001
```

## Tests

```bash
make check                # lint + types + client généré + tests sans base, plancher de couverture 90 %
make audit                # bandit, pip-audit, npm audit (réseau requis)
make test-integration     # contre un PostgreSQL jetable local, démarré au besoin
make test-postgres        # seulement les tests PostgreSQL, contre le conteneur jetable
```

Les tests d'intégration détruisent ce qu'ils touchent : ils annulent la chaîne
de migrations entre chaque cas. Ils tournent donc sur le
conteneur jetable de [`docker-compose.yml`](docker-compose.yml), publié sur
127.0.0.1, **jamais sur la base partagée** — les fixtures refusent tout hôte
qui n'est pas local, et le port 5432 de la base partagée, et sautent le test en
le disant. Docker est donc nécessaire pour
eux. Voir [`docs/adr/0024`](docs/adr/0024-tests-d-integration-sur-des-bases-jetables.md).

`make hooks` installe, si on le souhaite, les crochets `pre-commit`. La CI
(`.github/workflows/ci.yml`) rejoue les mêmes cibles — voir
[`docs/adr/0026`](docs/adr/0026-la-barriere-qualite.md).

Les sauvegardes (`make pg-backup`, `make pg-restore`) couvrent tout le modèle
depuis [`docs/adr/0033`](docs/adr/0033-postgresql-seul-pour-le-graphe.md) ;
elles sont décrites dans
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
