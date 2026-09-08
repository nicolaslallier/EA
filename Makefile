# Orchestrateur des commandes de développement local (Mac Studio).
#
# Cible unique d'entrée pour le stack Python (backend) + Vue (frontend).
# Voir docs/adr/0001-makefile-orchestration-locale.md.

BACKEND  := backend
FRONTEND := frontend

# Environnement virtuel Python géré par uv (uv sync le crée dans backend/.venv).
# Le témoin vit *dans* le venv : `make clean` l'emporte avec lui, donc un venv
# supprimé ne peut pas laisser derrière lui un témoin qui mentirait au garde-fou.
VENV       := $(BACKEND)/.venv
VENV_STAMP := $(VENV)/.uv-sync-stamp

# Configuration locale, dérivée de l'exemple committé. Jamais versionnée.
BE_ENV := $(BACKEND)/.env

# Surchargeables en cas de conflit de port : `make run-be BE_PORT=8001`.
#
# BE_HOST est l'adresse d'*écoute*, pas une adresse à ouvrir : 0.0.0.0 rend
# l'API joignable depuis les autres postes du réseau. BE_URL est celle qu'on
# affiche, puisqu'on ne visite pas 0.0.0.0. Qui a le droit d'appeler /mcp n'en
# découle pas — c'est EA_MCP_ALLOWED_HOSTS, voir backend/.env.example.
#
# FE_HOST suit la même règle depuis docs/adr/0019 : le SPA s'ouvre depuis les
# autres postes, et qui le *backend* sert n'en découle pas non plus — c'est
# EA_CORS_ORIGINS. `make run-fe FE_HOST=127.0.0.1` rend le serveur local.
BE_HOST ?= 0.0.0.0
BE_PORT ?= 8000
BE_URL  ?= http://127.0.0.1:$(BE_PORT)
FE_HOST ?= 0.0.0.0
FE_PORT ?= 5173
FE_URL  ?= http://127.0.0.1:$(FE_PORT)

# L'adresse par laquelle un autre poste atteint ce Mac. Sert uniquement à
# l'afficher : c'est l'origine à ajouter à EA_CORS_ORIGINS.
LAN_IP ?= $(shell ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)

# Base de données graphe : une instance unique sur le cluster Docker, déployée
# depuis deploy/neo4j.stack.yml. Rien ne la démarre depuis ce Makefile — voir
# docs/adr/0006.
NEO4J_HOST      ?= 192.168.1.252
NEO4J_BOLT_PORT ?= 7687
NEO4J_HTTP_PORT ?= 7474
NEO4J_URI       ?= bolt://$(NEO4J_HOST):$(NEO4J_BOLT_PORT)
NEO4J_BROWSER   ?= http://$(NEO4J_HOST):$(NEO4J_HTTP_PORT)
NEO4J_IMAGE     ?= neo4j:5.26-community
PORTAINER_STACKS ?= http://$(NEO4J_HOST):9000/\#!/9/docker/stacks

# Le mot de passe n'a pas de valeur par défaut : l'instance est partagée. Il
# vient de l'environnement, ou à défaut de backend/.env (non versionné).
NEO4J_PASSWORD ?= $(shell sed -n 's/^EA_NEO4J_PASSWORD=//p' $(BACKEND)/.env 2>/dev/null | tail -1)

# Base relationnelle : tout ce qui n'est pas le graphe (auth, audit,
# planification). Comme le graphe, une seule instance, sur le cluster Docker.
# Voir docs/adr/0015.
POSTGRES_HOST ?= 192.168.1.252
POSTGRES_PORT ?= 5432
POSTGRES_USER ?= ea
POSTGRES_DB   ?= ea
# L'image porte pgvector : l'extension `vector` doit exister *dans l'image*,
# pas seulement être activée dans la base — la migration 0003 fait
# `CREATE EXTENSION vector`. Le conteneur jetable de docker-compose.yml part de
# la même image, et la base du cluster a la même exigence (docs/adr/0019).
POSTGRES_IMAGE ?= pgvector/pgvector:pg17

