# Orchestrateur des commandes de développement local (Mac Studio).
#
# Cible unique d'entrée pour le stack Python (backend) + Vue (frontend).
# Voir docs/adr/0001-makefile-orchestration-locale.md.

BACKEND  := backend
FRONTEND := frontend

# Environnement virtuel Python géré par uv (uv sync le crée dans backend/.venv).
VENV       := $(BACKEND)/.venv
VENV_PYTHON := $(VENV)/bin/python

# Surchargeables en cas de conflit de port : `make run-be BE_PORT=8001`.
BE_HOST ?= 127.0.0.1
BE_PORT ?= 8000
FE_PORT ?= 5173

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
        pg-up pg-down openapi openapi-check \
        test test-unit test-integration test-fe lint typecheck check

help: ## Liste les cibles disponibles
	@printf "$(GREEN)Cibles disponibles :$(NC)\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## --- Installation ---------------------------------------------------------

install: install-be install-fe ## Installe toutes les dépendances (BE + FE)

install-be: ## Crée le venv et installe les dépendances Python
	@printf "$(GREEN)Installing backend dependencies (uv)...$(NC)\n"
	@command -v uv >/dev/null 2>&1 || { \
		printf "$(RED)uv est introuvable. Installe-le : brew install uv$(NC)\n"; exit 1; }
	cd $(BACKEND) && uv sync --all-extras

install-fe: ## Installe les dépendances Node du frontend
	@printf "$(GREEN)Installing frontend dependencies (npm)...$(NC)\n"
	cd $(FRONTEND) && npm install

## --- Exécution ------------------------------------------------------------

run-be: | $(VENV_PYTHON) ## Lance le backend FastAPI (http://127.0.0.1:8000)
	@printf "$(GREEN)Starting backend on http://$(BE_HOST):$(BE_PORT) ...$(NC)\n"
	cd $(BACKEND) && uv run uvicorn ea.main:app --reload --host $(BE_HOST) --port $(BE_PORT)

run-fe: | $(FRONTEND)/node_modules ## Lance le frontend Vue/Vite (http://localhost:5173)
	@printf "$(GREEN)Starting frontend on http://localhost:$(FE_PORT) ...$(NC)\n"
	cd $(FRONTEND) && npm run dev -- --port $(FE_PORT)

run: ## Lance backend et frontend en parallèle (logs entrelacés, Ctrl-C arrête tout)
	@printf "$(GREEN)Starting full stack...$(NC)\n"
	@$(MAKE) --no-print-directory -j 2 run-be run-fe

# Garde-fous : une dépendance order-only sur un chemin absent échoue avec un
# message explicite plutôt qu'avec un « command not found » du shell.
$(VENV_PYTHON):
	@printf "$(RED)Environnement Python absent ($(VENV)).$(NC)\n"
	@printf "$(RED)Lance d'abord : make install$(NC)\n"
	@exit 1

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

## --- PostgreSQL local -----------------------------------------------------
#
# Encore inutilisé : aucune table n'existe. Reste local, contrairement au
# graphe, tant qu'aucun code ne s'y connecte.

pg-up: ## Démarre PostgreSQL en local
	docker compose up -d --wait postgres

pg-down: ## Arrête PostgreSQL (les données restent dans le volume)
	docker compose down

## --- Contrat front/back ---------------------------------------------------
#
# Le schéma OpenAPI est la source de vérité : `frontend/src/api/` en est
# dérivé, jamais écrit à la main. Les deux fichiers sont versionnés pour que
# `openapi-check` puisse constater qu'ils sont à jour — voir docs/adr/0007.

openapi: | $(VENV_PYTHON) $(FRONTEND)/node_modules ## Régénère le schéma OpenAPI et le client TypeScript
	cd $(BACKEND) && uv run python -m ea.openapi > $(OPENAPI_SCHEMA_NAME)
	cd $(FRONTEND) && npm run generate:api
	@printf "$(GREEN)Schéma et client régénérés.$(NC)\n"

openapi-check: | $(VENV_PYTHON) $(FRONTEND)/node_modules ## Échoue si le client versionné n'est plus celui du schéma
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

test-integration: | $(VENV_PYTHON) require-neo4j-password ## Tests contre le vrai Neo4j (vide le graphe partagé !)
	@printf "$(RED)Ces tests effacent les :Element du graphe PARTAGÉ : $(NEO4J_URI)$(NC)\n"
	@printf "$(RED)Personne ne doit être en train de modéliser dessus.$(NC)\n"
	cd $(BACKEND) && EA_ALLOW_DESTRUCTIVE_TESTS=1 EA_DEBUG=true \
		EA_NEO4J_PASSWORD='$(NEO4J_PASSWORD)' \
		EA_NEO4J_URI=$(NEO4J_URI) \
		uv run pytest tests/integration -q

lint: ## ruff format + check
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .

typecheck: ## mypy --strict, puis vue-tsc sur le frontend
	cd $(BACKEND) && uv run mypy src
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
