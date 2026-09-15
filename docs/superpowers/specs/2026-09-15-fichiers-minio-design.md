# Fichiers dans MinIO — Web GUI et MCP

- **Date** : 2026-09-15
- **Statut** : design validé, à implémenter
- **ADR à écrire** : `docs/adr/0036-fichiers-dans-minio.md`

## But

Pouvoir déposer, parcourir, télécharger et supprimer des fichiers de **n'importe
quel type** dans MinIO, depuis le SPA et depuis `/mcp`. Un seul bucket,
`ea-catalogue` par défaut — celui que le pipeline `alimenter-catalogue` lit
déjà : un fichier déposé sous `inbox/` lui est donc offert sans autre étape.

## Approche retenue : l'API relaie les octets

Le SPA envoie le fichier à l'API (`multipart`), l'API l'écrit dans MinIO ; le
téléchargement fait le chemin inverse en streaming. Les outils MCP appellent le
même `FileService`.

Écartées :

- **URLs pré-signées** : CORS sur MinIO, CA Infra dans le navigateur, et un
  agent MCP ne peut pas faire le `PUT` lui-même — MCP aurait de toute façon
  besoin du relais, soit deux chemins et deux contrôles d'accès.
- **Console MinIO + OIDC Keycloak** : ne couvre pas MCP, et `ea-editor` devrait
  être redéclaré en politique MinIO — une seconde autorisation qui dérive.

Le relais est la seule option où l'autorisation reste décidée une fois, dans
`services/` (ADR 0032).

## 1. Cœur backend

### Settings (`core/config.py`)

| Réglage | Défaut | Note |
|---|---|---|
| `s3_enabled` | `False` | la stack déployée le met à `true` |
| `s3_endpoint` | `minio.famillelallier.net` | |
| `s3_secure` | `True` | |
| `s3_access_key`, `s3_secret_key` | — | `SecretStr` ; obligatoires si `s3_enabled` (validateur) |
| `s3_bucket` | `ea-catalogue` | |
| `s3_ca_cert` | `None` | CA Infra |

### Domaine (`domain/files.py`, pur)

- `MAX_FILE_BYTES = 50 * 1024 * 1024`.
- `MAX_TEXT_READ_BYTES = 1024 * 1024` (lecture texte pour MCP).
- `MAX_LISTED_ENTRIES = 1000`.
- `StoredFile(key, size, last_modified, content_type)` ; `name` = dernier
  segment de la clé.
- `FileListing(prefix, folders: list[str], files: list[StoredFile], truncated: bool)`.
- `clean_key(key) -> str` : non vide ; au plus 1024 octets UTF-8 ; pas de `/`
  initial ni final ; aucun segment vide (`//`), `.` ou `..` ; aucun caractère de
  contrôle ni NUL. Refus : `InvalidFileKeyError` (422).
- `clean_prefix(prefix) -> str` : vide accepté ; sinon mêmes règles sur les
  segments, et normalisé pour se terminer par `/`.
- Erreurs (`domain/errors.py`) : `StoredFileNotFoundError` (404),
  `StoredFileExistsError` (409), `InvalidFileKeyError` (422),
  `FileTooLargeError` (422), `FileNotTextError` (422),
  `FileStorageUnavailableError` (503, store désactivé).

### Port (`domain/ports.py`) et repository

`ObjectStore` : `list(prefix, limit) -> FileListing`, `stat(key) -> StoredFile`,
`put(key, data: bytes, content_type) -> StoredFile`,
`open(key) -> tuple[StoredFile, AsyncIterator[bytes]]`, `delete(key)`.

`repositories/object_store.py` — `MinioObjectStore` sur `minio` 7.2.20 (version
déjà verrouillée dans `pipelines/`), même `PoolManager` à CA privée que
`pipelines/storage.py`. Le SDK est synchrone : chaque appel passe par
`asyncio.to_thread`, sinon un gros upload bloque la boucle et `/mcp` avec.
Liste avec `delimiter="/"` (les dossiers sont des préfixes, jamais stockés).
`S3Error` `NoSuchKey` → `StoredFileNotFoundError`.

### Service (`services/files.py`, `FileService`)

| Méthode | Garde | Comportement |
|---|---|---|
| `list(prefix)` | `require_caller` | `clean_prefix`, au plus `MAX_LISTED_ENTRIES` |
| `open(key)` | `require_caller` | métadonnées + flux |
| `read_text(key)` | `require_caller` | UTF-8 strict, ≤ `MAX_TEXT_READ_BYTES`, sinon `FileNotTextError` / `FileTooLargeError` |
| `upload(key, raw, content_type, overwrite=False)` | `require_editor` | `clean_key`, taille ≤ `MAX_FILE_BYTES` ; clé existante et `overwrite=False` → 409 |
| `delete(key)` | `require_editor` | `stat` d'abord (S3 est idempotent) → 404 lisible |

- Chaque écriture journalise une ligne (`action`, `key`, `size`).
- « Refuser si existe » = `stat` puis `put` : `put_object` n'expose pas
  `If-None-Match`. Course tolérée, marquée `ponytail:` dans le code.
- `FileService` ajouté à `tests/unit/test_service_guards.py`.
- Store désactivé : le service existe quand même et lève
  `FileStorageUnavailableError` — les routes et outils restent déclarés.

## 2. Adaptateurs

