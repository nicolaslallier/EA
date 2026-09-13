# Orchestrateur des commandes de développement local (Mac Studio).
#
# Cible unique d'entrée pour le stack Python (backend) + Vue (frontend).
# Voir docs/adr/0001-orchestration-locale-via-makefile.md.

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

# Les mots de passe n'ont pas de valeur par défaut : les deux instances sont
# partagées. Ils viennent de l'environnement, ou à défaut de backend/.env.
#
# Ils ne passent JAMAIS par une variable make. Make développe les `$` d'une
# valeur, et une valeur recollée entre apostrophes dans une recette casse sur
# la première apostrophe : un mot de passe généré contient les deux. Le shell
# de la recette les lit donc lui-même, et les exporte vers la commande — voir
# NEO4J_ENV et POSTGRES_ENV, préfixes de recette.
#
# DOTENV_GET_PY lit une clé de backend/.env comme pydantic-settings la lit :
# `export ` toléré, guillemets retirés, commentaire de fin de ligne ignoré hors
# guillemets, dernière définition gagnante. Une variable d'environnement non
# vide passe avant le fichier, comme pour l'application. Il n'y a volontairement
# aucun `$` dans ce script : make le développerait.
#
# `make db-ping NEO4J_PASSWORD=...` fonctionne toujours, mais make y développe
# encore `$` : pour un tel mot de passe, NEO4J_PASSWORD='...' make db-ping.
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
    if len(raw) >= 2 and raw[0] == "'" and raw.endswith("'"):
        value = raw[1:-1]
    elif len(raw) >= 2 and raw[0] == '"' and raw.endswith('"'):
        value = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    else:
        value = re.split(r"\s+#", raw, maxsplit=1)[0]
sys.stdout.write(value)
endef
export DOTENV_GET_PY

NEO4J_ENV = export NEO4J_PASSWORD="$$(python3 -c "$$DOTENV_GET_PY" $(BE_ENV) NEO4J_PASSWORD EA_NEO4J_PASSWORD)";

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

# Comme pour Neo4j : pas de valeur par défaut, l'instance est partagée. Le
# shell de la recette la lit — POSTGRES_PASSWORD ou EA_POSTGRES_PASSWORD dans
# l'environnement, sinon backend/.env — et l'exporte sous le nom que psql,
# pg_dump et pg_restore lisent eux-mêmes.
POSTGRES_ENV = export PGPASSWORD="$$(python3 -c "$$DOTENV_GET_PY" $(BE_ENV) POSTGRES_PASSWORD EA_POSTGRES_PASSWORD)";

# Sauvegardes de la base du cluster (docs/adr/0025). Même règle que psql : les
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
# mot de passe est celui que docker-compose.yml porte déjà en clair.
POSTGRES_TEST_PASSWORD ?= developmentonly
POSTGRES_TEST_PORT     ?= 5432

# Le Neo4j jetable de docker-compose.yml, celui que les tests d'intégration
# vident entre chaque cas — jamais le graphe du cluster (docs/adr/0024). Publié
# sur 127.0.0.1 seulement, et sur un autre port que le 7687 du cluster. Le mot
# de passe est jetable, comme celui du PostgreSQL de test.
NEO4J_TEST_BOLT_PORT ?= 7688
NEO4J_TEST_PASSWORD  ?= developmentonly
NEO4J_TEST_URI       ?= bolt://127.0.0.1:$(NEO4J_TEST_BOLT_PORT)

# docker compose lit ports et mots de passe dans son environnement : on les lui
# passe explicitement, depuis les mêmes variables que les tests, pour que le
# conteneur et la suite ne puissent pas viser deux ports différents.
COMPOSE_TEST := POSTGRES_TEST_PORT=$(POSTGRES_TEST_PORT) \
	POSTGRES_TEST_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
	NEO4J_TEST_BOLT_PORT=$(NEO4J_TEST_BOLT_PORT) \
	NEO4J_TEST_PASSWORD='$(NEO4J_TEST_PASSWORD)' \
	docker compose

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
        test test-unit test-integration test-postgres test-fe lint typecheck check \
        lint-check lint-fe typecheck-be typecheck-fe audit hooks \
        pg-backup pg-restore db-backup-howto \
        db-test-up

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
# `db-ping`, `db-shell` et `db-reset` réclameront EA_NEO4J_PASSWORD.
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
	@$(NEO4J_ENV) test -n "$$NEO4J_PASSWORD" || { \
		printf "$(RED)NEO4J_PASSWORD est vide.$(NC)\n"; \
		printf "Renseigne EA_NEO4J_PASSWORD dans $(BACKEND)/.env, ou lance :\n"; \
		printf "  NEO4J_PASSWORD='...' make $(MAKECMDGOALS)\n"; exit 1; }

db-stack: ## Rappelle comment déployer le graphe sur le cluster Docker
	@printf "$(GREEN)Stack Neo4j : deploy/neo4j.stack.yml$(NC)\n"
	@printf "  1. Ouvre $(PORTAINER_STACKS)\n"
	@printf "  2. Add stack → Web editor → colle deploy/neo4j.stack.yml\n"
	@printf "  3. Environment variables → NEO4J_PASSWORD = <mot de passe>\n"
	@printf "  4. Deploy the stack, puis : make db-ping\n"

