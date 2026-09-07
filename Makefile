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

# Base de données graphe (Neo4j). `docker-compose.yml` lit ces mêmes variables.
NEO4J_BOLT_PORT ?= 7687
NEO4J_HTTP_PORT ?= 7474
NEO4J_PASSWORD  ?= developmentonly
export NEO4J_BOLT_PORT NEO4J_HTTP_PORT NEO4J_PASSWORD

GREEN := \033[0;32m
RED   := \033[0;31m
NC    := \033[0m

.DEFAULT_GOAL := help
.PHONY: help install install-be install-fe run run-be run-fe clean \
        db-up db-up-all db-down db-logs db-shell db-reset \
        test test-unit test-integration lint typecheck check

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

db-up: ## Démarre Neo4j (bolt 7687, navigateur http://localhost:7474)
	@printf "$(GREEN)Starting Neo4j...$(NC)\n"
	docker compose up -d --wait neo4j
	@printf "$(GREEN)Neo4j prêt : bolt://localhost:$(NEO4J_BOLT_PORT)$(NC)\n"
	@printf "Navigateur : http://localhost:$(NEO4J_HTTP_PORT) (neo4j / $(NEO4J_PASSWORD))\n"

db-up-all: ## Démarre Neo4j *et* PostgreSQL (pas encore utilisé par le code)
	docker compose --profile full up -d --wait

db-down: ## Arrête les bases sans supprimer les données
	docker compose --profile full down

db-logs: ## Suit les logs de Neo4j
	docker compose logs -f neo4j

db-shell: ## Ouvre un cypher-shell sur le graphe
	docker compose exec neo4j cypher-shell -u neo4j -p $(NEO4J_PASSWORD)

db-reset: ## Supprime les volumes : le graphe repart vide
	@printf "$(RED)Cette commande efface le contenu du graphe.$(NC)\n"
	docker compose --profile full down -v

## --- Qualité --------------------------------------------------------------

test: ## Tests unitaires et API (sans base de données)
	cd $(BACKEND) && uv run pytest -q

test-unit: ## Boucle rapide : uniquement les tests unitaires
	cd $(BACKEND) && uv run pytest tests/unit -q

test-integration: | $(VENV_PYTHON) ## Tests contre le vrai Neo4j (vide le graphe !)
	@printf "$(RED)Les tests d'intégration effacent le contenu du graphe local.$(NC)\n"
	cd $(BACKEND) && EA_ALLOW_DESTRUCTIVE_TESTS=1 EA_DEBUG=true \
		EA_NEO4J_PASSWORD=$(NEO4J_PASSWORD) \
		EA_NEO4J_URI=bolt://localhost:$(NEO4J_BOLT_PORT) \
		uv run pytest tests/integration -q

lint: ## ruff format + check
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .

typecheck: ## mypy --strict
	cd $(BACKEND) && uv run mypy src

check: lint typecheck test ## Tout ce que la CI vérifiera

## --- Nettoyage ------------------------------------------------------------

clean: ## Supprime venv, node_modules, caches et artefacts de build
	rm -rf $(VENV)
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(BACKEND)/.coverage $(BACKEND)/htmlcov
	find $(BACKEND) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@printf "$(GREEN)Cleaned up!$(NC)\n"