# psql attend indéfiniment par défaut. Un poste dont les conteneurs ne joignent
# pas le LAN faisait pendre `make pg-ping` jusqu'au ^C, et la cible concluait
# ensuite sur une base qu'elle n'avait jamais interrogée.
PGCONNECT_TIMEOUT ?= 10

# Le piège de ce poste-ci : `psql` tourne dans un conteneur, et un conteneur ne
# joint pas forcément le LAN que le Mac joint. Sur macOS, Docker Desktop a
# besoin de l'autorisation « Réseau local » pour sortir vers 192.168.x.x ; sans
# elle, Internet passe et le cluster non.
PG_UNREACHABLE_HINT := printf "Le Mac joint-il $(POSTGRES_HOST) alors qu'un conteneur ne le joint pas ?\n  docker run --rm alpine nc -w5 -z $(POSTGRES_HOST) $(POSTGRES_PORT)\nSi oui : Réglages Système → Confidentialité et sécurité → Réseau local → Docker.\n";

# Comme pour Neo4j : pas de valeur par défaut, l'instance est partagée. Vient
# de l'environnement, sinon de backend/.env (non versionné).
POSTGRES_PASSWORD ?= $(shell sed -n 's/^EA_POSTGRES_PASSWORD=//p' $(BACKEND)/.env 2>/dev/null | tail -1)

# Le conteneur jetable de docker-compose.yml, contre lequel tournent les tests
# d'intégration : `alembic downgrade base` ne doit jamais viser le partagé. Son
# mot de passe est celui que docker-compose.yml porte déjà en clair.
POSTGRES_TEST_HOST     ?= host.docker.internal
POSTGRES_TEST_PASSWORD ?= developmentonly

# Service d'embeddings : LM Studio sur le même cluster que les deux bases,
# servant un /v1/embeddings compatible OpenAI. Voir docs/adr/0019.
EMBEDDINGS_URL   ?= http://192.168.1.252:1234/v1
EMBEDDINGS_MODEL ?= text-embedding-mxbai-embed-large-v1

# Contrat front/back : le schéma est versionné, le client TypeScript en dérive.
OPENAPI_SCHEMA_NAME := openapi.json
OPENAPI_SCHEMA      := $(BACKEND)/$(OPENAPI_SCHEMA_NAME)
GENERATED_CLIENT    := $(FRONTEND)/src/api/schema.d.ts