db-ping: | require-neo4j-password ## Vérifie que le graphe du cluster répond
	@printf "$(GREEN)Interrogation de $(NEO4J_URI) ...$(NC)\n"
	@$(NEO4J_ENV) NEO4J_USERNAME=neo4j docker run --rm \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI) --format plain \
		'MATCH (n:Element) RETURN count(n) AS elements' \
	|| { printf "$(RED)Aucune réponse. Vérifie la stack : make db-stack$(NC)\n"; exit 1; }
	@printf "Navigateur : $(NEO4J_BROWSER)\n"

db-shell: | require-neo4j-password ## Ouvre un cypher-shell sur le graphe du cluster
	@$(NEO4J_ENV) NEO4J_USERNAME=neo4j docker run --rm -it \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI)

db-reset: | require-neo4j-password ## Vide le graphe du cluster (CONFIRM=yes obligatoire)
	@test "$(CONFIRM)" = "yes" || { \
		printf "$(RED)Cette commande efface le graphe PARTAGÉ : $(NEO4J_URI)$(NC)\n"; \
		printf "$(RED)Tout le monde le perd, il n'y a qu'une instance.$(NC)\n"; \
		printf "Relance avec : make db-reset CONFIRM=yes\n"; exit 1; }
	@$(NEO4J_ENV) NEO4J_USERNAME=neo4j docker run --rm \
		-e NEO4J_USERNAME -e NEO4J_PASSWORD $(NEO4J_IMAGE) \
		cypher-shell -a $(NEO4J_URI) 'MATCH (n) DETACH DELETE n'
	@printf "$(GREEN)Graphe vidé.$(NC)\n"

# Neo4j Community n'a pas de sauvegarde à chaud : `neo4j-admin database dump`
# exige le serveur arrêté, et se lance sur l'hôte, contre le volume. Rien ici ne
# peut le faire depuis un poste, donc la cible *rappelle* la procédure, comme
# `db-stack` rappelle le déploiement — et son nom le dit, pour que personne ne
# la mette dans une crontab en croyant sauvegarder. Voir docs/adr/0025.
db-backup-howto: ## Rappelle comment sauvegarder et restaurer le graphe (hors ligne, sur l'hôte)
	@printf "$(GREEN)Sauvegarde du graphe : hors ligne, sur l'hôte $(NEO4J_HOST) — docs/adr/0025$(NC)\n"
	@printf '%s\n' \
		"Rien ne s'exécute d'ici. Sur l'hôte (SSH, ou console Portainer) :" \
		"" \
		"  img=\$$(docker inspect -f '{{.Config.Image}}' ea-neo4j)" \
		"  dir=/srv/backups/ea-neo4j/\$$(date -u +%Y%m%dT%H%M%SZ)" \
		"  mkdir -p \"\$$dir\" && chown 7474:7474 \"\$$dir\"" \
		"  docker stop ea-neo4j" \
		"  docker run --rm --volumes-from ea-neo4j -v \"\$$dir\":/backups \"\$$img\" \\" \
		"    neo4j-admin database dump neo4j --to-path=/backups" \
		"  docker start ea-neo4j" \
		"" \
		"Puis, depuis un poste : make db-ping"
	@printf "$(RED)Copie hors de l'hôte OBLIGATOIRE : un dump sur le disque du volume ne survit pas au disque.$(NC)\n"
	@printf '%s\n' \
		"  scp -r <hôte>:\"\$$dir\" <destination hors de $(NEO4J_HOST)>" \
		"" \
		"Restauration — REMPLACE le graphe partagé :" \
		"  docker stop ea-neo4j" \
		"  docker run --rm --volumes-from ea-neo4j -v <dossier du dump>:/backups \"\$$img\" \\" \
		"    neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true" \
		"  docker start ea-neo4j && make db-ping" \
		"" \
		"Avec PostgreSQL : même fenêtre, sans écriture entre les deux, graphe d'abord (make pg-backup)."

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
	@$(POSTGRES_ENV) test -n "$$PGPASSWORD" || { \
		printf "$(RED)POSTGRES_PASSWORD est vide.$(NC)\n"; \
		printf "Renseigne EA_POSTGRES_PASSWORD dans $(BACKEND)/.env, ou lance :\n"; \
		printf "  POSTGRES_PASSWORD='...' make $(MAKECMDGOALS)\n"; exit 1; }

pg-ping: | require-postgres-password ## Vérifie que la base du cluster répond
	@printf "$(GREEN)Interrogation de $(POSTGRES_USER)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB) ...$(NC)\n"
	@$(POSTGRES_ENV) $(PSQL) $(PSQL_ARGS) -c 'SELECT version()' \
	|| { printf "$(RED)Aucune réponse. Vérifie l'hôte et le mot de passe.$(NC)\n"; \
	     $(PG_UNREACHABLE_HINT) exit 1; }

