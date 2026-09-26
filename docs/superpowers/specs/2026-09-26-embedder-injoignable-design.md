# Un magasin dégradé au démarrage se rétablit seul

- **Date** : 2026-09-26
- **Statut** : à implémenter
- **ADR à écrire** : `docs/adr/0041-un-magasin-degrade-se-retablit-seul.md` (complète `0037`)

## Le symptôme

Au démarrage de la stack déployée, 2026-09-26 08:32 UTC :

```
search is degraded: its boot probe failed, so the section it serves will refuse
EmbeddingServiceError: the embedding service at http://192.168.2.10:11434/v1/embeddings is unreachable
  ← httpx.ConnectError: All connection attempts failed
```

`/health` répond `{"status": "degraded", "degraded": ["search"]}` ;
`search_documents` refuse, et les documents déposés depuis ne sont pas indexés.

## Ce n'est pas le port

L'adresse est la bonne : `11434` est le défaut du dépôt depuis
[`0040`](../../adr/0040-le-port-d-ollama-est-11434.md) (#73), et, sondée
depuis le Mac le 2026-09-26, elle répond — Ollama 0.34.1,
`mxbai-embed-large`, 1024 dimensions. L'embedder était donc **momentanément**
injoignable à 08:32 (Ollama, le cluster ou Docker Desktop en train de
redémarrer — le démon du Mac était arrêté au moment de l'enquête).

Une coupure de quelques secondes suffit pourtant à couper la recherche pour
de bon, et c'est là le défaut :

## La cause : une dégradation est définitive

`_reachable` (`main.py`) sonde **une fois**, au démarrage. Si la sonde échoue,
`DocumentService` est construit avec `indexer=None` et `app.state.degraded` est
figé en tuple. La recherche reste coupée **jusqu'au prochain redémarrage du
conteneur**, même quand l'embedder revient une minute plus tard — et le bucket
MinIO (`files`) a exactement le même défaut. L'ADR 0037 a eu raison de ne plus
bloquer le démarrage ; elle n'a pas prévu le retour. Le commentaire de
`deploy/ea.stack.yml` le dit déjà : « Rien ne re-sonde : remettre Ollama en
route demande un redémarrage de `api` ».

## Comportement voulu

- Au démarrage : rien ne change — la sonde échoue, la section est dégradée,
  l'API démarre (0037).
- Ensuite, **une tâche de fond** relance la sonde de chaque magasin dégradé,
  avec un délai croissant plafonné (30 s, 60 s, … 5 min).
- Dès qu'une sonde passe : la section est rebranchée
  (`DocumentService` reçoit son `DocumentIndexer`, `FileService` son
  `ObjectStore`), le nom sort de `app.state.degraded`, `/health` repasse à
  `ok`, et un log `action=subsystem_recovered` le dit.
- À l'arrêt, la tâche est annulée par l'`AsyncExitStack` du lifespan.
- Un magasin sain au démarrage n'est **pas** surveillé : l'appel qui échoue
  plus tard répond déjà 503 lui-même.

## Écarté

- **Sonder à la demande** (au premier appel de recherche) : `/health` resterait
  `degraded` tant que personne ne cherche, et c'est lui que l'opérateur lit.
- **Redémarrer le conteneur** (healthcheck en échec) : 0037 a choisi que
  `/health` reste 200 ; un redémarrage coupe aussi le catalogue, qui allait bien.
- **Retry bloquant au démarrage** : c'est revenir sur 0037.

## Découpage

- `main.py` : `_reachable` garde sa forme ; une fonction `_recover(name, probe,
  attach, degraded, sleep)` boucle jusqu'au succès puis appelle `attach()`.
  `sleep` est injecté (règle *no `time.sleep` — inject a clock*).
- `DocumentService` et `FileService` : une méthode pour recevoir leur
  indexeur / magasin après construction — la seule surface nouvelle.
- `app.state.degraded` devient un ensemble muté par la tâche ; `health.py`
  le lit tel quel, sans changement de schéma — **l'OpenAPI ne bouge pas**.
- Le pool HTTP de l'embedder est déjà ouvert et fermé par le lifespan : la
  tâche réutilise le même client, elle n'en ouvre pas.
- Le commentaire de `deploy/ea.stack.yml` (« Rien ne re-sonde ») est corrigé.

## Tests (écrits d'abord)

Unitaires, avec une sonde double et un `sleep` qui ne dort pas :

1. une sonde qui échoue deux fois puis passe → la section est rebranchée, le
   nom sort de `degraded`, le délai a crû entre les essais ;
2. un magasin sain au démarrage → aucune tâche n'est lancée ;
3. l'arrêt du lifespan pendant l'attente → la tâche est annulée, rien ne fuit.

API : `/health` passe de `degraded` à `ok` après rétablissement de la sonde
double ; une recherche refusée avant répond après.

## Hors périmètre

- **Les documents déposés pendant la dégradation** restent sans passages : le
  rétablissement ne réindexe pas. Le log `subsystem_recovered` de `search`
  rappelle `make docs-reindex`. Réindexer automatiquement est possible plus
  tard si cela se reproduit.
- Surveiller un magasin sain (voir plus haut).

## En attendant

`make app-restart` (#72) relance la stack, puis `make docs-reindex`.
