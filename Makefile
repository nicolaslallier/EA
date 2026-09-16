# Orchestrateur des commandes de développement local (Mac Studio).
#
# Cible unique d'entrée pour le stack Python (backend) + Vue (frontend).
# Voir docs/adr/0001-orchestration-locale-via-makefile.md.

BACKEND   := backend
FRONTEND  := frontend
# Second projet Python, son propre lockfile : Prefect épingle ses propres
# FastAPI/SQLAlchemy/Alembic, à des versions que ce dépôt ne contrôle pas — un
# lockfile commun avec backend/ forcerait l'un des deux à suivre l'autre. Voir
# docs/adr/0028.
PIPELINES := pipelines

# Environnement virtuel Python géré par uv (uv sync le crée dans backend/.venv).
# Le témoin vit *dans* le venv : `make clean` l'emporte avec lui, donc un venv
# supprimé ne peut pas laisser derrière lui un témoin qui mentirait au garde-fou.
VENV       := $(BACKEND)/.venv
VENV_STAMP := $(VENV)/.uv-sync-stamp

# Même garde-fou pour pipelines/, son propre venv.
PL_VENV       := $(PIPELINES)/.venv
PL_VENV_STAMP := $(PL_VENV)/.uv-sync-stamp

# Configuration locale, dérivée de l'exemple committé. Jamais versionnée.
BE_ENV := $(BACKEND)/.env

# Surchargeables en cas de conflit de port : `make run-be BE_PORT=8001`.
#
# BE_HOST est l'adresse d'*écoute*, pas une adresse à ouvrir : 0.0.0.0 rend
# l'API joignable depuis les autres postes du réseau. BE_URL est celle qu'on
# affiche, puisqu'on ne visite pas 0.0.0.0. Qui a le droit d'appeler /mcp n'en
# découle pas — c'est EA_MCP_ALLOW_REMOTE_CLIENTS (docs/adr/0023), voir backend/.env.example.
#
# FE_HOST suit la même règle depuis docs/adr/0022 : le SPA s'ouvre depuis les
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

# Le mot de passe n'a pas de valeur par défaut : l'instance est partagée. Il
# vient de l'environnement, ou à défaut de backend/.env.
#
# Il ne passe JAMAIS par une variable make. Make développe les `$` d'une
# valeur, et une valeur recollée entre apostrophes dans une recette casse sur
# la première apostrophe : un mot de passe généré contient les deux. Le shell
# de la recette le lit donc lui-même, et l'exporte vers la commande — voir
# POSTGRES_ENV, préfixe de recette.
#
# DOTENV_GET_PY lit une clé de backend/.env comme pydantic-settings la lit :
# `export ` toléré, guillemets retirés, commentaire de fin de ligne ignoré hors
# guillemets, dernière définition gagnante. Une variable d'environnement non
# vide passe avant le fichier, comme pour l'application. Il n'y a volontairement
# aucun `$` dans ce script : make le développerait.
define DOTENV_GET_PY
import os, re, sys
path, names = sys.argv[1], sys.argv[2:]
found = [os.environ[name] for name in names if os.environ.get(name)]
value = found[0] if found else ""
try:
    lines = [] if found else open(path, encoding="utf-8").read().splitlines()
except OSError:
    lines = []
for line in lines:
    match = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)", line)
    if not match or match.group(1) not in names:
        continue
    raw = match.group(2).strip()
    quoted = re.match(r"""(['"])(.*?)\1\s*(?:#.*)?\Z""", raw)
    if quoted and quoted.group(1) == "'":
        value = quoted.group(2)
    elif quoted:
        value = quoted.group(2).replace('\\"', '"').replace("\\\\", "\\")
    else:
        value = re.split(r"\s+#", raw, maxsplit=1)[0]
sys.stdout.write(value)
endef
export DOTENV_GET_PY

# La base du projet : le graphe (docs/adr/0033), les documents, les
# diagrammes. Une seule instance — mais pas sur le cluster : la base `ea` vit
# dans la stack ~/OpenCode/Infra de ce Mac, derrière son NGINX. Voir
# docs/adr/0015 et 0029.
POSTGRES_HOST ?= 127.0.0.1
POSTGRES_PORT ?= 5432
POSTGRES_USER ?= ea
POSTGRES_DB   ?= ea
# L'image porte pgvector : l'extension `vector` doit exister *dans l'image*,
# pas seulement être activée dans la base — la migration 0003 fait
# `CREATE EXTENSION vector`. Le conteneur jetable de docker-compose.yml part de
# la même image, et la base partagée a la même exigence (docs/adr/0019).
POSTGRES_IMAGE ?= pgvector/pgvector:pg17

# psql attend indéfiniment par défaut. Un poste dont les conteneurs ne joignent
# pas le LAN faisait pendre `make pg-ping` jusqu'au ^C, et la cible concluait
# ensuite sur une base qu'elle n'avait jamais interrogée.
PGCONNECT_TIMEOUT ?= 10

# Le client psql : celui du Mac s'il y en a un, sinon celui de l'image, dans un
# conteneur. Le conteneur était le seul chemin, et il ajoute deux pannes que la
# base n'a pas — le démon Docker peut être arrêté, et un conteneur ne joint pas
# forcément le LAN que le Mac joint. Une base que pgAdmin interroge pendant que
# `make pg-ping` la déclare injoignable, c'était l'une des deux, jamais elle.
PSQL_BIN  := $(shell command -v psql 2>/dev/null)
PSQL_ARGS  = -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB)