GREEN := \033[0;32m
RED   := \033[0;31m
NC    := \033[0m

.DEFAULT_GOAL := help
.PHONY: help install install-be install-fe run run-be run-fe clean \
        db-stack db-ping db-shell db-reset require-neo4j-password \
        pg-up pg-down pg-ping pg-shell pg-migrate pg-revision pg-history \
        pg-vector-check pg-stack require-postgres-password \
        embed-ping embed-models docs-reindex openapi openapi-check \
        test test-unit test-integration test-postgres test-fe lint typecheck check

help: ## Liste les cibles disponibles
	@printf "$(GREEN)Cibles disponibles :$(NC)\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## --- Installation ---------------------------------------------------------

install: install-be install-fe ## Installe toutes les dépendances (BE + FE)

install-be: $(BE_ENV) ## Crée le venv et installe les dépendances Python
	@printf "$(GREEN)Installing backend dependencies (uv)...$(NC)\n"
	@command -v uv >/dev/null 2>&1 || { \
		printf "$(RED)uv est introuvable. Installe-le : brew install uv$(NC)\n"; exit 1; }
	cd $(BACKEND) && uv sync --all-extras
	@touch $(VENV_STAMP)

install-fe: ## Installe les dépendances Node du frontend
	@printf "$(GREEN)Installing frontend dependencies (npm)...$(NC)\n"
	cd $(FRONTEND) && npm install

## --- Exécution ------------------------------------------------------------

run-be: | $(VENV_STAMP) $(BE_ENV) ## Lance le backend FastAPI (écoute 0.0.0.0:8000)
	@printf "$(GREEN)Starting backend on $(BE_HOST):$(BE_PORT) — $(BE_URL)$(NC)\n"
	cd $(BACKEND) && uv run uvicorn ea.main:app --reload --host $(BE_HOST) --port $(BE_PORT)

run-fe: | $(FRONTEND)/node_modules ## Lance le frontend Vue/Vite (écoute 0.0.0.0:5173)
	@printf "$(GREEN)Starting frontend on $(FE_HOST):$(FE_PORT) — $(FE_URL)$(NC)\n"
	@test -z "$(LAN_IP)" || printf "  Depuis un autre poste : http://$(LAN_IP):$(FE_PORT)\n"
	@test -z "$(LAN_IP)" || printf "  Ajoute http://$(LAN_IP):$(FE_PORT) à EA_CORS_ORIGINS ($(BE_ENV)), sinon l'API refuse ses appels.\n"
	cd $(FRONTEND) && npm run dev -- --host $(FE_HOST) --port $(FE_PORT)

run: ## Lance backend et frontend en parallèle (logs entrelacés, Ctrl-C arrête tout)
	@printf "$(GREEN)Starting full stack...$(NC)\n"
	@$(MAKE) --no-print-directory -j 2 run-be run-fe

# Garde-fous : une dépendance order-only sur un chemin absent échoue avec un
# message explicite plutôt qu'avec un « command not found » du shell.
#
# Le témoin dépend du manifeste et du lockfile : ajouter une dépendance les rend
# plus récents que lui, et make réclame `make install` avant de lancer le
# serveur — au lieu de laisser uvicorn échouer sur un ModuleNotFoundError.
$(VENV_STAMP): $(BACKEND)/pyproject.toml $(BACKEND)/uv.lock
	@printf "$(RED)Environnement Python absent ou périmé ($(VENV)).$(NC)\n"
	@printf "$(RED)Lance d'abord : make install$(NC)\n"
	@exit 1

# Pas de prérequis : la règle ne tourne que si le fichier manque, donc éditer
# `.env.example` n'écrase jamais la configuration locale d'un développeur.
#
# L'exemple ne porte aucun mot de passe : le graphe est l'instance partagée du
# cluster (docs/adr/0006). Le fichier semé suffit à démarrer en EA_DEBUG, mais
# `db-ping`, `db-shell` et `test-integration` réclameront EA_NEO4J_PASSWORD.
$(BE_ENV):
	@printf "$(GREEN)Creating $(BE_ENV) from .env.example...$(NC)\n"
	@cp $(BACKEND)/.env.example $@
	@printf "$(RED)Renseigne EA_NEO4J_PASSWORD dans $@ — voir make db-stack.$(NC)\n"

$(FRONTEND)/node_modules:
	@printf "$(RED)Dépendances Node absentes ($(FRONTEND)/node_modules).$(NC)\n"
	@printf "$(RED)Lance d'abord : make install$(NC)\n"
	@exit 1

## --- Base de données graphe -----------------------------------------------
#
# Le graphe tourne sur le cluster, pas ici : ces cibles s'y connectent, aucune
# ne le démarre. Le client `cypher-shell` est pris dans l'image Neo4j plutôt
# qu'installé sur le poste.
#
# Le mot de passe est passé par une variable d'environnement plutôt que par
# `-p` : un argument de ligne de commande est visible dans `ps`.

require-neo4j-password:
	@test -n "$(NEO4J_PASSWORD)" || { \
		printf "$(RED)NEO4J_PASSWORD est vide.$(NC)\n"; \
		printf "Renseigne EA_NEO4J_PASSWORD dans $(BACKEND)/.env, ou lance :\n"; \
		printf "  make $(MAKECMDGOALS) NEO4J_PASSWORD=...\n"; exit 1; }

db-stack: ## Rappelle comment déployer le graphe sur le cluster Docker
	@printf "$(GREEN)Stack Neo4j : deploy/neo4j.stack.yml$(NC)\n"
	@printf "  1. Ouvre $(PORTAINER_STACKS)\n"
	@printf "  2. Add stack → Web editor → colle deploy/neo4j.stack.yml\n"
	@printf "  3. Environment variables → NEO4J_PASSWORD = <mot de passe>\n"
	@printf "  4. Deploy the stack, puis : make db-ping\n"

db-ping: | require-neo4j-password ## Vérifie que le graphe du cluster répond
	@printf "$(GREEN)Interrogation de $(NEO4J_URI) ...$(NC)\n"
	@NEO4J_USERNAME=neo4j NEO4J_PASSWORD='$(NEO4J_PASSWORD)' docker run --rm \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI) --format plain \
		'MATCH (n:Element) RETURN count(n) AS elements' \
	|| { printf "$(RED)Aucune réponse. Vérifie la stack : make db-stack$(NC)\n"; exit 1; }
	@printf "Navigateur : $(NEO4J_BROWSER)\n"

db-shell: | require-neo4j-password ## Ouvre un cypher-shell sur le graphe du cluster
	@NEO4J_USERNAME=neo4j NEO4J_PASSWORD='$(NEO4J_PASSWORD)' docker run --rm -it \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI)

db-reset: | require-neo4j-password ## Vide le graphe du cluster (CONFIRM=yes obligatoire)
	@test "$(CONFIRM)" = "yes" || { \
		printf "$(RED)Cette commande efface le graphe PARTAGÉ : $(NEO4J_URI)$(NC)\n"; \
		printf "$(RED)Tout le monde le perd, il n'y a qu'une instance.$(NC)\n"; \
		printf "Relance avec : make db-reset CONFIRM=yes\n"; exit 1; }
	@NEO4J_USERNAME=neo4j NEO4J_PASSWORD='$(NEO4J_PASSWORD)' docker run --rm \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI) 'MATCH (n) DETACH DELETE n'
	@printf "$(GREEN)Graphe vidé.$(NC)\n"

