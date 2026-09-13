# Authentification par Keycloak — conception

Date : 2026-09-13 · Statut : validée en discussion, à planifier · ADR à écrire : `0032-authentification-par-keycloak`

## Objet

Aujourd'hui quiconque atteint l'API, le SPA ou `/mcp` lit **et écrit** le
graphe. Après ce changement :

- tout appel exige un utilisateur authentifié par Keycloak ;
- toute écriture exige en plus le rôle de realm `ea-editor` ;
- la règle est tenue dans `services/`, jamais seulement dans un adaptateur.

Quatre appelants sont couverts : le SPA, l'API REST, `/mcp`, le worker
`pipelines/`.

## Décisions prises

| Question | Décision |
|---|---|
| Portée | Connexion + rôle d'écriture `ea-editor` ; tout utilisateur du realm lit |
| Realm | Nouveau realm `ea`, importé par l'Infra |
| Pipelines | Compte de service, *client credentials* |
| Développement local | Auth **activée par défaut**, même Keycloak |
| `/mcp` | Exige lui aussi un jeton porteur |
| Approche | L'application valide les JWT elle-même (resource server) |

Écartées : la passerelle oauth2-proxy + `auth_request` de Jarvis (n'existe
qu'en déploiement, ne couvre ni `/mcp` ni le pipeline, fait confiance à un
en-tête que l'appelant écrit — le défaut qu'évite `docs/adr/0023`) ; le BFF à
cookie de session (table de sessions, CSRF, et un second mécanisme puisque
`/mcp` et le pipeline ont besoin d'un jeton porteur de toute façon).

## 1. Keycloak — dépôt Infra, PR séparée

`keycloak/realm-import/ea-realm.json`, sur le modèle de `jarvis-realm.json` :

- realm `ea`, `enabled: true`, `sslRequired: external`,
  `registrationAllowed: false`, `resetPasswordAllowed: false` ;
- rôle de realm `ea-editor` ;
- **aucun `users`, aucun `secret`** : utilisateurs et attribution du rôle se
  font à la console d'administration, le secret du client confidentiel est
  généré par Keycloak à l'import ;
- chaque client porte un mapper `oidc-audience-mapper` avec
  `included.custom.audience: ea-api`, `access.token.claim: true`. Sans lui un
  jeton Keycloak dit `aud: account`, et l'API refuserait tout — ou, si elle ne
  vérifiait pas `aud`, accepterait les jetons de n'importe quel client du realm.

| Client | Type | Flux | Redirections / origines |
|---|---|---|---|
| `ea-spa` | public | standard flow, PKCE `S256` exigé (`pkce.code.challenge.method`) | `https://ea.infra.famillelallier.net/*`, `http://localhost:5173/*`, et chaque origine LAN servant le SPA, listée explicitement |
| `ea-mcp` | public | standard flow, PKCE `S256` | redirection de boucle locale du client MCP (voir *Inconnue 1*) |
| `ea-pipelines` | confidentiel | `serviceAccountsEnabled` seul, `standardFlowEnabled: false` | aucune ; le compte de service reçoit `ea-editor` |

`directAccessGrantsEnabled` et `implicitFlowEnabled` sont à `false` partout.

## 2. Backend

### Configuration (`core/config.py`)

| Variable | Défaut | Rôle |
|---|---|---|
| `EA_AUTH_ENABLED` | `true` | `false` n'est accepté que si `EA_DEBUG` est vrai — même règle que le mot de passe Neo4j |
| `EA_AUTH_ISSUER` | `https://keycloak.famillelallier.net/realms/ea` | `iss` attendu ; les clés sont lues à `{issuer}/protocol/openid-connect/certs` |
| `EA_AUTH_AUDIENCE` | `ea-api` | `aud` attendu |
| `EA_AUTH_CA_CERT` | vide | CA de l'Infra. Depuis le Mac, `keycloak.famillelallier.net` résout sur `127.0.0.1` avec un certificat que le trousseau système ne connaît pas |
| `EA_MCP_RESOURCE_URL` | `http://127.0.0.1:8000/mcp` | identifiant de ressource publié pour `/mcp` (RFC 9728) |

Auth coupée : les adaptateurs posent un appelant `LOCAL_DEVELOPER` portant
`ea-editor`, et le démarrage l'annonce en `WARNING`.

### Découpage

- **`domain/auth.py`** (pur) — `Caller(subject: str, username: str, roles:
  frozenset[str])`, `EDITOR_ROLE = "ea-editor"`, `Caller.can_write`, et les
  erreurs `NotAuthenticated` et `NotAuthorised`.
- **`repositories/keycloak.py`** — `JwtVerifier` : récupère le JWKS par
  `httpx` (déjà une dépendance ; `PyJWKClient` est bloquant), le met en cache,
  vérifie par `pyjwt` (`pyjwt[crypto]`, déjà verrouillé par `mcp`, déclaré
  désormais en dépendance directe). Exige `RS256` uniquement, `iss`, `aud`,
  `exp`, `nbf`, `sub` ; un `kid` inconnu déclenche **un** rechargement du JWKS,
  limité dans le temps. Rend un `Caller` (rôles lus dans
  `realm_access.roles`, nom dans `preferred_username`) ou lève
  `NotAuthenticated`. Le JWKS est sondé au démarrage : un Keycloak injoignable
  empêche le démarrage, comme les autres dépendances.
- **`services/caller.py`** — `current_caller: ContextVar[Caller | None]`,
  `require_caller() -> Caller`, `require_editor() -> Caller`. **Échec fermé** :
  sans appelant posé, refus.
- **Services** — chaque méthode publique de `ArchitectureService`,
  `DocumentService` et `IpamService` commence par `require_caller()` (lecture)
  ou `require_editor()` (écriture). Un test énumère les méthodes publiques et
  vérifie le refus sans appelant, et le refus des écritures pour un lecteur.
- **`reindex.py`** — pose un appelant système `ea-reindex` portant `ea-editor`.

### REST

- Dépendance `Authenticated` (schéma `HTTPBearer`) : valide le jeton, pose
  `current_caller`. Chaque routeur sauf `health` est inclus avec
  `dependencies=[Authenticated]`.
- Test de liste complète : toute route de `app.routes` hors `/health`,
  `/docs`, `/openapi.json` répond 401 sans jeton.
- `NotAuthenticated` → `401 {"error": "unauthenticated"}` avec
  `WWW-Authenticate: Bearer` ; `NotAuthorised` → `403 {"error": "forbidden"}`
  (`api/errors.py`). Messages génériques.
- `GET /me` → `MeRead {username, can_write}` : le SPA **demande** s'il peut
  écrire, il ne lit pas le jeton.
- Le schéma OpenAPI gagne `securitySchemes` : `make openapi` régénère le client.

### MCP

- `build_mcp_server(..., token_verifier=...)` construit
  `MCPServer(auth=AuthSettings(issuer_url=EA_AUTH_ISSUER,
  resource_server_url=EA_MCP_RESOURCE_URL), token_verifier=...)`, le
  vérificateur étant un adaptateur mince autour du même `JwtVerifier`.
- La route de métadonnées `/.well-known/oauth-protected-resource…` que le SDK
  produit est greffée comme `/mcp` l'est déjà (pas de `Mount`).
- `speaking_plainly` lit `get_access_token()`, en tire le `Caller`, pose
  `current_caller`. `NotAuthorised` et `NotAuthenticated` deviennent des
  `ToolError` (`mcp/errors.py`).
- **La garde de boucle locale reste** (`mcp/transport.py`), en défense en
  profondeur. Le stack déployé garde `EA_MCP_ENABLED=false` ; servir `/mcp`
  derrière NGINX est une décision future, avec son ADR.

## 3. SPA

- Dépendance nouvelle **`oidc-client-ts`** (OIDC générique, non lié à
  Keycloak), consignée dans l'ADR.
- **`lib/auth.ts`** — `UserManager` : `authority` = `VITE_AUTH_AUTHORITY`
  (défaut : l'issuer ci-dessus), `client_id` = `VITE_AUTH_CLIENT_ID` (défaut
  `ea-spa`), `redirect_uri` = `{origine}/auth/callback`, `response_type:
  code`, `scope: openid profile`, `userStore` en `InMemoryWebStorage` — jamais
  `localStorage` —, renouvellement par refresh token. Un rechargement vide la
  mémoire : redirection vers Keycloak, que le cookie de session SSO renvoie
  aussitôt, sans formulaire.
- **`lib/api.ts`** — middleware `openapi-fetch` : `Authorization: Bearer` sur
  chaque appel ; sur 401, relance la connexion en conservant l'URL courante
  (`?element=` et consorts survivent).
- **Routeur** — route `/auth/callback` ajoutée dans `router/`, **pas** dans
  `SECTIONS` (ce n'est pas une section) ; garde globale : sans utilisateur,
  connexion.
- **Interface** — `AppNav` affiche le nom et *Déconnexion* ; `useMe()` lit
  `GET /me` et masque les contrôles d'écriture quand `can_write` est faux.
  L'API reste seule juge.

## 4. Pipelines, déploiement

- **`pipelines/`** — `KeycloakClientCredentials(httpx.Auth)` : obtient un
  jeton au point `token` de l'issuer, le réutilise jusqu'à expiration, en
  redemande un sur 401. `EaClient` le reçoit. Réglages
  `PIPELINES_EA_CLIENT_ID` (défaut `ea-pipelines`),
  `PIPELINES_EA_CLIENT_SECRET` (vide dans `.env.example`),
  `PIPELINES_AUTH_ISSUER`. Le CA de MinIO (`PIPELINES_S3_CA_CERT`) sert aussi
  pour Keycloak.
- **`deploy/ea.stack.yml`** — `api` : `EA_AUTH_CA_CERT` pointant sur le CA
  monté en lecture seule ; `web` : `VITE_AUTH_AUTHORITY`/`VITE_AUTH_CLIENT_ID`
  en `build.args`. Dans `infra-net`, `keycloak.famillelallier.net` est déjà
  un alias de `nginx`. `nginx/conf.d/ea.conf` inchangé.
- **Documentation** — ADR `0032` (remplace la ligne « OAuth2 password/bearer
  + argon2 » de `CLAUDE.md`, amende `0023`) ; `CLAUDE.md` mis à jour dans le
  même commit que le code ; `.env.example` des deux projets.

## Tests (TDD)

- **Unitaires, `JwtVerifier`** — clé RSA générée dans le test, JWKS servi par
  `httpx.MockTransport` : jeton valide ; expiré ; `aud` faux ; `iss` faux ;
  `alg: none` et `HS256` refusés ; `kid` inconnu → un rechargement puis refus ;
  rôles et nom extraits.
- **Unitaires, services** — refus de chaque méthode publique sans appelant ;
  refus de chaque écriture pour un lecteur ; succès pour un éditeur.
- **Unitaires, `Settings`** — `auth_enabled=False` refusé hors `debug`.
- **API** (`tests/e2e`) — 401 sans jeton / jeton invalide, 403 lecteur en
  écriture, 200 éditeur, `GET /me` ; test de liste complète des routes.
- **MCP** — outil refusé sans jeton ; outil d'écriture refusé pour un lecteur.
- **Pipelines** — `KeycloakClientCredentials` : obtention, réutilisation,
  renouvellement sur 401 (MockTransport).
- **Vitest** — en-tête porteur ajouté ; 401 → connexion ; contrôles d'écriture
  masqués quand `can_write` est faux ; route de rappel.
- **Existant** — les tests d'API et de services ont désormais besoin d'un
  appelant : un assistant `an_editor()` / `a_reader()` et une fixture
  l'absorbent. Le diff touchera beaucoup de fichiers de tests.
- **Non couvert** — aucun test Playwright du vrai parcours de connexion :
  Playwright n'est pas en place et la CI n'a pas de Keycloak. Le point
  « comportement visible couvert par un E2E » de la définition de fini reste
  ouvert.

## Inconnues à lever en tête de plan

1. **Claude Code ↔ Keycloak pour `/mcp`.** Découverte RFC 9728 → Keycloak ;
   Keycloak refuse l'enregistrement dynamique anonyme par défaut, il faut donc
   probablement un client `ea-mcp` pré-enregistré, et la redirection de boucle
   locale de Claude Code utilise un port variable. À vérifier avant d'écrire le
   code MCP ; si c'est bloquant, repli : jeton passé en en-tête dans
   `.mcp.json` (issu d'une variable d'environnement), documenté dans l'ADR.
2. **Rôles dans `AccessToken`.** Vérifier qu'une sous-classe portant le
   `Caller` survit à `get_access_token()` dans `mcp` 2.2 ; sinon un cache
   jeton → `Caller` borné dans l'adaptateur.
3. **ContextVar et dépendances FastAPI.** Vérifier par un test qu'une valeur
   posée dans une dépendance `async` est visible de l'endpoint et des services
   qu'il appelle ; sinon un middleware ASGI pur pose l'appelant.
4. **Le worker atteint-il Keycloak ?** `pipelines/docker-compose.yml` n'est
   peut-être pas sur `infra-net`, où vit l'alias `keycloak.famillelallier.net`.
   Vérifier la résolution et la confiance TLS depuis le conteneur avant
   d'écrire `KeycloakClientCredentials`.

## Hors périmètre

Rôles plus fins que `ea-editor` ; audit « qui a modifié quoi » ; `/mcp`
servi derrière NGINX ; création d'utilisateurs ; test Playwright.