ifeq ($(PSQL_BIN),)
PSQL     = docker run --rm -e PGPASSWORD -e PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(POSTGRES_IMAGE) psql
PSQL_TTY = docker run --rm -it -e PGPASSWORD $(POSTGRES_IMAGE) psql
# Deux pannes, deux indices : un démon arrêté n'est pas une permission réseau
# manquante, et l'envoyer chercher dans les Réglages Système est un mensonge.
PG_UNREACHABLE_HINT := \
	if ! docker info >/dev/null 2>&1; then \
	  printf "Ici psql tourne dans un conteneur, et le démon Docker ne répond pas.\n  Démarre Docker Desktop, ou installe un client natif : brew install libpq\n"; \
	else \
	  printf "Le Mac joint-il $(POSTGRES_HOST) alors qu'un conteneur ne le joint pas ?\n  docker run --rm alpine nc -w5 -z $(POSTGRES_HOST) $(POSTGRES_PORT)\n  Si oui : Réglages Système → Confidentialité et sécurité → Réseau local → Docker.\n"; \
	fi;
else
PSQL     = PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(PSQL_BIN)
PSQL_TTY = $(PSQL_BIN)
PG_UNREACHABLE_HINT := printf "Le client est celui du Mac ($(PSQL_BIN)) : ni Docker ni le réseau des conteneurs n'entrent en jeu.\n  Vérifie l'hôte, le port, et EA_POSTGRES_PASSWORD dans $(BACKEND)/.env.\n";
endif

# Pas de valeur par défaut : l'instance est partagée. Le shell de la recette
# la lit — POSTGRES_PASSWORD ou EA_POSTGRES_PASSWORD dans l'environnement,
# sinon backend/.env — et l'exporte sous le nom que psql, pg_dump et
# pg_restore lisent eux-mêmes.
POSTGRES_ENV = export PGPASSWORD="$$(python3 -c "$$DOTENV_GET_PY" $(BE_ENV) POSTGRES_PASSWORD EA_POSTGRES_PASSWORD)";

# Sauvegardes de la base partagée (docs/adr/0025). Même règle que psql : les
# clients du Mac s'il y en a, sinon ceux de l'image. Un dump se restaure avec un
# pg_restore au moins aussi récent que le pg_dump qui l'a écrit — celui de
# l'image (17) ne relit pas une archive du client 18 du Mac.
BACKUP_DIR     ?= backups
PG_DUMP_BIN    := $(shell command -v pg_dump 2>/dev/null)
PG_RESTORE_BIN := $(shell command -v pg_restore 2>/dev/null)

ifeq ($(PG_DUMP_BIN),)
PG_DUMP = docker run --rm -e PGPASSWORD -e PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(POSTGRES_IMAGE) pg_dump
else
PG_DUMP = PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(PG_DUMP_BIN)
endif
# L'archive arrive sur l'entrée standard dans les deux cas : un conteneur ne
# voit pas les fichiers du poste.
ifeq ($(PG_RESTORE_BIN),)
PG_RESTORE = docker run --rm -i -e PGPASSWORD -e PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(POSTGRES_IMAGE) pg_restore
else
PG_RESTORE = PGCONNECT_TIMEOUT=$(PGCONNECT_TIMEOUT) $(PG_RESTORE_BIN)
endif

# Le conteneur jetable de docker-compose.yml, contre lequel tournent les tests
# d'intégration : `alembic downgrade base` ne doit jamais viser le partagé. Son
# mot de passe est celui que docker-compose.yml porte déjà en clair. Pas 5432 :
# c'est le port de la base partagée depuis docs/adr/0029, et les fixtures le
# refusent même sur 127.0.0.1.
POSTGRES_TEST_PASSWORD ?= developmentonly
POSTGRES_TEST_PORT     ?= 5433

# Même logique pour le MinIO jetable : 9100, pas le nom de l'instance
# partagée — les fixtures refusent tout le reste (tests/integration/throwaway.py).
MINIO_TEST_PORT     ?= 9100
MINIO_TEST_PASSWORD ?= developmentonly

# docker compose lit ports et mots de passe dans son environnement : on les lui
# passe explicitement, depuis les mêmes variables que les tests, pour que les
# conteneurs et la suite ne puissent pas viser des ports différents.
COMPOSE_TEST := POSTGRES_TEST_PORT=$(POSTGRES_TEST_PORT) \
	POSTGRES_TEST_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
	MINIO_TEST_PORT=$(MINIO_TEST_PORT) \
	MINIO_TEST_PASSWORD='$(MINIO_TEST_PASSWORD)' \
	docker compose

# Service d'embeddings : Ollama sur le cluster, servant un /v1/embeddings
# compatible OpenAI sur 11435. Voir docs/adr/0019 et docs/adr/0038.
EMBEDDINGS_URL   ?= http://192.168.2.10:11435/v1
EMBEDDINGS_MODEL ?= mxbai-embed-large