## --- PostgreSQL -----------------------------------------------------------
#
# Comme le graphe, une instance unique sur le cluster ($(POSTGRES_HOST)) :
# `pg-ping`, `pg-shell` et `pg-migrate` s'y connectent, aucune ne la démarre.
# Le client psql est pris dans l'image Postgres plutôt qu'installé sur le poste.
#
# `pg-up` fait exception : il lance le conteneur *jetable* de
# docker-compose.yml, celui contre lequel tournent les tests d'intégration.
# Ceux-ci appliquent puis annulent les migrations, ce qu'on ne fait pas sur une
# base que d'autres utilisent.
#
# La première table existe : `element_documents`, les fichiers markdown
# attachés aux éléments (docs/adr/0017). Le graphe n'a pas de migrations (ses
# contraintes sont réappliquées au démarrage) ; PostgreSQL, si — `pg-migrate`
# est l'étape que le graphe n'a pas.

require-postgres-password:
	@test -n "$(POSTGRES_PASSWORD)" || { \
		printf "$(RED)POSTGRES_PASSWORD est vide.$(NC)\n"; \
		printf "Renseigne EA_POSTGRES_PASSWORD dans $(BACKEND)/.env, ou lance :\n"; \
		printf "  make $(MAKECMDGOALS) POSTGRES_PASSWORD=...\n"; exit 1; }

pg-ping: | require-postgres-password ## Vérifie que la base du cluster répond
	@printf "$(GREEN)Interrogation de $(POSTGRES_USER)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB) ...$(NC)\n"
	@PGPASSWORD='$(POSTGRES_PASSWORD)' docker run --rm -e PGPASSWORD \
		-e PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(POSTGRES_IMAGE) \
		psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB) \
		-c 'SELECT version()' \
	|| { printf "$(RED)Aucune réponse. Vérifie l'hôte et le mot de passe.$(NC)\n"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }

pg-shell: | require-postgres-password ## Ouvre un psql sur la base du cluster
	@PGPASSWORD='$(POSTGRES_PASSWORD)' docker run --rm -it -e PGPASSWORD $(POSTGRES_IMAGE) \
		psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB)

pg-migrate: | $(VENV_STAMP) ## Applique les migrations Alembic jusqu'à head (base du cluster)
	@printf "$(RED)Cible : $(POSTGRES_HOST)/$(POSTGRES_DB), la base PARTAGÉE.$(NC)\n"
	cd $(BACKEND) && uv run alembic upgrade head

pg-history: | $(VENV_STAMP) ## Affiche la chaîne des migrations et la révision courante
	cd $(BACKEND) && uv run alembic history --indicate-current

