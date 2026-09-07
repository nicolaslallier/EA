# 1. Orchestration du développement local via un Makefile

Date : 2026-09-07
Statut : Accepté

## Contexte

Le projet démarre son scaffolding avec une architecture découplée : un backend
Python (FastAPI) et un frontend Vue. Sans point d'entrée unique, chaque
développeur mémorise deux jeux de commandes, dans deux répertoires, avec deux
gestionnaires de paquets — et l'onboarding technique consiste à lire un README
et à espérer qu'il soit à jour.

L'alternative naturelle serait une collection de scripts shell (`scripts/*.sh`),
mais elle prolifère : un script par action, par plateforme, sans découverte ni
graphe de dépendances.

## Décision

Un `Makefile` à la racine est le point d'entrée unique des commandes de
développement local. Il expose :

| Cible | Effet |
|---|---|
| `make install` | Dépendances backend (`uv sync`) et frontend (`npm install`) |
| `make run-be` | Backend FastAPI sur `http://127.0.0.1:8000` |
| `make run-fe` | Frontend Vite sur `http://localhost:5173` |
| `make run` | Les deux en parallèle (`$(MAKE) -j 2`), logs entrelacés |
| `make clean` | Supprime venv, `node_modules`, caches et artefacts |
| `make help` | Cible par défaut : liste les cibles documentées |

Points de conception :

- **Garde-fous.** `run-be` et `run-fe` déclarent une dépendance *order-only* sur
  un témoin de venv (`backend/.venv/.uv-sync-stamp`) et sur
  `frontend/node_modules`. Si la cible est absente, make exécute une règle qui
  échoue avec « Lance d'abord : make install » plutôt que de laisser le shell
  produire un `command not found` opaque. Le témoin — écrit par `install-be`,
  et déclaré dépendant de `pyproject.toml` et `uv.lock` — étend le garde-fou au
  venv *périmé* : une dépendance ajoutée rend le lockfile plus récent que le
  témoin, donc make réclame `make install` au lieu de laisser uvicorn échouer
  sur un `ModuleNotFoundError`.
- **`.env` local semé depuis l'exemple.** `backend/.env` est une cible de
  fichier sans prérequis, copiée de `.env.example` quand elle manque : `install`
  et `run-be` la fabriquent, et éditer l'exemple n'écrase jamais la
  configuration locale. Sans elle, `Settings` refuse de démarrer faute de mot de
  passe Neo4j — un refus correct, mais illisible dans une trace pydantic.
  L'exemple ne porte pas de mot de passe : le graphe est l'instance partagée du
  cluster (voir `0006`). Le fichier semé suffit donc à démarrer en `EA_DEBUG`,
  et la règle le dit en clair plutôt que de laisser croire le contraire.
- **Ports surchargeables.** `BE_PORT` et `FE_PORT` sont des variables `?=`, donc
  un conflit de port se contourne par `make run-be BE_PORT=8001` sans éditer le
  fichier. Vite tourne en `strictPort` : un port occupé échoue au lieu de glisser
  silencieusement vers 5174, ce qui sortirait de l'allowlist CORS du backend.
- **Pas de venv à la racine.** `uv sync` gère l'environnement dans
  `backend/.venv` ; le Makefile ne crée ni ne manipule de venv lui-même
  (voir [ADR 0003](0003-uv-comme-chaine-outils-python.md)).

## Conséquences

- `make`, `python3`, `node`/`npm` et `uv` doivent être présents sur la machine.
  `make install-be` vérifie explicitement `uv` et indique `brew install uv`.
- GNU make 3.81 (celui livré par macOS) suffit — aucune fonctionnalité de make 4
  n'est utilisée.
- `make run` s'appuie sur `-j 2` : `Ctrl-C` arrête bien les deux services, mais
  les logs sont entrelacés sans préfixe. Si cela devient gênant, un
  multiplexeur (`overmind`, `hivemind`, `docker compose`) remplacera cette cible.
- Le Makefile est délibérément limité à l'exécution locale. Les tests, le lint et
  les scans de sécurité restent invoqués via `uv run` / `npm run` tant que
  personne n'a exprimé le besoin de les centraliser.
- Hors périmètre à ce stade : conteneurisation Docker, déploiement Azure ACA,
  pipeline CI.