### HTTP (`api/files.py`, tag `files`)

Les clés contiennent `/` : elles passent en query.

| Route | Rôle | Réponse |
|---|---|---|
| `GET /files?prefix=` | lecteur | `FileListingRead` |
| `POST /files` — multipart `file`, `prefix`, `overwrite` | éditeur | 201 `FileRead` ; 409 ; 422 |
| `GET /files/content?key=` | lecteur | `StreamingResponse`, `Content-Disposition: attachment; filename*=UTF-8''…`, `X-Content-Type-Options: nosniff` |
| `DELETE /files?key=` | éditeur | 204 ; 404 |

- Lecture de l'upload plafonnée à `MAX_FILE_BYTES + 1`, comme `api/documents.py`.
- Clé = `clean_prefix(prefix) + filename`, repassée par `clean_key`.
- **Toujours `attachment` + `nosniff`** : un `.html`/`.svg` servi inline sur
  l'origine EA serait une XSS stockée.
- Bornes déclarées une fois dans `api/schemas.py` ; `make openapi` régénère le
  client.

### MCP (`mcp/server.py`)

`build_mcp_server(..., get_files: FileProvider)` — obligatoire, comme
`get_documents`.

| Outil | Annotation | Détail |
|---|---|---|
| `list_files(prefix)` | `readOnlyHint` | |
| `read_file(key)` | `readOnlyHint` | texte seulement ; binaire refusé avec un message explicite |
| `upload_file(key, content, encoding="text"\|"base64", overwrite=False)` | écriture | base64 invalide → `ToolError` ; limite appliquée après décodage |
| `delete_file(key)` | `destructiveHint` | la docstring dit qu'il supprime |

- Une phrase dans `INSTRUCTIONS` : un fichier sous `inbox/` est lu par
  `alimenter-catalogue`.
- Liste complète des outils et `TestTheSameBoundsAsTheHttpAdapter` mis à jour
  dans `tests/unit/test_mcp_server.py`.

## 3. SPA

- Section `/fichiers` (« Fichiers »), nouveau groupe `storage` (« Stockage »)
  dans `GROUPS` ; `view: () => import('../features/files/FilesScreen.vue')`.
- `features/files/useFiles.ts` + `FilesScreen.vue`.
- **Préfixe dans l'URL** (`?prefix=`) : ouvrir un dossier pousse une entrée
  d'historique ; fil d'Ariane pour remonter.
- Liste via `useLatestRequest()` : dossiers puis fichiers (nom, taille, date).
- Upload : `<input type="file" multiple>`, envois séquentiels vers le préfixe
  courant ; sur 409, confirmation puis renvoi avec `overwrite=true`.
- Téléchargement : `openapi-fetch` avec `parseAs: 'blob'` puis
  `URL.createObjectURL` — le token est en mémoire, un `<a href>` n'aurait pas de
  bearer.
- Suppression avec confirmation.
- Upload et suppression masqués pour un lecteur (`GET /me`), comme les
  diagrammes.

## 4. Déploiement

- `deploy/ea.stack.yml` : `EA_S3_ENABLED: "true"`, `EA_S3_ENDPOINT`,
  `EA_S3_BUCKET`, `EA_S3_ACCESS_KEY` / `EA_S3_SECRET_KEY` en `:?`,
  `EA_S3_CA_CERT` = la CA déjà montée pour Keycloak.
- `deploy/ea.env.example`, `backend/.env.example` : variables sans valeur.
- **MinIO (manuel, documenté dans l'ADR)** : utilisateur `ea-api`, politique
  lecture-écriture limitée à `ea-catalogue/*`. La clé du pipeline reste en
  lecture seule.
- **Repo Infra (hors de ce repo)** : `client_max_body_size 50m;` dans
  `nginx/conf.d/ea.conf` — sinon le défaut NGINX de 1 Mio donne une 413.

## 5. Tests

- **Unit** : `clean_key`/`clean_prefix` ; `FileService` sur un `ObjectStore` en
  mémoire (409, 404, texte binaire, trop gros, gardes lecteur/éditeur, store
  désactivé) ; les 4 outils MCP (base64 invalide, bornes identiques au HTTP).
- **API** : 201, 409, 422 (clé, taille), 404, 401, 403 ; en-têtes
  `attachment` + `nosniff` ; streaming du contenu.
- **Intégration** : `MinioObjectStore` contre un MinIO jetable dans
  `docker-compose.yml` sur `127.0.0.1:9100`, service `minio` dans le job
  `integration` de la CI ; fixture refusant tout hôte non loopback
  (`throwaway.py`). Pas de driver mocké.
- **Vitest** : `FilesScreen` — navigation par préfixe, confirmation
  d'écrasement, contrôles masqués pour un lecteur, téléchargement.

## 6. Docs

- `docs/adr/0036-fichiers-dans-minio.md` : contexte, A vs B vs C, bucket partagé
  avec le pipeline, `attachment`/`nosniff`, course `stat`→`put`.
- `CLAUDE.md` mis à jour dans le même commit que le code : stack, layout,
  section MCP (32 outils), section SPA (huit sections).

## Hors périmètre

- Plusieurs buckets ; pagination au-delà de 1000 entrées (signalée par
  `truncated`) ; lecture base64 via MCP ; renommage/déplacement d'objet ;
  URLs pré-signées.