# `m` est obligatoire : une révision sans message donne un fichier qu'on ne
# sait plus relire six mois plus tard.
pg-revision: | $(VENV_STAMP) ## Génère une migration depuis les modèles (m="ajoute la table users")
	@test -n "$(m)" || { \
		printf "$(RED)Message manquant.$(NC)\n"; \
		printf 'Lance : make pg-revision m="ajoute la table users"\n'; exit 1; }
	cd $(BACKEND) && uv run alembic revision --autogenerate -m "$(m)"
	@printf "$(GREEN)Relis le fichier généré avant de le committer.$(NC)\n"

pg-vector-check: | require-postgres-password ## Vérifie que pgvector est disponible sur le cluster
	@printf "$(GREEN)pgvector sur $(POSTGRES_HOST)/$(POSTGRES_DB) ?$(NC)\n"
	@out=$$(PGPASSWORD='$(POSTGRES_PASSWORD)' docker run --rm -e PGPASSWORD \
		-e PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(POSTGRES_IMAGE) \
		psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB) \
		-tAc "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'" 2>&1) \
	|| { printf "$(RED)Connexion impossible : la question n'a pas été posée.$(NC)\n"; \
	     printf "%s\n" "$$out"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }; \
	test -n "$$out" \
	|| { printf "$(RED)Serveur joint, mais l'extension vector y est absente.$(NC)\n"; \
	     printf "L'image du stack doit être pgvector/pgvector:pgNN, pas postgres:NN —\n"; \
	     printf "voir deploy/postgres.stack.yml, make pg-stack, docs/adr/0019.\n"; exit 1; }; \
	printf "$(GREEN)pgvector disponible (%s).$(NC)\n" "$$out"

pg-stack: ## Rappelle comment déployer la base relationnelle sur le cluster Docker
	@printf "$(GREEN)Stack PostgreSQL : deploy/postgres.stack.yml$(NC)\n"
	@printf "  1. Ouvre $(PORTAINER_STACKS)\n"
	@printf "  2. Add stack → Web editor → colle deploy/postgres.stack.yml\n"
	@printf "  3. Environment variables → POSTGRES_PASSWORD = <mot de passe>\n"
	@printf "  4. Deploy the stack, puis : make pg-ping && make pg-vector-check\n"
	@printf "\n"
	@printf "$(RED)Remplacer une image postgres:NN par pgvector/pgvector:pgNN :$(NC)\n"
	@printf "  le volume est réutilisable (même version majeure), mais il a été\n"
	@printf "  initialisé sous musl et repart sous glibc : les collations diffèrent.\n"
	@printf "  Après le redéploiement, une fois : REINDEX DATABASE $(POSTGRES_DB);\n"
	@printf "  (make pg-shell). Base vide : supprimer le volume est plus simple.\n"
	@printf "  Puis make pg-migrate pour appliquer la chaîne jusqu'à head.\n"

pg-up: ## Démarre le PostgreSQL jetable local (pour les tests)
	docker compose up -d --wait postgres

pg-down: ## Arrête le PostgreSQL jetable local (les données restent dans le volume)
	docker compose down

## --- Embeddings -----------------------------------------------------------
#
# LM Studio tourne sur le cluster et sert un /v1/embeddings compatible OpenAI.
# Rien ici ne le démarre : comme les deux bases, c'est une instance partagée.
# La largeur des vecteurs (1024) est celle de la colonne, pas un réglage — voir
# docs/adr/0019.

embed-models: ## Liste les modèles que LM Studio expose
	@curl -sf --max-time 10 $(EMBEDDINGS_URL)/models \
		| python3 -c 'import json,sys; [print(m["id"]) for m in json.load(sys.stdin)["data"]]' \
	|| { printf "$(RED)Aucune réponse de $(EMBEDDINGS_URL).$(NC)\n"; exit 1; }

embed-ping: ## Vérifie que le modèle d'embedding répond, et à quelle largeur
	@printf "$(GREEN)$(EMBEDDINGS_MODEL) sur $(EMBEDDINGS_URL) ...$(NC)\n"
	@curl -sf --max-time 120 $(EMBEDDINGS_URL)/embeddings \
		-H 'Content-Type: application/json' \
		-d '{"model":"$(EMBEDDINGS_MODEL)","input":["ping"]}' \
		| python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]), "dimensions")' \
	|| { printf "$(RED)Pas de réponse. Le modèle est-il chargé dans LM Studio ?$(NC)\n"; exit 1; }

