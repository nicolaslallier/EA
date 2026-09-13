---
titre: Pipelines Python dans le dépôt EA — Prefect 3 et LiteLLM, auto-hébergés
date: 2026-09-13
statut: Proposition
affects: pipelines/, Makefile, .github/workflows/ci.yml, .github/dependabot.yml, .pre-commit-config.yaml
---

# 28. Pipelines Python dans le dépôt EA

Date : 2026-09-13
Statut : Proposition

## Contexte

La note Obsidian `Docs/Guides/STACK_PIPELINES_PYTHON` propose une stack pour industrialiser des
processus en code Python — plutôt qu'un outil visuel type n8n — auto-hébergée et sans coût de
licence : Prefect 3 comme orchestrateur, LiteLLM comme passerelle unique devant les fournisseurs de
modèles. Le premier processus visé, décidé dans `Docs/Guides/PLAN_PIPELINES_EA`, est « alimenter le
catalogue EA » : lire une source (un document déposé dans MinIO), en extraire des éléments et des
relations ArchiMate par un appel LLM structuré, et les écrire dans le catalogue partagé par l'API EA.

Trois ressources existent déjà sur le cluster Docker (192.168.1.252) et n'ont pas à être
redéployées : le PostgreSQL de `docs/adr/0015` (deux bases de plus, `prefect` et `litellm`), le LM
Studio de `docs/adr/0019` (un modèle de chat, pas seulement l'embedding), et le MinIO de la stack
`~/OpenCode/Infra`. Rien de tout cela n'est le graphe : le pipeline n'est pas un troisième adaptateur
de `backend/`, c'est un client de son API, exactement comme le SPA.

## Décision

**`pipelines/` est un second projet Python dans ce dépôt, avec son propre lockfile.** Prefect épingle
ses propres versions de FastAPI, SQLAlchemy et Alembic — les mêmes bibliothèques que `backend/`, à
des versions que ce dépôt ne contrôle pas. Un seul lockfile pour les deux forcerait l'un des deux à
suivre les contraintes de l'autre.

**Prefect 3, lancé par `flow.serve(name="manuel", limit=1)`.** Pas de work pool, pas de
`prefect.yaml` : un seul flow, déclenché à la main pendant que le processus tourne, est le juste
besoin du premier processus. Un work pool découplerait le déploiement de l'exécution pour un
avantage qu'un unique flow manuel n'a pas encore.

**LiteLLM derrière deux alias, `smart` et `fast`.** Le code ne nomme jamais un modèle réel — voir
`CLAUDE.md`, section *Sécurité* pour la règle équivalente côté secrets. `pipelines/litellm.yaml`
décide seul quel modèle répond à chaque alias. Il laisse `drop_params` à `false` : un
`response_format` retiré en silence pour un modèle que LiteLLM ne connaît pas rendrait du texte libre
là où le pipeline exige un schéma strict.

**MinIO de la stack Infra, en lecture seule depuis ce projet.** Le pipeline lit une source, il n'en
est pas le gestionnaire de cycle de vie.

**Écriture dans EA uniquement par l'API REST, jamais par MCP, jamais par un repository.** `/mcp`
refuse tout pair non-loopback (`docs/adr/0023`) ; le conteneur du pipeline n'est pas ce pair. Un
repository partagerait la couche que `services/` possède déjà et rendrait le pipeline capable
d'écrire un graphe que l'API elle-même refuserait.

**Le métamodèle est demandé à l'exécution, jamais restaté.** Le pipeline appelle `GET /metamodel`
pour connaître les types d'éléments et de relations qu'il peut proposer au modèle, exactement comme
le SPA (`CLAUDE.md`, section *La SPA ne détient aucune copie du métamodèle*). Il n'en lit pas les
règles : c'est `POST /relationships` qui refuse une relation que le métamodèle interdit, et le
pipeline enregistre ce refus.

**Dédoublonnage des éléments par la contrainte `element_name_unique_per_type` ; des relations par
recherche.** Le catalogue est partagé et déjà peuplé : réécrire un élément existant serait une
collision silencieuse. Le pipeline recherche une relation déjà là avant d'en créer une, faute d'une
contrainte équivalente côté relations.

**On complète, on n'écrase pas.** Un modèle qui a mal lu la source ne doit pas remplacer une valeur
correcte posée par un humain. Sur un élément déjà là, le pipeline ne remplit qu'une `description`
vide, et ne touche jamais `properties` : `PATCH /elements/{id}` remplace cette table en entier.
`ingested_from`, la source, n'est donc posée qu'à la création d'un élément. Une extraction qui
contredit l'existant ne change rien, et une description complétée ne laisse aucune trace dans le
catalogue : seul l'artifact Prefect de l'exécution l'enregistre.

**Une couche de réessai par type d'échec.** LiteLLM réessaie l'appel au fournisseur
(`num_retries: 2`) ; l'appel LLM côté pipeline est un unique `POST httpx`, sans réessai de sa part ;
les tâches qui écrivent dans EA sont `@task(retries=2, retry_delay_seconds=[2, 10])`, pour l'aléa
réseau vers le Mac ; une sortie LLM mal formée n'est **pas** réessayée — réessayer une hallucination
ne la corrige pas.

**Deux bases (`prefect`, `litellm`) sur le PostgreSQL partagé, créées à la main.** Pas de troisième
instance PostgreSQL : le cluster en a déjà une (`docs/adr/0015`), et Prefect comme LiteLLM ne
demandent qu'une base à eux, pas un moteur à eux.

**Ports publiés sur `127.0.0.1` uniquement ; images épinglées par tag *et* digest** — la même
discipline que `docs/adr/0026` pour Neo4j et PostgreSQL.

### Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| n8n, Windmill, Kestra, Dagster, Temporal, Airflow | Écosystèmes matures | Visuel d'abord, ou un modèle conceptuel (assets, exécution durable) que ce besoin ne justifie pas — voir la note Obsidian | Écarté |
| Dépôt séparé pour `pipelines/` | Cycle de vie et permissions indépendants | Un deuxième dépôt à cloner, à versionner, à lier à celui-ci pour la moindre évolution de l'API EA | Écarté |
| Workspace `uv` (un seul lockfile pour `backend/` et `pipelines/`) | Une seule commande d'installation | Prefect impose ses propres versions de FastAPI/SQLAlchemy/Alembic ; un workspace forcerait l'un des deux projets à suivre les contraintes de l'autre | Écarté |
| MCP comme voie d'écriture depuis le pipeline | Même serveur que le SPA parle déjà | `/mcp` refuse un pair non-loopback (`docs/adr/0023`) ; le contourner serait la mauvaise réponse à une défense délibérée | Écarté |
| Un second PostgreSQL dédié aux pipelines | Isolation totale | Une troisième base à sauvegarder et surveiller pour deux schémas que Prefect et LiteLLM gèrent eux-mêmes | Écarté |
| Redis (cache, limitation de débit) | Suggéré par la note Obsidian pour la stack complète | Aucun besoin mesuré pour un flow manuel unique | Écarté |
| Ollama | Modèles locaux gratuits | LM Studio du cluster sert déjà un `/v1/chat/completions` compatible OpenAI ; un second serveur de modèles est une duplication | Écarté |
| SDK `openai` | Client officiel, complet | `httpx` suffit pour un unique `POST` vers LiteLLM ; le SDK apporte une couche de retry et de types que ce projet n'utilise pas | Écarté |
| `boto3` | Client S3 de référence | `minio` est le client officiel de MinIO, plus léger pour get/put d'objets | Écarté |
| `tenacity` | Décorateurs de réessai génériques | Prefect a déjà `retries=` sur `@task` ; une deuxième bibliothèque de réessai pour la même couche est une duplication | Écarté |
| Work pool + `prefect.yaml` | Nécessaire à terme (plusieurs flows, planification) | Un unique flow lancé à la main n'a pas encore ce besoin ; `flow.serve()` le couvre | Écarté pour l'instant |

## Conséquences

- **L'API EA doit tourner** (`make run-be`) pour que le pipeline écrive quoi que ce soit : c'est un
  client de plus, pas un adaptateur qui partage le processus.
- **Un modèle écrit dans le catalogue partagé sans relecture humaine avant écriture.** Atténué par
  trois choses : `ingested_from` trace la source de chaque élément que le pipeline crée ; l'artifact
  de table de chaque exécution Prefect enregistre ce qui a été créé, complété ou refusé — même quand
  l'exécution échoue en cours de route — et c'est le seul enregistrement d'une description
  complétée ; la règle
  « on complète, on n'écrase pas » limite les dégâts d'une mauvaise lecture à des ajouts, jamais à une
  perte.
- **Les DSN en URL, dans `pipelines/.env`, sont une exception à `CLAUDE.md`** (qui interdit un DSN en
  chaîne pour `backend/`) : Prefect et LiteLLM attendent tous deux une URL de connexion, pas les
  champs séparés que `dsn_of()` assemble côté backend. L'exception est confinée à ce fichier, jamais
  commité ; les mots de passe des deux nouvelles bases sont générés par `openssl rand -hex 32`.
- **Ces deux bases sont hors `pg-backup`** : la cible sauvegarde la base `ea`, pas `prefect` ni
  `litellm`. Une panne y perd l'historique des exécutions et le suivi des coûts LiteLLM, jamais le
  catalogue.
- **LM Studio doit avoir un modèle de *chat* chargé**, en plus du modèle d'embedding de
  `docs/adr/0019` — les deux n'ont pas vocation à être le même modèle.
- **Les appels au fournisseur `smart` (Anthropic) coûtent** : chaque exécution du flow a un prix, à
  la différence de tout ce que ce dépôt a fait tourner jusqu'ici.
- **Prefect 3.8 tire `redis` et `pydocket` comme dépendances Python transitives** (`pipelines/uv.lock`).
  Ce sont des paquets, pas une infrastructure Redis : aucun serveur Redis n'est déployé, et
  l'alternative écartée plus haut le reste.
- **Ce statut reste *Proposition* tant qu'aucun conteneur n'a tourné** : la note Obsidian source
  documente une configuration validée syntaxiquement mais jamais déployée. Le faire passer à
  *Accepté* attend une vérification de bout en bout : la stack démarrée et une exécution réelle du
  flow sur le catalogue.

## Références

- ADR liés : [0006](0006-neo4j-sur-le-cluster-docker.md),
  [0007](0007-client-openapi-genere-pour-le-spa.md),
  [0012](0012-ecran-du-metamodele.md),
  [0014](0014-serveur-mcp-pour-les-agents.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0019](0019-recherche-semantique-sur-les-documents.md),
  [0023](0023-mcp-reserve-a-la-boucle-locale.md),
  [0025](0025-sauvegardes-des-deux-bases.md),
  [0026](0026-la-barriere-qualite.md)
- Note source : `Docs/Guides/STACK_PIPELINES_PYTHON` (vault Obsidian), et le plan
  `Docs/Guides/PLAN_PIPELINES_EA` qui l'attache à ce dépôt
- Code concerné : `pipelines/`
