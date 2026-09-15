---
titre: Les fichiers de tout type, dans le MinIO de l'Infra
date: 2026-09-15
statut: Proposition
affects: backend/src/ea/domain/files.py, backend/src/ea/domain/errors.py, backend/src/ea/domain/ports.py, backend/src/ea/core/config.py, backend/src/ea/repositories/object_store.py, backend/src/ea/services/files.py, backend/src/ea/api/files.py, backend/src/ea/mcp/server.py, frontend/src/features/files, docker-compose.yml, deploy/ea.stack.yml, CLAUDE.md
---

# 36. Les fichiers de tout type, dans le MinIO de l'Infra

Date : 2026-09-15
Statut : Proposition

## Contexte

Le catalogue attache déjà du markdown aux éléments (`docs/adr/0017`), mais
rien ne permet de déposer un fichier de type quelconque — un PDF, une
capture, un export — depuis le SPA ou depuis un agent. Le seul code du dépôt
qui parle à MinIO est `pipelines/storage.py`, en lecture seule, pour le flux
`alimenter-catalogue` (`docs/adr/0028`) : rien côté `backend/` n'écrit dans ce
magasin, et rien n'en sert le contenu.

Le MinIO visé est celui de la stack `~/OpenCode/Infra`, comme PostgreSQL
(`docs/adr/0029`) et l'embedder : un magasin de plus à faire tourner ici
n'apporterait rien qu'un compte et une politique sur celui qui existe déjà.

## Décision

**L'API relaie les octets, elle ne les interprète pas.** `FileService`
(`services/files.py`) est le point d'entrée unique du SPA et de `/mcp`, sous
la même garde que le reste : `require_caller()` pour lister, ouvrir ou lire un
fichier, `require_editor()` pour en déposer ou en supprimer un — exactement la
règle de `services/caller.py` pour tout le reste du catalogue. `domain/files.py`
est pur et ne connaît pas MinIO : c'est lui qui dit ce qu'est un chemin valide,
ce qu'est un dossier (le début partagé de plusieurs clés — jamais un objet
écrit) et où sont les limites (50 Mo par envoi, 1000 entrées par listage,
1024 octets par clé, comme S3 lui-même).

**Un seul bucket, `ea-catalogue`, partagé avec le pipeline.** Le dossier
`inbox/` est celui que lit `alimenter-catalogue` : y déposer un fichier depuis
le SPA ou un agent l'offre au même flux, sans second magasin ni synchronisation
à écrire.

**Un fichier déjà présent au même chemin est un 409, sauf `overwrite`.** Deux
personnes qui déposent sans le savoir au même endroit ne s'écrasent pas
silencieusement ; demander le remplacement est un choix explicite.

**Le téléchargement est toujours une pièce jointe.** `Content-Disposition:
attachment`, `X-Content-Type-Options: nosniff` et une `Content-Security-Policy:
default-src 'none'; sandbox` sur `/files/content` : le bucket accepte
n'importe quel type, et un HTML ou un SVG servi en ligne sur cette origine
s'exécuterait avec la session de qui vient de l'ouvrir.

**Un agent lit et écrit du texte, jamais un flux binaire brut.** `read_file`
ne rend que du texte UTF-8, au plus 1 Mo : un PDF est refusé, une personne le
télécharge depuis le SPA. `upload_file` accepte le texte tel quel
(`encoding="text"`) ou du contenu binaire encodé en base64
(`encoding="base64"`), parce qu'un agent MCP n'a pas de fichier à joindre à une
requête — seulement du texte à passer en paramètre. Sa limite réelle n'est pas
les 50 Mo du SPA : le SDK MCP plafonne le corps d'une requête à 4 Mio par
défaut, et le NGINX de l'Infra déployé limite `/api/mcp` à 2 Mo — `upload_file`
refuse donc tout ce qui dépasse 1 Mo une fois décodé (`MAX_MCP_UPLOAD_BYTES`),
et le dit dans sa description ; un fichier plus gros se dépose depuis le SPA.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| URLs pré-signées, le navigateur ou l'agent parle direct à MinIO | Décharge l'API du transfert | CORS à ouvrir sur MinIO, son CA à faire confiance côté navigateur, et un agent MCP ne sait pas faire un `PUT` HTTP arbitraire — seulement appeler un outil | Écarté |
| Console MinIO + connexion OIDC déléguée | Rien à coder côté API | Aucun outil pour un agent, et un second modèle d'autorisation à côté de `services/` — la règle d'un seul endroit qui décide qui peut écrire (`docs/adr/0032`) s'en trouverait dédoublée | Écarté |

