---
titre: Le port d'Ollama est 11434, et le suffixe /v1 fait partie de l'adresse
date: 2026-09-17
statut: Acceptée
affects: backend/src/ea/core/config.py, backend/.env.example, deploy/ea.stack.yml, deploy/ea.env.example, Makefile, backend/tests/unit/test_config.py, backend/tests/unit/test_deploy_stack.py, backend/tests/unit/test_boot_degradation.py, backend/tests/unit/test_makefile_env.py, scripts/portainer-stack.sh, CLAUDE.md
---

# 40. Le port d'Ollama est 11434, et le suffixe /v1 fait partie de l'adresse

Date : 2026-09-17
Statut : Acceptée

Amende [`0038`](0038-ollama-plutot-que-lm-studio-pour-les-embeddings.md) sur le
seul numéro de port. Le fournisseur, le modèle, la largeur de la colonne et la
règle « chaque vecteur porte le nom du modèle » sont inchangés.

## Contexte

Le déploiement a écrit, au démarrage, la même ligne qu'en `0038` à un port
près :

```
search is degraded: its boot probe failed, so the section it serves will refuse
ea.repositories.embeddings.EmbeddingServiceError:
  the embedding service at http://192.168.2.10:11435/v1/embeddings is unreachable
```

`0038` avait corrigé la machine — le cluster, plus le Mac — et le fournisseur —
Ollama, plus LM Studio. Il a écrit `11435` sans jamais l'avoir sondé. Le port
d'Ollama est **11434**, celui qu'il écoute par défaut :

```console
$ curl -sf --max-time 5 http://192.168.2.10:11435/v1/models   # rien
$ curl -sf --max-time 5 http://192.168.2.10:11434/v1/models
{"object":"list","data":[{"id":"nemotron-3-nano:4b",...},
                         {"id":"mxbai-embed-large:latest",...}]}
```

**Ce qui rend cet ADR nécessaire est que `0038` a réussi.** Tant que le défaut
du dépôt était faux *et connu comme faux*, chaque machine portait son
`EA_EMBEDDINGS_BASE_URL` dans son `deploy/ea.env` et touchait, tant bien que
mal, un service qui répondait. `0038` a rendu le défaut de la stack crédible et
`make app-up` s'est mis à nommer toute ligne du fichier qui le contredit : la
surcharge a été retirée, le défaut a pris — et tous les conteneurs se sont mis
à sonder un port fermé, ensemble. Un défaut faux est plus dangereux une fois
qu'on a cessé de s'en méfier.

Le second défaut est passé inaperçu derrière le premier. `backend/.env`, sur le
Mac, portait le bon port et **pas le suffixe `/v1`** :

```
EA_EMBEDDINGS_BASE_URL=http://192.168.2.10:11434
```

`HttpEmbedder` construit son URL par `base_url.rstrip("/") + "/embeddings"` et
rien d'autre. Cette ligne visait donc `…:11434/embeddings`, qu'Ollama ne sert
pas : un `404`, pas un `ConnectError`. Les deux chemins — le conteneur et le
poste de développement — étaient cassés depuis `0038`, de deux façons
différentes, et aucun n'a jamais indexé un passage. Le nom du modèle y était
resté celui de LM Studio par-dessus le marché.

Rien de tout cela n'est un problème de code.

## Décision

**L'adresse par défaut du service d'embeddings est
`http://192.168.2.10:11434/v1`.** C'est une valeur par défaut, dans
`config.py`, la stack, les deux `.env.example` et le `Makefile`, et rien
d'autre.

**Le suffixe `/v1` fait partie de l'adresse, et c'est écrit là où l'adresse
s'écrit.** Le client n'ajoute que `/embeddings` : une base sans `/v1` est un
404 silencieux, c'est-à-dire une section dégradée pour une raison qui ne
ressemble pas à la cause. Le commentaire qui précède chaque occurrence le dit
maintenant, parce que c'est exactement l'erreur qui a été commise.

**Le modèle ne change pas**, donc **il n'y a pas de réindexation à faire de ce
fait** — `mxbai-embed-large`, 1024 dimensions, le préfixe de requête de la
famille `mxbai`. La réindexation que `0038` annonçait reste due : elle n'a
jamais pu tourner, l'embedder n'ayant jamais répondu depuis.

## Conséquences

- Le rattrapage est celui de `0038`, dans le même ordre : `make app-up` (rien
  ne re-sonde, `docs/adr/0037`), puis `make docs-reindex`.
- `make embed-ping` et `make embed-models` interrogent 11434. Ils continuent de
  nommer `backend/.env` quand c'est lui qui a parlé — la règle de `0038`, qui
  aurait fait gagner du temps ici si le fichier avait été relu.
- `test_makefile_env.py` ne se sert plus de `11435` comme « adresse qui n'est
  pas le défaut » : la valeur du fixture est une machine visiblement inventée
  (`192.168.2.99`). Un port qui a été un vrai défaut faux n'est pas un bon
  exemple d'autre chose.
- Un futur changement de fournisseur reste une base et un nom de modèle. Ce
  que cet ADR ajoute au mode d'emploi de `0038`, c'est de **sonder l'adresse
  avant de l'écrire** : `curl -sf $URL/models` est une ligne, et c'est la seule
  chose qui séparait `0038` d'être juste.

## Alternatives écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Corriger `0038` sur place — il n'était qu'au statut *Proposition* | Un ADR de moins ; on peut plaider la coquille | La coquille a coûté deux déploiements dégradés, dont le second *parce que* le dépôt avait été corrigé. C'est un enchaînement, pas une faute de frappe, et c'est ce que l'historique doit garder | Écarté |
| Ne corriger que `deploy/ea.env` et `backend/.env` | Deux lignes, tout de suite | Exactement l'option que `0038` a écartée, pour la raison qu'il a écrite — et le port serait alors, de nouveau, juste dans les seuls fichiers non versionnés | Écarté |
| Faire tolérer au client une base sans `/v1` (ajouter le segment s'il manque) | Un `.env` incomplet fonctionnerait quand même | Le client devinerait la forme de l'API à partir de l'adresse, ce que `0019` a précisément refusé : c'est la base qui décrit le fournisseur. Et une base qui pointe déjà `/v1/embeddings` deviendrait ambiguë | Écarté |
| Publier 11435 sur le cluster pour que le dépôt ait raison | Aucun fichier à changer ici | Déplacer un service pour épargner un ADR ; et 11434 est le port que tout le reste de l'écosystème Ollama suppose | Écarté |