docs-reindex: | $(VENV_STAMP) ## Reconstruit l'index sémantique de tous les documents
	@printf "$(RED)Cible : $(POSTGRES_HOST)/$(POSTGRES_DB), la base PARTAGÉE.$(NC)\n"
	cd $(BACKEND) && uv run python -m ea.reindex

## --- Contrat front/back ---------------------------------------------------
#
# Le schéma OpenAPI est la source de vérité : `frontend/src/api/` en est
# dérivé, jamais écrit à la main. Les deux fichiers sont versionnés pour que
# `openapi-check` puisse constater qu'ils sont à jour — voir docs/adr/0007.

openapi: | $(VENV_STAMP) $(FRONTEND)/node_modules ## Régénère le schéma OpenAPI et le client TypeScript
	cd $(BACKEND) && uv run python -m ea.openapi > $(OPENAPI_SCHEMA_NAME)
	cd $(FRONTEND) && npm run generate:api
	@printf "$(GREEN)Schéma et client régénérés.$(NC)\n"

openapi-check: | $(VENV_STAMP) $(FRONTEND)/node_modules ## Échoue si le client versionné n'est plus celui du schéma
	@cd $(BACKEND) && uv run python -m ea.openapi | diff -u $(OPENAPI_SCHEMA_NAME) - \
		|| { printf "$(RED)$(OPENAPI_SCHEMA) est périmé. Lance : make openapi$(NC)\n"; exit 1; }
	@cd $(FRONTEND) && npm run --silent generate:api:check \
		|| { printf "$(RED)$(GENERATED_CLIENT) est périmé. Lance : make openapi$(NC)\n"; exit 1; }
	@printf "$(GREEN)Client généré à jour.$(NC)\n"

## --- Qualité --------------------------------------------------------------

test: ## Tests unitaires et API (sans base de données)
	cd $(BACKEND) && uv run pytest -q

test-unit: ## Boucle rapide : uniquement les tests unitaires
	cd $(BACKEND) && uv run pytest tests/unit -q

test-fe: | $(FRONTEND)/node_modules ## Tests Vitest du frontend (une passe, sans watch)
	cd $(FRONTEND) && npm test -- --run

test-integration: | $(VENV_STAMP) require-neo4j-password ## Tests contre le vrai Neo4j (vide le graphe partagé !)
	@printf "$(RED)Ces tests effacent les :Element du graphe PARTAGÉ : $(NEO4J_URI)$(NC)\n"
	@printf "$(RED)Personne ne doit être en train de modéliser dessus.$(NC)\n"
	cd $(BACKEND) && EA_ALLOW_DESTRUCTIVE_TESTS=1 EA_DEBUG=true \
		EA_NEO4J_PASSWORD='$(NEO4J_PASSWORD)' \
		EA_NEO4J_URI=$(NEO4J_URI) \
		uv run pytest tests/integration -q

# Explicitement contre le conteneur jetable, jamais contre le cluster : ces
# tests appliquent puis annulent la chaîne de migrations.
test-postgres: | $(VENV_STAMP) ## Tests contre le PostgreSQL jetable local (le démarre au besoin)
	docker compose up -d --wait postgres
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=5432 \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		uv run pytest tests/integration -m postgres -q

lint: ## ruff format + check
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .

# `migrations` en plus de `src` : env.py et les révisions sont du code exécuté
# en production, pas des fichiers générés qu'on ne relit jamais.
typecheck: ## mypy --strict, puis vue-tsc sur le frontend
	cd $(BACKEND) && uv run mypy src migrations
	cd $(FRONTEND) && npm run typecheck

check: lint typecheck openapi-check test test-fe ## Tout ce que la CI vérifiera

## --- Nettoyage ------------------------------------------------------------

clean: ## Supprime venv, node_modules, caches et artefacts de build
	rm -rf $(VENV)
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(BACKEND)/.coverage $(BACKEND)/htmlcov
	find $(BACKEND) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@printf "$(GREEN)Cleaned up!$(NC)\n"