## Conséquences

- **Une dépendance de plus côté backend** : le SDK `minio`, synchrone. Chaque
  appel tourne dans `asyncio.to_thread` (`repositories/object_store.py`), pour
  ne jamais bloquer la boucle d'événement le temps d'un envoi de 50 Mo — ce qui
  bloquerait `/mcp` avec.
- **Le flux de téléchargement est rendu directement par `StreamingResponse`.**
  Si le client abandonne la lecture en cours de route, c'est le finaliseur du
  générateur asynchrone (`_chunks`, dans son `finally`) qui referme et rend la
  connexion MinIO — jamais une fuite laissée à un `GC` hypothétique.
- **La course entre `stat` et `put` est assumée, pas corrigée.** Deux éditeurs
  qui déposent au même chemin dans le même instant peuvent tous deux réussir ;
  le SDK MinIO n'expose pas d'écriture conditionnelle (`If-None-Match`). Un
  `# ponytail:` dans `services/files.py` nomme la limite et ce qu'il faudrait
  pour la lever.
- **`EA_S3_ENABLED` est éteint par défaut.** Un déploiement sans MinIO
  configuré démarre quand même ; les routes `/files` et les quatre outils MCP
  restent déclarés et répondent 503 — la même règle que pour les documents
  (`docs/adr/0018`) : le service existe, il refuse juste d'agir.
- **Un MinIO jetable rejoint le PostgreSQL jetable** dans
  `docker-compose.yml`, sur `127.0.0.1:9100` ; `tests/integration/throwaway.py`
  refuse tout autre point de terminaison (`refuse_a_shared_minio`), et
  `make test-integration` le démarre avec `make minio-up`. **Sa suite
  d'intégration est écrite mais n'a pas encore tourné pour de vrai** — aucun
  Docker n'était disponible pendant ce travail ; c'est dû, au même titre que
  les trois points ci-dessous.

### À faire hors de ce repo

1. Sur le MinIO de l'Infra : un utilisateur `ea-api` et une politique
   lecture-écriture limitée à `arn:aws:s3:::ea-catalogue` et
   `arn:aws:s3:::ea-catalogue/*` (`s3:ListBucket`, `s3:GetObject`,
   `s3:PutObject`, `s3:DeleteObject`) — rien de plus large.
2. Dans `~/OpenCode/Infra/nginx/conf.d/ea.conf`, la `location /api/` porte déjà
   `client_max_body_size 2m;` (pour un document markdown) : la monter à
   `51m`, pour l'enveloppe multipart d'un envoi de 50 Mo. `location = /api/mcp`
   reste à `2m` — les outils MCP des fichiers ont leur propre plafond, bien
   plus bas (voir `mcp/server.py`, `MAX_MCP_UPLOAD_BYTES`).
3. Les deux clés dans `deploy/ea.env` : `make app-up` refuse de partir tant
   qu'une variable `:?` du fichier de stack est vide.

## Références

- ADR liés : [0017](0017-documents-markdown-attaches-aux-elements.md),
  [0018](0018-documents-exposes-aux-agents-via-mcp.md), [0028](0028-pipelines-python-dans-le-depot-ea.md),
  [0029](0029-nouvelle-adresse-du-cluster-et-postgresql-sur-le-mac.md),
  [0032](0032-authentification-par-keycloak.md)
- Code concerné : `backend/src/ea/domain/files.py`,
  `backend/src/ea/services/files.py`, `backend/src/ea/api/files.py`,
  `backend/src/ea/repositories/object_store.py`, `backend/src/ea/mcp/server.py`,
  `frontend/src/features/files`
