---
titre: Un magasin périphérique injoignable dégrade sa section, il n'empêche pas de démarrer
date: 2026-09-16
statut: Proposition
affects: backend/src/ea/main.py, backend/src/ea/api/health.py, frontend/src/lib/health.ts, frontend/src/components/BackendStatus.vue, CLAUDE.md
---

# 37. Un magasin périphérique injoignable dégrade sa section, il n'empêche pas de démarrer

Date : 2026-09-16
Statut : Proposition

## Contexte

Le déploiement a répondu `502 Bad Gateway` sur **toutes** les routes à la fois
— `/api/health`, `/api/me`, `/api/elements`, `/api/metamodel` — juste après la
mise en service de `docs/adr/0036`. Ce n'était pas un défaut de routage : le
`502` du NGINX de l'Infra dit que `ea-api` ne répond pas, et `ea-api` ne
répondait pas parce que le processus n'était pas là.

Le `lifespan` de `main.py` sondait quatre dépendances, et **chacune des quatre
arrêtait le processus** : Keycloak (`verifier.probe()`), PostgreSQL
(`check_relational_store`), le service d'embeddings (`embedder.probe()`) et,
depuis `0036`, le bucket MinIO (`minio.probe()`). Or `0036` lui-même liste,
sous « À faire hors de ce repo », la création de l'utilisateur MinIO `ea-api`
et de sa politique — un travail encore à faire —, pendant que
`deploy/ea.stack.yml` livre `EA_S3_ENABLED: "true"`. Le catalogue, le
métamodèle, l'adressage IP et les diagrammes sont donc devenus injoignables
parce qu'un **navigateur de fichiers** n'atteignait pas son bucket.

Deux choses rendent cela particulièrement coûteux. La première est que
`0036` décide exactement le contraire pour le cas jumeau : « un déploiement
sans MinIO configuré démarre quand même ; les routes `/files` et les quatre
outils MCP restent déclarés et répondent 503 ». *Pas configuré* dégradait,
*injoignable* tuait — deux issues opposées pour une même absence.
`DocumentService` porte la même asymétrie : il est écrit pour vivre sans
indexeur (`_passages_of`, `SearchUnavailableError`), et pourtant un LM Studio
éteint sur 192.168.2.10 — une application de bureau, sur une machine que
personne ne surveille — emportait tout le reste.

La seconde est que **rien ne disait pourquoi**. Un `502` ne porte aucun corps :
`/health` aurait été l'endroit où lire la cause, et `/health` était précisément
ce qui ne répondait pas. Le diagnostic n'existait que dans les logs du
conteneur, derrière `make app-logs s=api`, c'est-à-dire derrière l'accès à
Portainer.

## Décision

**Est fatale au démarrage la dépendance dont *toute* route a besoin ; est
dégradable celle qui ne sert qu'une section.**

| Dépendance | Au démarrage | Sans elle |
|---|---|---|
| PostgreSQL | fatale | il n'y a pas de modèle du tout (`docs/adr/0033`) |
| Keycloak | fatale | aucune route sauf `/health` ne peut vérifier un appel (`docs/adr/0032`) |
| Service d'embeddings | **dégradée** | `search_documents` répond 503 ; un document reste stocké, `make docs-reindex` rattrape l'index |
| Bucket MinIO | **dégradée** | `/files` et les quatre outils MCP répondent 503, comme avec `EA_S3_ENABLED=false` |

Les deux sondes dégradables passent par `_reachable(nom, probe, degraded)`
dans `main.py`. Son `except` est aveugle, pour la raison qui le rend aveugle
dans `check_connectivity` : aucun échec d'une sonde ne veut dire autre chose
que « ce magasin n'est pas utilisable ». L'exception part au log avec
`action=subsystem_degraded`, là où l'hôte, le bucket et le modèle ont le droit
d'être nommés ; ce qui remonte à l'appelant est le nom de la section, et rien
d'autre.

**`app.state.degraded` est publié une fois les sondes passées, et `/health` le
lit.** `status` vaut `ok` ou `degraded`, et `degraded` nomme les sections qui
vont refuser (`files`, `search`). Deux propriétés portent la décision :

- **`/health` reste un 200 dans les deux cas.** C'est la cible du `HEALTHCHECK`
  de `backend/Dockerfile` et le premier appel du SPA ; un 503 marquerait
  « malsain » un conteneur qui sert le catalogue parfaitement. La vivacité
  n'est pas la disponibilité fonctionnelle.
- **`/health` nomme des sections, jamais des raisons.** La route ne porte pas
  `Authenticated` — c'est la seule —, donc son corps est lisible sans jeton.
  `files` et `search` disent quelle partie de l'application refuse, sans dire
  quel hôte, quel bucket ni quel modèle est en cause : la règle de `CLAUDE.md`
  sur les erreurs typées et génériques, appliquée à un état plutôt qu'à une
  erreur.