# Contrat front/back : le schéma est versionné, le client TypeScript en dérive.
OPENAPI_SCHEMA_NAME := openapi.json
OPENAPI_SCHEMA      := $(BACKEND)/$(OPENAPI_SCHEMA_NAME)
GENERATED_CLIENT    := $(FRONTEND)/src/api/schema.d.ts

GREEN := \033[0;32m
RED   := \033[0;31m
NC    := \033[0m

.DEFAULT_GOAL := help
.PHONY: help install install-be install-fe run run-be run-fe clean \
        pg-up pg-down pg-ping pg-shell pg-migrate pg-revision pg-history \
        pg-vector-check pg-stack app-stack require-postgres-password \
        app-up app-down app-delete app-ps app-logs \
        compose-up compose-down compose-ps compose-logs compose-reset \
        embed-ping embed-models docs-reindex openapi openapi-check \
        test test-unit test-integration test-postgres test-fe lint typecheck check \
        lint-check lint-fe typecheck-be typecheck-fe audit hooks \
        pg-backup pg-restore graph-import \
        pipelines-install pipelines-lint pipelines-lint-check pipelines-typecheck \
        pipelines-test pipelines-check pipelines-audit \
        require-pipelines-env pipelines-up pipelines-down pipelines-logs pipelines-run \
        pipelines-db-howto

help: ## Liste les cibles disponibles
	@printf "$(GREEN)Cibles disponibles :$(NC)\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## --- Installation ---------------------------------------------------------

install: install-be install-fe pipelines-install ## Installe toutes les dépendances (BE + FE + pipelines)

install-be: $(BE_ENV) ## Crée le venv et installe les dépendances Python
	@printf "$(GREEN)Installing backend dependencies (uv)...$(NC)\n"
	@command -v uv >/dev/null 2>&1 || { \
		printf "$(RED)uv est introuvable. Installe-le : brew install uv$(NC)\n"; exit 1; }
	cd $(BACKEND) && uv sync --all-extras
	@touch $(VENV_STAMP)

# `npm ci`, pas `npm install` : installer n'est pas mettre à jour. `ci` installe
# exactement package-lock.json et échoue s'il ne correspond plus à
# package.json, là où `install` réécrit le lockfile en silence. Ajouter une
# dépendance reste `npm install <paquet>`, un geste délibéré.
install-fe: ## Installe les dépendances Node du frontend (npm ci, lockfile exact)
	@printf "$(GREEN)Installing frontend dependencies (npm ci)...$(NC)\n"
	cd $(FRONTEND) && npm ci

## --- Exécution ------------------------------------------------------------

run-be: | $(VENV_STAMP) $(BE_ENV) ## Lance le backend FastAPI (écoute 0.0.0.0:8000)
	@printf "$(GREEN)Starting backend on $(BE_HOST):$(BE_PORT) — $(BE_URL)$(NC)\n"
	cd $(BACKEND) && uv run uvicorn ea.main:app --reload --host $(BE_HOST) --port $(BE_PORT)

run-fe: | $(FRONTEND)/node_modules ## Lance le frontend Vue/Vite (écoute 0.0.0.0:5173)
	@printf "$(GREEN)Starting frontend on $(FE_HOST):$(FE_PORT) — $(FE_URL)$(NC)\n"
	@# http://$(LAN_IP):$(FE_PORT) sert la page mais ne connecte personne : PKCE exige
	@# crypto.subtle, qu'un navigateur réserve aux contextes sécurisés (docs/adr/0032).
	@test -z "$(LAN_IP)" || printf "  Depuis un autre poste, pas http://$(LAN_IP):$(FE_PORT) (la connexion Keycloak exige un contexte sécurisé) :\n"
	@test -z "$(LAN_IP)" || printf "    ssh -L $(FE_PORT):127.0.0.1:$(FE_PORT) -L $(BE_PORT):127.0.0.1:$(BE_PORT) $(LAN_IP), puis http://localhost:$(FE_PORT)\n"
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
$(BE_ENV):
	@printf "$(GREEN)Creating $(BE_ENV) from .env.example...$(NC)\n"
	@cp $(BACKEND)/.env.example $@
	@printf "$(RED)Renseigne EA_POSTGRES_PASSWORD dans $@ — voir make pg-stack.$(NC)\n"

$(FRONTEND)/node_modules:
	@printf "$(RED)Dépendances Node absentes ($(FRONTEND)/node_modules).$(NC)\n"
	@printf "$(RED)Lance d'abord : make install$(NC)\n"
	@exit 1

## --- PostgreSQL -----------------------------------------------------------
#
# Une instance unique et partagée ($(POSTGRES_HOST), docs/adr/0029) :
# `pg-ping`, `pg-shell` et `pg-migrate` s'y connectent, aucune ne la démarre.
# Le client psql est pris dans l'image Postgres plutôt qu'installé sur le poste.
#
# `pg-up` fait exception : il lance le conteneur *jetable* de
# docker-compose.yml, celui contre lequel tournent les tests d'intégration.
# Ceux-ci appliquent puis annulent les migrations, ce qu'on ne fait pas sur une
# base que d'autres utilisent.
#
# La première table existe : `element_documents`, les fichiers markdown
# attachés aux éléments (docs/adr/0017).

require-postgres-password:
	@$(POSTGRES_ENV) test -n "$$PGPASSWORD" || { \
		printf "$(RED)POSTGRES_PASSWORD est vide.$(NC)\n"; \
		printf "Renseigne EA_POSTGRES_PASSWORD dans $(BACKEND)/.env, ou lance :\n"; \
		printf "  POSTGRES_PASSWORD='...' make $(MAKECMDGOALS)\n"; exit 1; }

pg-ping: | require-postgres-password ## Vérifie que la base partagée répond
	@printf "$(GREEN)Interrogation de $(POSTGRES_USER)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB) ...$(NC)\n"
	@$(POSTGRES_ENV) $(PSQL) $(PSQL_ARGS) -c 'SELECT version()' \
	|| { printf "$(RED)Aucune réponse. Vérifie l'hôte et le mot de passe.$(NC)\n"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }

pg-shell: | require-postgres-password ## Ouvre un psql sur la base partagée
	@$(POSTGRES_ENV) $(PSQL_TTY) $(PSQL_ARGS)

pg-migrate: | $(VENV_STAMP) ## Applique les migrations Alembic jusqu'à head (base partagée)
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

pg-vector-check: | require-postgres-password ## Vérifie que pgvector est disponible sur la base partagée
	@printf "$(GREEN)pgvector sur $(POSTGRES_HOST)/$(POSTGRES_DB) ?$(NC)\n"
	@$(POSTGRES_ENV) out=$$($(PSQL) $(PSQL_ARGS) \
		-tAc "SELECT default_version FROM pg_available_extensions WHERE name = 'vector'" 2>&1) \
	|| { printf "$(RED)Connexion impossible : la question n'a pas été posée.$(NC)\n"; \
	     printf "%s\n" "$$out"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }; \
	test -n "$$out" \
	|| { printf "$(RED)Serveur joint, mais l'extension vector y est absente.$(NC)\n"; \
	     printf "L'image du serveur doit être pgvector/pgvector:pgNN, pas postgres:NN —\n"; \
	     printf "voir make pg-stack, docs/adr/0019 et 0029.\n"; exit 1; }; \
	printf "$(GREEN)pgvector disponible (%s).$(NC)\n" "$$out"

app-stack: ## Rappelle comment déployer l'API et le SPA derrière le NGINX de l'Infra
	@printf "$(GREEN)Stack EA : deploy/ea.stack.yml → https://ea.infra.famillelallier.net$(NC)\n"
	@printf "  0. Infra : EA_DB_PASSWORD dans .env, puis make provision-app app=ea\n"
	@printf "  1. .portainer.env : PORTAINER_API_KEY=… (Portainer → My account → Access tokens)\n"
	@printf "     ou celui de l'Infra : PORTAINER_ENV_FILE=~/OpenCode/Infra/.portainer.env\n"
	@printf "  2. cp deploy/ea.env.example deploy/ea.env, puis le remplir\n"
	@printf "  3. make app-up — main doit être poussé : Portainer clone GitHub, pas ce poste\n"
	@printf "  4. make app-ps, puis : curl -k https://ea.infra.famillelallier.net/api/health\n"

# La stack « ea » vit dans le Portainer de l'Infra, sur le Docker de ce Mac.
# L'écrire passe par l'API de Portainer (scripts/portainer-stack.sh), pour qu'il
# en reste le propriétaire ; la lire passe par docker compose, qui retrouve le
# projet `ea` par ses étiquettes, sans fichier. Voir docs/adr/0027.
PORTAINER_STACK := scripts/portainer-stack.sh
APP_COMPOSE     := docker compose -p ea

app-up: ## Crée ou redéploie la stack EA dans Portainer (GitHub main, images reconstruites)
	$(PORTAINER_STACK) up

app-down: ## Arrête la stack EA dans Portainer (elle reste déclarée)
	$(PORTAINER_STACK) down

app-delete: ## Retire la stack EA de Portainer (CONFIRM=yes obligatoire)
	@test "$$CONFIRM" = "yes" || { \
		printf "$(RED)Retire la stack ea de Portainer : https://ea.infra.famillelallier.net ne répond plus.$(NC)\n"; \
		printf "Relance avec : make app-delete CONFIRM=yes\n"; exit 1; }
	$(PORTAINER_STACK) delete

app-ps: ## État des conteneurs de la stack EA
	$(APP_COMPOSE) ps

app-logs: ## Suit les logs de la stack EA (s=api ou s=web pour un seul service)
	$(APP_COMPOSE) logs -f $(if $(s),"$(s)",)

pg-stack: ## Rappelle où vit la base relationnelle et comment la provisionner
	@printf "$(GREEN)PostgreSQL : la stack ~/OpenCode/Infra de ce Mac — docs/adr/0029$(NC)\n"
	@printf "  1. Dans ~/OpenCode/Infra : make provision-app app=ea\n"
	@printf "     (rôle ea à moindre privilège, extension vector créée par le superutilisateur)\n"
	@printf "  2. EA_DB_PASSWORD du .env d'Infra → EA_POSTGRES_PASSWORD de $(BE_ENV)\n"
	@printf "  3. Puis : make pg-ping && make pg-vector-check && make pg-migrate\n"
	@printf "deploy/postgres.stack.yml n'est plus déployé nulle part.\n"

# `pg_dump` lit la base en ligne, dans un instantané cohérent : rien à arrêter.
# Le dump est écrit dans un `.partial` et ne prend son nom qu'une fois pg_dump
# réussi et sa table des matières relue — un fichier tronqué ne porte jamais le
# nom d'une sauvegarde. `backups/` est ignoré par git et fermé aux autres
# comptes : un dump contient chaque document attaché au modèle. Voir
# docs/adr/0025.
pg-backup: | require-postgres-password ## Sauvegarde la base partagée dans backups/ (pg_dump -Fc)
	@mkdir -p $(BACKUP_DIR) && chmod 700 $(BACKUP_DIR)
	@$(POSTGRES_ENV) umask 077; \
	file="$(BACKUP_DIR)/postgres-$(POSTGRES_DB)-$$(date -u +%Y%m%dT%H%M%SZ).dump"; \
	printf "$(GREEN)pg_dump de $(POSTGRES_HOST)/$(POSTGRES_DB) → %s$(NC)\n" "$$file"; \
	$(PG_DUMP) $(PSQL_ARGS) --format=custom > "$$file.partial" \
	|| { rm -f "$$file.partial"; \
	     printf "$(RED)pg_dump a échoué : aucune sauvegarde n'a été écrite.$(NC)\n"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }; \
	$(PG_RESTORE) --list < "$$file.partial" > /dev/null \
	|| { rm -f "$$file.partial"; \
	     printf "$(RED)L'archive écrite ne se relit pas : sauvegarde écartée.$(NC)\n"; exit 1; }; \
	mv "$$file.partial" "$$file"; \
	printf "$(GREEN)Sauvegarde écrite : %s (%s)$(NC)\n" "$$file" "$$(du -h "$$file" | cut -f1)"
	@printf "Ce poste n'est pas un lieu de conservation : copie le fichier ailleurs.\n"

# Remplace le contenu de la base PARTAGÉE. Une seule transaction : une erreur au
# milieu annule tout, la base est restaurée ou inchangée, jamais à moitié.
# FILE et CONFIRM sont lus par le shell, pas recollés dans la recette : un nom
# de fichier n'a pas à survivre à l'analyse de make.
pg-restore: | require-postgres-password ## Restaure un dump dans la base partagée (FILE=... CONFIRM=yes)
	@test -n "$$FILE" && test -f "$$FILE" || { \
		printf "$(RED)FILE ne désigne aucun fichier.$(NC)\n"; \
		printf "Lance : make pg-restore FILE=$(BACKUP_DIR)/postgres-$(POSTGRES_DB)-<horodatage>.dump CONFIRM=yes\n"; \
		ls -1t $(BACKUP_DIR)/*.dump 2>/dev/null | head -5 | sed 's/^/  /'; exit 1; }
	@test "$$CONFIRM" = "yes" || { \
		printf "$(RED)Cette commande REMPLACE la base PARTAGÉE : $(POSTGRES_HOST)/$(POSTGRES_DB)$(NC)\n"; \
		printf "$(RED)Tout ce qui a été écrit depuis la sauvegarde est perdu, pour tout le monde.$(NC)\n"; \
		printf "Relance avec : make pg-restore FILE=%s CONFIRM=yes\n" "$$FILE"; exit 1; }
	@$(POSTGRES_ENV) $(PG_RESTORE) --list < "$$FILE" > /dev/null \
	|| { printf "$(RED)%s n'est pas une archive pg_dump lisible : rien n'a été touché.$(NC)\n" "$$FILE"; exit 1; }
	@$(POSTGRES_ENV) $(PG_RESTORE) $(PSQL_ARGS) --clean --if-exists --no-owner \
		--single-transaction --exit-on-error < "$$FILE" \
	|| { printf "$(RED)Restauration échouée : la transaction est annulée, la base est inchangée.$(NC)\n"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }
	@printf "$(GREEN)Base restaurée depuis %s.$(NC)\n" "$$FILE"
	@printf "L'index des passages est revenu avec les documents.\n"
	@printf "Hors retour arrière, si le dump précède head : make pg-migrate.\n"
	@printf "$(RED)Retour arrière de la bascule du graphe (docs/adr/0033) : PAS de make pg-migrate.$(NC)\n"
	@printf "Avant de relancer make graph-import : DROP TABLE relationships, elements (voir l'ADR).\n"

# Ponctuel (docs/adr/0033) : lit tout le graphe Neo4j avant d'écrire quoi que ce
# soit, le copie dans PostgreSQL, puis applique la révision 0006. Un échec après
# la révision 0005 affiche la commande de retour : alembic downgrade 0004. Vise la base PARTAGÉE de backend/.env : API arrêtée
# (make app-down) et make pg-backup d'abord. Le pilote neo4j n'est chargé que
# pour cette commande. EA_NEO4J_PASSWORD est lu dans backend/.env ou
# l'environnement. Supprimée avec le script une fois la bascule confirmée.
graph-import: | $(VENV_STAMP) ## Importe une fois le graphe Neo4j dans PostgreSQL (CONFIRM=yes)
	@test "$(CONFIRM)" = "yes" || { \
		printf "$(RED)Écrit dans la base PARTAGÉE. D'abord : make app-down, make pg-backup.$(NC)\n"; \
		printf "Relance avec : make graph-import CONFIRM=yes\n"; exit 1; }
	cd $(BACKEND) && uv run --with 'neo4j==6.3.0' python scripts/import_neo4j.py

pg-up: ## Démarre le PostgreSQL jetable local (pour les tests)
	$(COMPOSE_TEST) up -d --wait postgres

minio-up: ## Démarre le MinIO jetable local (pour les tests)
	$(COMPOSE_TEST) up -d --wait minio

pg-down: ## Arrête le PostgreSQL jetable local (garde son volume)
	docker compose down

compose-up: ## Démarre le PostgreSQL jetable local et attend qu'il soit sain
	$(COMPOSE_TEST) up -d --wait

compose-down: pg-down ## Arrête les bases jetables locales (alias de pg-down)

compose-ps: ## État des bases jetables locales
	docker compose ps

compose-logs: ## Suit les logs du PostgreSQL jetable
	docker compose logs -f $(if $(s),"$(s)",)

# Jetable veut dire jetable : le volume ne tient que ce que les tests écrivent.
compose-reset: ## Arrête les bases jetables et supprime le volume de PostgreSQL
	docker compose down -v

## --- Embeddings -----------------------------------------------------------
#
# Ollama tourne sur le cluster et sert un /v1/embeddings compatible OpenAI.
# Rien ici ne le démarre ni ne tire un modèle : comme la base, c'est une
# instance partagée.
# La largeur des vecteurs (1024) est celle de la colonne, pas un réglage — voir
# docs/adr/0019.

embed-models: ## Liste les modèles qu'Ollama expose
	@curl -sf --max-time 10 $(EMBEDDINGS_URL)/models \
		| python3 -c 'import json,sys; [print(m["id"]) for m in json.load(sys.stdin)["data"]]' \
	|| { printf "$(RED)Aucune réponse de $(EMBEDDINGS_URL).$(NC)\n"; exit 1; }

embed-ping: ## Vérifie que le modèle d'embedding répond, et à quelle largeur
	@printf "$(GREEN)$(EMBEDDINGS_MODEL) sur $(EMBEDDINGS_URL) ...$(NC)\n"
	@curl -sf --max-time 120 $(EMBEDDINGS_URL)/embeddings \
		-H 'Content-Type: application/json' \
		-d '{"model":"$(EMBEDDINGS_MODEL)","input":["ping"]}' \
		| python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]), "dimensions")' \
	|| { printf "$(RED)Pas de réponse. Le modèle est-il tiré (ollama pull $(EMBEDDINGS_MODEL)) ?$(NC)\n"; exit 1; }

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

# Chaque cible qui passe par `uv run` exige le témoin du venv : sans lui, un
# clone neuf laissait `uv run` construire un environnement SANS l'extra `dev`
# (ni pytest, ni ruff, ni mypy), et l'échec disait « command not found ».
#
# `--cov=ea` : le plancher de 90 % est dans pyproject.toml
# ([tool.coverage.report] fail_under), donc `test` échoue en dessous — et
# `check` et la CI avec lui. Voir docs/adr/0026.
test: | $(VENV_STAMP) ## Tests unitaires et API (sans base), plancher de couverture 90 %
	cd $(BACKEND) && uv run pytest tests/unit tests/e2e -q --cov=ea

test-unit: | $(VENV_STAMP) ## Boucle rapide : uniquement les tests unitaires
	cd $(BACKEND) && uv run pytest tests/unit -q

test-fe: | $(FRONTEND)/node_modules ## Tests Vitest du frontend (une passe, sans watch)
	cd $(FRONTEND) && npm test -- --run

# Contre les conteneurs jetables, jamais contre les stores partagés : ces
# tests annulent la chaîne de migrations, graphe compris, et vident un bucket.
# Les fixtures refusent de toute façon un hôte qui n'est pas local et le port
# du store partagé (docs/adr/0024). Les embeddings sont coupés : le démarrage
# de l'application irait sinon interroger l'Ollama du cluster, et la
# suite n'en dépend pas. EA_S3_ENABLED n'est volontairement pas mis : le boot
# de l'application doit rester possible sans bucket (test_application_boot).
test-integration: | $(VENV_STAMP) ## Tests d'intégration contre PostgreSQL et MinIO jetables locaux (les démarre au besoin)
	$(COMPOSE_TEST) up -d --wait postgres minio
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		EA_EMBEDDINGS_ENABLED=false \
		EA_S3_ENDPOINT=127.0.0.1:$(MINIO_TEST_PORT) EA_S3_SECURE=false \
		EA_S3_ACCESS_KEY=ea-test EA_S3_SECRET_KEY='$(MINIO_TEST_PASSWORD)' EA_S3_BUCKET=ea-test \
		uv run pytest tests/integration -q

# Explicitement contre le conteneur jetable, jamais contre le cluster : ces
# tests appliquent puis annulent la chaîne de migrations. Le port est celui que
# `COMPOSE_TEST` publie, pas une seconde valeur écrite en dur. Les embeddings
# sont coupés comme pour `test-integration` : le démarrage de l'application
# irait sinon interroger Ollama, et aucun test ne sort de la machine.
test-postgres: | $(VENV_STAMP) ## Tests contre le PostgreSQL jetable local (le démarre au besoin)
	$(COMPOSE_TEST) up -d --wait postgres
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		EA_EMBEDDINGS_ENABLED=false \
		uv run pytest tests/integration -m postgres -q

# Corriger et vérifier sont deux gestes. `lint` réécrit les fichiers, donc ne
# peut jamais échouer sur le formatage ; `lint-check` ne touche à rien, et
# échoue. `check`, pre-commit et la CI n'appellent que le second : une
# vérification qui corrige en passant valide un code que personne n'a relu.
lint: | $(VENV_STAMP) ## Corrige : ruff format, puis ruff check --fix
	cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .

lint-check: | $(VENV_STAMP) ## Vérifie sans rien modifier : ruff format --check, ruff check
	cd $(BACKEND) && uv run ruff format --check . && uv run ruff check .

lint-fe: | $(FRONTEND)/node_modules ## ESLint sur le frontend, sans correction
	cd $(FRONTEND) && npm run lint

typecheck: typecheck-be typecheck-fe ## mypy --strict, puis vue-tsc sur le frontend

# `migrations` en plus de `src` : env.py et les révisions sont du code exécuté
# en production, pas des fichiers générés qu'on ne relit jamais.
typecheck-be: | $(VENV_STAMP) ## mypy --strict sur src et migrations
	cd $(BACKEND) && uv run mypy src migrations

typecheck-fe: | $(FRONTEND)/node_modules ## vue-tsc sur le frontend
	cd $(FRONTEND) && npm run typecheck

# Les trois scanners de CLAUDE.md, tous lancés même quand le premier échoue :
# un bandit rouge ne doit pas cacher un npm audit rouge. bandit lit
# [tool.bandit] dans pyproject.toml (tests exclus) ; un faux positif se tait à
# la ligne, par `# nosec BXXX` et sa raison. pip-audit audite le venv tel qu'il
# est installé — `ea` lui-même, éditable, n'est pas sur PyPI. npm audit bloque
# à partir de `high`. Les trois ont besoin du réseau. Voir docs/adr/0026.
audit: | $(VENV_STAMP) $(FRONTEND)/node_modules ## bandit, pip-audit et npm audit (high)
	@status=0; \
	printf "$(GREEN)bandit$(NC)\n"; \
	(cd $(BACKEND) && uv run bandit -c pyproject.toml -r src -q) || status=1; \
	printf "$(GREEN)pip-audit$(NC)\n"; \
	(cd $(BACKEND) && uv run pip-audit --skip-editable) || status=1; \
	printf "$(GREEN)npm audit$(NC)\n"; \
	(cd $(FRONTEND) && npm audit --audit-level=high) || status=1; \
	test $$status -eq 0 || printf "$(RED)Au moins un scanner a échoué — voir ci-dessus.$(NC)\n"; \
	exit $$status

check: lint-check lint-fe typecheck openapi-check test test-fe pipelines-check ## Tout ce que la CI vérifiera (ne modifie aucun fichier)

# pre-commit n'est pas une dépendance du projet : uvx le prend à la version
# épinglée ici. Ses crochets appellent `uv run` et `npm run`, donc ruff et mypy
# sont ceux de uv.lock. `hooks` écrit dans .git/hooks : un choix de chaque
# poste, que rien d'autre ne fait à sa place. Voir .pre-commit-config.yaml.
PRE_COMMIT ?= uvx pre-commit@4.6.2

hooks: ## Installe les crochets pre-commit dans .git (ruff, mypy, vue-tsc, eslint, gitleaks)
	$(PRE_COMMIT) install
	@printf "$(GREEN)Crochets installés. Sur tout le dépôt : $(PRE_COMMIT) run --all-files$(NC)\n"

## --- Pipelines --------------------------------------------------------------
#
# Second projet Python, son propre venv, sa propre barrière — voir docs/adr/0028.
# Les cibles de vérification ne démarrent ni Prefect, ni LiteLLM, ni MinIO :
# seules pipelines-up/-down/-run touchent la stack Docker, plus bas.
# `pipelines-check` est ce que `check` et la CI appellent ; aucune des deux ne
# modifie de fichier.

$(PL_VENV_STAMP): $(PIPELINES)/pyproject.toml $(PIPELINES)/uv.lock
	@printf "$(RED)Environnement Python absent ou périmé ($(PL_VENV)).$(NC)\n"
	@printf "$(RED)Lance d'abord : make pipelines-install$(NC)\n"
	@exit 1

pipelines-install: ## Crée le venv de pipelines/ et installe ses dépendances Python
	@printf "$(GREEN)Installing pipelines dependencies (uv)...$(NC)\n"
	@command -v uv >/dev/null 2>&1 || { \
		printf "$(RED)uv est introuvable. Installe-le : brew install uv$(NC)\n"; exit 1; }
	cd $(PIPELINES) && uv sync --all-extras
	@touch $(PL_VENV_STAMP)

pipelines-lint: | $(PL_VENV_STAMP) ## Corrige : ruff format, puis ruff check --fix (pipelines/)
	cd $(PIPELINES) && uv run ruff format . && uv run ruff check --fix .

pipelines-lint-check: | $(PL_VENV_STAMP) ## Vérifie sans rien modifier : ruff format --check, ruff check (pipelines/)
	cd $(PIPELINES) && uv run ruff format --check . && uv run ruff check .

pipelines-typecheck: | $(PL_VENV_STAMP) ## mypy --strict sur pipelines/src
	cd $(PIPELINES) && uv run mypy src

pipelines-test: | $(PL_VENV_STAMP) ## Tests de pipelines/, plancher de couverture 90 %
	cd $(PIPELINES) && uv run pytest -q --cov=pipelines

pipelines-check: pipelines-lint-check pipelines-typecheck pipelines-test ## Tout ce que la CI vérifiera pour pipelines/

pipelines-audit: | $(PL_VENV_STAMP) ## bandit et pip-audit sur pipelines/
	@status=0; \
	printf "$(GREEN)bandit (pipelines)$(NC)\n"; \
	(cd $(PIPELINES) && uv run bandit -c pyproject.toml -r src -q) || status=1; \
	printf "$(GREEN)pip-audit (pipelines)$(NC)\n"; \
	(cd $(PIPELINES) && uv run pip-audit --skip-editable) || status=1; \
	test $$status -eq 0 || printf "$(RED)Au moins un scanner a échoué — voir ci-dessus.$(NC)\n"; \
	exit $$status

# La stack Docker de pipelines/ : Prefect + LiteLLM + le worker qui sert le
# flow. --env-file pointe pipelines/.env, jamais committé (voir .gitignore) —
# require-pipelines-env est ce qui donne un message clair plutôt que l'erreur
# brute de docker compose quand ce fichier manque.
PL_COMPOSE := docker compose -f $(PIPELINES)/docker-compose.yml --env-file $(PIPELINES)/.env

# Même rôle que $(BE_ENV), sans la création automatique : les secrets de ce
# fichier (mots de passe, clé LiteLLM) ne peuvent pas venir d'un copier-coller
# de l'exemple, contrairement à backend/.env qui démarre en EA_DEBUG sans eux.
require-pipelines-env:
	@test -f $(PIPELINES)/.env || { \
		printf "$(RED)$(PIPELINES)/.env absent.$(NC)\n"; \
		printf "Copie $(PIPELINES)/.env.example vers $(PIPELINES)/.env et renseigne les secrets\n"; \
		printf "(mots de passe : openssl rand -hex 32) — voir make pipelines-db-howto.\n"; exit 1; }

pipelines-up: | require-pipelines-env ## Démarre Prefect + LiteLLM + le worker (pipelines/docker-compose.yml)
	$(PL_COMPOSE) up -d --build
	@printf "$(GREEN)Prefect : http://127.0.0.1:4200 — LiteLLM : http://127.0.0.1:4000$(NC)\n"

pipelines-down: ## Arrête la stack Prefect + LiteLLM + le worker
	$(PL_COMPOSE) down

pipelines-logs: ## Suit les logs de la stack (docker compose logs -f)
	$(PL_COMPOSE) logs -f

# inbox/ est le préfixe où le catalogue dépose ce que le flow doit lire — voir
# la définition du flow alimenter-catalogue.
PREFIX ?= inbox/

pipelines-run: ## Déclenche alimenter-catalogue/manuel dans le worker (PREFIX=... défaut inbox/)
	$(PL_COMPOSE) exec worker prefect deployment run 'alimenter-catalogue/manuel' --param "prefix=$(PREFIX)" --watch

# Imprime seulement, comme pg-stack : les rôles et les bases
# vivent dans le PostgreSQL de la stack ~/OpenCode/Infra (docs/adr/0029), où le
# rôle `ea` ne crée pas de rôle — la provision passe par l'Infra, comme pour `ea`.
# Cette cible ne réclame ni mot de passe ni pipelines/.env pour la rappeler.
pipelines-db-howto: ## Rappelle comment préparer les rôles PostgreSQL et le bucket MinIO de pipelines/
	@printf "$(GREEN)Préparation manuelle — rien ne s'exécute d'ici$(NC)\n"
	@printf "\n$(GREEN)1. Dans ~/OpenCode/Infra :$(NC)\n"
	@printf '%s\n' \
		"  .env : PREFECT_DB_PASSWORD, LITELLM_DB_PASSWORD, et prefect,litellm dans APP_DATABASES" \
		"  make provision-app app=prefect && make provision-app app=litellm" \
		"  make psql, puis :" \
		"  \\c prefect" \
		"  CREATE EXTENSION IF NOT EXISTS pg_trgm;"
	@printf "\n$(GREEN)2. MinIO — https://minio-console.famillelallier.net :$(NC)\n"
	@printf '%s\n' \
		"  Bucket ea-catalogue" \
		"  Un utilisateur dédié, politique en lecture seule sur ce bucket"
	@printf "\n$(GREEN)3. Mots de passe : openssl rand -hex 32$(NC)\n"

## --- Nettoyage ------------------------------------------------------------

clean: ## Supprime venv, node_modules, caches et artefacts de build
	rm -rf $(VENV) $(PL_VENV)
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(BACKEND)/.coverage $(BACKEND)/htmlcov
	rm -rf $(PIPELINES)/.pytest_cache $(PIPELINES)/.mypy_cache $(PIPELINES)/.ruff_cache
	rm -rf $(PIPELINES)/.coverage $(PIPELINES)/htmlcov
	find $(BACKEND) -type d -name "__pycache__" -prune -exec rm -rf {} +
	find $(PIPELINES) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@printf "$(GREEN)Cleaned up!$(NC)\n"
