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

GREEN := \033[0;32m
RED   := \033[0;31m
NC    := \033[0m

.DEFAULT_GOAL := help
.PHONY: help install install-be install-fe run run-be run-fe clean

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

## --- Nettoyage ------------------------------------------------------------

clean: ## Supprime venv, node_modules, caches et artefacts de build
	rm -rf $(VENV)
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(BACKEND)/.coverage $(BACKEND)/htmlcov
	find $(BACKEND) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@printf "$(GREEN)Cleaned up!$(NC)\n"
