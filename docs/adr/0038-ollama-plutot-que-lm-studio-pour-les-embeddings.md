---
titre: Ollama plutôt que LM Studio pour les embeddings
date: 2026-09-16
statut: Proposition
affects: backend/src/ea/core/config.py, backend/.env.example, deploy/ea.stack.yml, deploy/ea.env.example, Makefile, .github/workflows/ci.yml, CLAUDE.md
---

# 38. Ollama plutôt que LM Studio pour les embeddings

Date : 2026-09-16
Statut : Proposition

Amende [`0019`](0019-recherche-semantique-sur-les-documents.md) sur le seul
fournisseur : le découpage, la largeur de la colonne, le modèle mesuré et la
règle « chaque vecteur porte le nom du modèle » sont inchangés.

## Contexte

Le déploiement a écrit, au démarrage :

```
search is degraded: its boot probe failed, so the section it serves will refuse
ea.repositories.embeddings.EmbeddingServiceError:
  the embedding service at http://192.168.2.35:1234/v1/embeddings is unreachable
```

Deux choses s'y lisent. La première est que `docs/adr/0037` fait son travail :
l'API a démarré, `/health` est passé à `degraded` en nommant `search`, et le
catalogue, le métamodèle, l'adressage IP, les diagrammes et les fichiers ont
continué de répondre. La seconde est que **le repo décrivait une machine qui
n'est plus celle qui sert les vecteurs** : `192.168.2.10:1234`, LM Studio,
partout — `config.py`, le `Makefile`, les deux `.env.example`, la stack — alors
que le service réellement en place est un **Ollama sur `192.168.2.10:11435`**.
Le `deploy/ea.env` du Mac, lui, pointait un troisième endroit
(`192.168.2.35:1234`, le Mac lui-même), ce qui est exactement ce qui arrive à
un défaut qui n'existe que dans un fichier non versionné : on le corrige à la
main, ailleurs, et chaque machine finit avec sa propre adresse.

Rien de tout cela n'est un problème de code. `HttpEmbedder` parle la forme
OpenAI `/embeddings` et nomme déjà Ollama parmi les services qui la servent ;
`0019` avait retenu cette forme précisément pour que changer de fournisseur ne
soit pas un second client.

## Décision

**Le service d'embeddings par défaut est Ollama, sur `192.168.2.10:11435`, avec
le modèle `mxbai-embed-large`.** Ce sont deux valeurs par défaut —
`embeddings_base_url` et `embeddings_model` — et rien d'autre : aucun client,
aucune dépendance, aucune migration.

**Le modèle est le même qu'en `0019`**, sous le nom par lequel Ollama le tire.
`mxbai-embed-large` produit des vecteurs de 1024, qui est la largeur de
`document_chunks.embedding` ; le préfixe de requête reste celui de la famille
`mxbai` (une instruction sur la question, aucune sur le passage), pour la
raison que `0019` a déjà écrite.

**Le nom du modèle change, donc l'index existant est à reconstruire.** Chaque
vecteur porte le nom du modèle qui l'a produit et toute recherche filtre
dessus — c'est la règle de `0019` qui fait qu'un corpus à moitié réindexé
répond *trop peu*, visiblement, plutôt que quelque chose de plausible et faux.
Les passages indexés sous `text-embedding-mxbai-embed-large-v1` sont donc
invisibles tant que `make docs-reindex` n'a pas tourné. C'est le prix voulu :
l'alternative — garder l'ancien nom en réglage pour éviter la réindexation —
ferait mentir la colonne sur ce qui a produit le vecteur.

## Conséquences

- `make embed-ping` et `make embed-models` interrogent Ollama ; leur message
  d'échec parle de `ollama pull`, pas d'un modèle chargé dans une fenêtre.
- Le déploiement demande, dans l'ordre : corriger `EA_EMBEDDINGS_BASE_URL` dans
  `deploy/ea.env` (ou l'y supprimer, la stack ayant désormais la bonne valeur
  par défaut), `make app-up` — rien ne re-sonde, `docs/adr/0037` —, puis
  `make docs-reindex`.
- Une valeur par défaut juste est ce qui rend le fichier non versionné
  inutile : tant que le repo décrivait la mauvaise machine, chaque déploiement
  devait porter un `EA_EMBEDDINGS_BASE_URL` de plus, et c'est comme cela que
  celui du Mac a pu dériver sans que rien ne le contredise.
- `pipelines/` n'est pas concerné : son `LM_STUDIO_API_BASE` sert l'alias de
  *chat* `fast` de `litellm.yaml`, pas les embeddings. Si ce modèle-là a bougé
  lui aussi, c'est une autre décision, dans `docs/adr/0028`.
- Si Ollama tire ce modèle sous un autre tag, c'est `EA_EMBEDDINGS_MODEL` qui
  le dit — et la réindexation qui suit.

## Alternatives écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Ne corriger que `deploy/ea.env` sur le Mac | Une ligne, tout de suite | Le repo continue de décrire une machine qui n'existe plus ; la prochaine machine repart du mauvais défaut | Écarté |
| Garder `text-embedding-mxbai-embed-large-v1` comme nom de modèle pour éviter la réindexation | Aucun `docs-reindex` | La colonne `model` mentirait sur ce qui a produit le vecteur, et le jour où les deux services coexistent la comparaison n'aurait plus de sens | Écarté |
| Remettre LM Studio sur le cluster | Rien à changer dans le repo | C'est une application de bureau sur une machine que personne ne surveille — le défaut que `0037` décrit déjà | Écarté |