pg-shell: | require-postgres-password ## Ouvre un psql sur la base du cluster
	@$(POSTGRES_ENV) $(PSQL_TTY) $(PSQL_ARGS)

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
	@$(POSTGRES_ENV) out=$$($(PSQL) $(PSQL_ARGS) \
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

# `pg_dump` lit la base en ligne, dans un instantané cohérent : rien à arrêter.
# Le dump est écrit dans un `.partial` et ne prend son nom qu'une fois pg_dump
# réussi et sa table des matières relue — un fichier tronqué ne porte jamais le
# nom d'une sauvegarde. `backups/` est ignoré par git et fermé aux autres
# comptes : un dump contient chaque document attaché au modèle. Voir
# docs/adr/0025.
pg-backup: | require-postgres-password ## Sauvegarde la base du cluster dans backups/ (pg_dump -Fc)
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
	@printf "Ce poste n'est pas un lieu de conservation : copie le fichier là où vont les dumps du graphe.\n"
	@printf "Le graphe se sauvegarde à part, sur l'hôte : make db-backup-howto\n"

# Remplace le contenu de la base PARTAGÉE. Une seule transaction : une erreur au
# milieu annule tout, la base est restaurée ou inchangée, jamais à moitié.
# FILE et CONFIRM sont lus par le shell, pas recollés dans la recette : un nom
# de fichier n'a pas à survivre à l'analyse de make.
pg-restore: | require-postgres-password ## Restaure un dump dans la base du cluster (FILE=... CONFIRM=yes)
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
	@printf "L'index des passages est revenu avec les documents. Si le dump précède head : make pg-migrate\n"

pg-up: ## Démarre le PostgreSQL jetable local (pour les tests)
	$(COMPOSE_TEST) up -d --wait postgres

# Le pendant de `pg-up` pour le graphe : un Neo4j local, publié sur 127.0.0.1,
# que les tests vident entre chaque cas. Ce n'est pas un graphe où modéliser —
# celui-là reste sur le cluster (docs/adr/0006, 0024).
db-test-up: ## Démarre le Neo4j jetable local (pour les tests d'intégration)
	$(COMPOSE_TEST) up -d --wait neo4j

pg-down: ## Arrête les bases jetables locales, PostgreSQL et Neo4j (seul PostgreSQL garde un volume)
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

# Contre les deux conteneurs jetables, jamais contre le cluster : ces tests
# vident le graphe entre chaque cas et annulent la chaîne de migrations. Les
# fixtures refusent de toute façon un hôte qui n'est pas local (docs/adr/0024) ;
# pointer ici sur 127.0.0.1 est ce qui les fait tourner plutôt que sauter.
# Les embeddings sont coupés : le démarrage de l'application irait sinon
# interroger LM Studio sur le cluster, et la suite n'en dépend pas.
test-integration: | $(VENV_STAMP) ## Tests contre un Neo4j et un PostgreSQL jetables locaux (les démarre au besoin)
	$(COMPOSE_TEST) up -d --wait neo4j postgres
	cd $(BACKEND) && EA_ALLOW_DESTRUCTIVE_TESTS=1 EA_DEBUG=true \
		EA_NEO4J_URI=$(NEO4J_TEST_URI) \
		EA_NEO4J_PASSWORD='$(NEO4J_TEST_PASSWORD)' \
		EA_POSTGRES_ENABLED=true EA_POSTGRES_HOST=127.0.0.1 \
		EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		EA_EMBEDDINGS_ENABLED=false \
		uv run pytest tests/integration -q

# Explicitement contre le conteneur jetable, jamais contre le cluster : ces
# tests appliquent puis annulent la chaîne de migrations. Le port est celui que
# `COMPOSE_TEST` publie, pas une seconde valeur écrite en dur.
test-postgres: | $(VENV_STAMP) ## Tests contre le PostgreSQL jetable local (le démarre au besoin)
	$(COMPOSE_TEST) up -d --wait postgres
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
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

check: lint-check lint-fe typecheck openapi-check test test-fe ## Tout ce que la CI vérifiera (ne modifie aucun fichier)

# pre-commit n'est pas une dépendance du projet : uvx le prend à la version
# épinglée ici. Ses crochets appellent `uv run` et `npm run`, donc ruff et mypy
# sont ceux de uv.lock. `hooks` écrit dans .git/hooks : un choix de chaque
# poste, que rien d'autre ne fait à sa place. Voir .pre-commit-config.yaml.
PRE_COMMIT ?= uvx pre-commit@4.6.2

hooks: ## Installe les crochets pre-commit dans .git (ruff, mypy, vue-tsc, eslint, gitleaks)
	$(PRE_COMMIT) install
	@printf "$(GREEN)Crochets installés. Sur tout le dépôt : $(PRE_COMMIT) run --all-files$(NC)\n"

## --- Nettoyage ------------------------------------------------------------

clean: ## Supprime venv, node_modules, caches et artefacts de build
	rm -rf $(VENV)
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(BACKEND)/.coverage $(BACKEND)/htmlcov
	find $(BACKEND) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@printf "$(GREEN)Cleaned up!$(NC)\n"
