---
titre: L'application déployée comme stack Portainer, derrière le NGINX de l'Infra
date: 2026-09-13
statut: Proposition
affects: deploy/ea.stack.yml, backend/Dockerfile, frontend/Dockerfile, Infra nginx/conf.d/ea.conf
---

# 27. L'application déployée comme stack Portainer, derrière le NGINX de l'Infra

## Contexte

Jusqu'ici l'application ne tournait que sous `make run` : deux serveurs de
développement sur `0.0.0.0` (`0016`, `0022`), une origine par poste à ajouter à
`EA_CORS_ORIGINS`, et un SPA qui devine l'adresse de l'API. Seules les bases
étaient déployées, en stacks Portainer sur un hôte du réseau.

Le dépôt voisin `Infra` fournit désormais le socle commun des applications de
la maison, sur le Docker Desktop d'un Mac : un Portainer, un PostgreSQL 18 avec
pgvector, un certificat et une zone DNS génériques pour
`*.infra.famillelallier.net`, et **un NGINX qui est la seule entrée** — aucune
application n'y publie de port, elles rejoignent le réseau `infra-net`. Jarvis
y est déjà servi ainsi.

## Décision

**L'API et le SPA sont une stack Portainer Git, `deploy/ea.stack.yml`,
servie par le NGINX de l'Infra à `https://ea.infra.famillelallier.net`.**

| Point | Choix | Pourquoi |
|---|---|---|
| Images | `backend/Dockerfile` (uv, Python 3.12), `frontend/Dockerfile` (build Vite, puis nginx statique) | Construites par Portainer depuis le clone : une stack Git, pas un Web editor, parce qu'une image se construit depuis des fichiers. `pull_policy: build` reconstruit à chaque déploiement |
| Réseau | `infra-net`, alias `ea-api` et `ea-web`, aucun port | La règle d'entrée unique de l'Infra ; un nom de service nu (`api`) y entrerait en collision avec ceux des autres projets |
| Chemins | SPA à `/`, API sous `/api/`, préfixe retiré par le vhost | Les routes du SPA (`/elements`, `/ipam`) sont aussi des chemins de l'API. Une origine unique rend CORS sans objet et `VITE_API_BASE_URL=/api` indépendant du nom d'hôte. `UVICORN_ROOT_PATH=/api` garde `/api/docs` fonctionnel |
| MCP | `EA_MCP_ENABLED=false` dans la stack, `/api/mcp` → 404 dans le vhost | Derrière un proxy, le pair TCP est NGINX : `/mcp` n'y servirait personne, et l'ouvrir aux clients distants l'ouvrirait à tous, sans authentification (`0023`). Deux verrous plutôt qu'un |
| PostgreSQL | Celui de l'Infra, base et rôle `ea` (`make provision-app app=ea`) | Le socle existe pour ça, avec pgvector et les extensions installées par le superutilisateur |
| Migrations | `alembic upgrade head` au démarrage du conteneur | Une base neuve devient utilisable en démarrant, comme le graphe applique son schéma au démarrage (`0004`). Une migration en échec arrête le conteneur |
| Neo4j, embeddings | Hors de l'Infra, par `EA_NEO4J_URI` (obligatoire) et `EA_EMBEDDINGS_BASE_URL` (LM Studio du Mac par défaut) | L'Infra n'a pas de Neo4j, et l'adresse du graphe a déjà changé deux fois |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Une seule image, FastAPI servant aussi le SPA | Un conteneur | Du code backend pour servir des fichiers et le repli `history` ; mélange deux cycles de build | Écarté |
| Stack Web editor avec images poussées sur un registre | Pas de build dans Portainer | Un registre à tenir, et une étape de publication de plus | Écarté |
| API sur son propre sous-domaine (`ea-api.…`) | Pas de préfixe | Deux origines, donc CORS, et le SPA doit connaître un second nom | Écarté |
| `--forwarded-allow-ips` pour retrouver le vrai client | Un pair « réel » derrière le proxy | Le pair deviendrait un en-tête écrit par l'appelant, exactement ce que `0023` refuse | Écarté |
| Neo4j dans la stack | Tout au même endroit | Déplacer le graphe est un vidage hors ligne (`0025`) et une décision à part | Reporté |

## Conséquences

- **L'agent n'a pas de `/mcp` sur le déploiement.** Il garde `make run-be` en
  local. Le rouvrir attend l'auth.
- **Les documents d'avant ne sont pas dans la base de l'Infra.** Une base neuve
  y est migrée à vide ; reprendre l'existant est un `make pg-backup` sur
  l'ancienne instance puis un `pg_restore` vers `ea` (PostgreSQL 17 → 18 :
  `pg_dump` lit l'ancienne, `pg_restore` 18 écrit la nouvelle).
- **Deux dépôts bougent ensemble.** Le vhost vit dans `Infra`
  (`nginx/conf.d/ea.conf`), déployé par son `make up` depuis `main` ; la stack
  vit ici. Renommer un alias d'un côté casse l'autre en 502.
- **Le build n'est pas en CI.** `npm run build` l'est, les `docker build` non :
  un Dockerfile cassé se découvre au déploiement.
- **Dependabot suit les images de base** des deux Dockerfile, épinglées par
  tag et digest comme les stacks.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0016](0016-ecoute-sur-toutes-les-interfaces.md),
  [0023](0023-mcp-reserve-a-la-boucle-locale.md),
  [0025](0025-sauvegardes-des-deux-bases.md)
- Code concerné : `deploy/ea.stack.yml`, `backend/Dockerfile`,
  `frontend/Dockerfile`, `frontend/nginx.conf` ; dans `Infra` :
  `nginx/conf.d/ea.conf`