Le SPA suit : `lib/health.ts` rend la réponse entière au lieu du seul `status`,
et `BackendStatus.vue` nomme les sections indisponibles en français. La table
de traduction y est une table de **libellés**, pas de règles — une section sans
entrée est affichée telle quelle, parce qu'une section que personne n'a encore
traduite est quand même une section en panne.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Laisser les quatre sondes fatales et compter sur `restart: unless-stopped` | Un démarrage réussi prouve que tout est là ; une panne transitoire finit par se résorber | Une panne *durable* — un utilisateur MinIO jamais créé — est une boucle de redémarrage infinie et un 502 total, sans aucun diagnostic lisible | Écarté : c'est l'état constaté |
| Mettre `EA_S3_ENABLED=false` dans la stack en attendant l'utilisateur MinIO | Une ligne, et le 502 disparaît | Ne soigne que ce symptôme-ci : l'embedder garde exactement le même pouvoir de tout arrêter, et la section fichiers reste éteinte après la création de l'utilisateur tant que personne ne rebascule la variable | Écarté : traite l'incident, pas la règle |
| Rendre aussi PostgreSQL et Keycloak dégradables | `/health` répondrait toujours, quelle que soit la panne | Un catalogue qui démarre sans son modèle sert des listes vides ; un vérificateur sans clés répond 500 sur toutes les routes. Démarrer n'apporte rien qu'un 502 remplacé par un 500 partout | Écarté : ces deux-là ne sont pas une section |
| Sonder en arrière-plan et rebrancher la section quand le magasin revient | Un MinIO relancé se reprendrait sans redémarrer le conteneur | Une tâche de fond, sa période, son arrêt propre et son interaction avec `AsyncExitStack` — pour un gain qu'un `docker restart` obtient déjà | Écarté pour l'instant ; à reconsidérer si la panne devient fréquente |
| Séparer `/health` (vivacité) et `/ready` (disponibilité) | La convention Kubernetes, que tout le monde lit | Rien ici ne consomme une sonde de disponibilité : ni le `HEALTHCHECK` du `Dockerfile`, ni le NGINX de l'Infra, qui n'a pas de bloc `upstream` avec contrôle de santé | Écarté : une seconde route que personne n'appelle |

## Conséquences

**Un démarrage réussi ne prouve plus que tout est branché.** C'est le coût
central, et il est assumé : la contrepartie est qu'un magasin manquant se lit
sur `/health` et dans le bandeau du SPA, au lieu de ne se lire nulle part. Le
log de démarrage porte `degraded` sur sa dernière ligne, et chaque sonde en
échec écrit une ligne `action=subsystem_degraded` avec sa trace.

**Une section dégradée le reste jusqu'au redémarrage du conteneur.** Rien ne
re-sonde : une fois l'utilisateur MinIO créé côté Infra, il faut `make app-up`
(ou un redémarrage du service `api`) pour que la section fichiers revienne.
C'est explicitement le compromis retenu contre une tâche de fond.

**L'index peut prendre du retard sans que personne l'ait choisi.** Un document
attaché pendant que l'embedder était injoignable n'est pas indexé, exactement
comme s'il avait été attaché avec `EA_EMBEDDINGS_ENABLED=false`.
`make docs-reindex` est le rattrapage des deux cas, et c'était déjà écrit ainsi
dans `docs/adr/0019` ; ce qui change est qu'une panne, et non plus seulement un
réglage, peut y mener. `/health` disant `search` est ce qui permet de savoir
qu'il faut le lancer.

**À surveiller** : le jour où une troisième dépendance périphérique arrive, elle
choisit son camp dans le tableau ci-dessus et ajoute une constante à côté de
`SEARCH` et `FILES` dans `main.py`. Une dépendance dont *toute* route a besoin
n'y entre pas.

**Reste à faire, hors de ce dépôt** : l'utilisateur MinIO `ea-api` et sa
politique, point 1 de `docs/adr/0036`. Cette décision fait que son absence
coûte la section fichiers au lieu de tout le déploiement ; elle ne la crée pas.

## Références

- ADR liés : [0019](0019-recherche-semantique-sur-les-documents.md),
  [0032](0032-authentification-par-keycloak.md),
  [0033](0033-postgresql-seul-pour-le-graphe.md),
  [0036](0036-fichiers-dans-minio.md)
- Code concerné : `backend/src/ea/main.py`, `backend/src/ea/api/health.py`,
  `backend/tests/unit/test_boot_degradation.py`,
  `frontend/src/lib/health.ts`, `frontend/src/components/BackendStatus.vue`
