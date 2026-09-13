---
titre: Authentification par Keycloak
date: 2026-09-13
statut: Proposition
affects: backend/src/ea/domain/auth.py, backend/src/ea/domain/errors.py, backend/src/ea/domain/ports.py, backend/src/ea/services/caller.py, backend/src/ea/services/{architecture,documents,ipam}.py, backend/src/ea/repositories/keycloak.py, backend/src/ea/api/auth.py, backend/src/ea/api/me.py, backend/src/ea/api/errors.py, backend/src/ea/mcp/auth.py, backend/src/ea/mcp/errors.py, backend/src/ea/main.py, backend/src/ea/reindex.py, backend/src/ea/core/config.py, .mcp.json, frontend/src/lib/{auth,api,me}.ts, frontend/src/router/, frontend/src/components/UserBadge.vue, pipelines/src/pipelines/auth.py, deploy/ea.stack.yml, frontend/Dockerfile
---

# 31. Authentification par Keycloak

Amende [`0023`](0023-mcp-reserve-a-la-boucle-locale.md). Remplace la ligne
« OAuth2 password/bearer … `argon2` » des règles de sécurité de `CLAUDE.md`,
qui décrivait une auth jamais écrite.

## Contexte

**Quiconque atteint l'API écrit dans le graphe.** L'API écoute `0.0.0.0`
(`0016`), le SPA est servi à tout le réseau (`0022`, `0027`), et `/mcp` n'est
tenu que par la garde de boucle locale de `0023` — une restriction de *lieu*,
pas une identité. Le pipeline de `0028` écrit par l'API, sans rien prouver non
plus.

**Keycloak tourne déjà** dans la stack Infra, derrière son NGINX, à
`https://keycloak.famillelallier.net`, et sert déjà Jarvis. Jarvis le branche
par oauth2-proxy et `auth_request` : la passerelle authentifie, l'application
lit un en-tête. Ce montage n'existe qu'en déploiement, ne couvre ni un agent
MCP ni un worker, et fait confiance à un en-tête — exactement le défaut que
`0023` refuse pour `Host`.

`CLAUDE.md` promettait « OAuth2 password/bearer, JWT, `argon2` » : un serveur
d'identité écrit à la main, avec ses mots de passe, sa table et son
renouvellement, alors qu'un serveur d'identité est déjà là.

## Décision

**EA est un *resource server* du realm Keycloak `ea`.** Il ne connaît aucun
mot de passe : il vérifie lui-même chaque jeton d'accès, et les services
décident sur l'appelant qu'il prouve.

### Le realm

Déclaré dans le dépôt Infra (`keycloak/realm-import/ea-realm.json`), importé
par Keycloak. Chaque client porte un mapper d'audience `ea-api` : sans lui un
jeton dit `aud: account`, et l'API refuserait tout — ou, sans vérifier `aud`,
accepterait le jeton de n'importe quel client du realm.

| Client | Type | Flux | Redirections |
|---|---|---|---|
| `ea-spa` | public | code + PKCE `S256` | exactes : `https://ea.infra.famillelallier.net/auth/callback`, `http://localhost:5173/auth/callback`, `http://127.0.0.1:5173/auth/callback` |
| `ea-mcp` | public | code + PKCE `S256` | exactement `http://localhost:33418/callback`, le `callbackPort` de `.mcp.json` |
| `ea-pipelines` | confidentiel | *client credentials* seul | aucune ; son compte de service porte `ea-editor` |

**Un rôle, `ea-editor`.** Tout utilisateur du realm lit ; seul `ea-editor`
écrit. Utilisateurs et attribution du rôle se font à la console ; le secret
d'`ea-pipelines` est généré par Keycloak, jamais committé.

### L'API

| Point | Choix | Pourquoi |
|---|---|---|
| Vérification | `JwtVerifier` (`repositories/keycloak.py`) : **RS256 seul**, `iss`, `aud=ea-api`, `exp`, `iat`, `sub` exigés ; clés lues au JWKS du realm par `httpx` | L'algorithme n'est jamais lu dans le jeton : sinon `none`, ou un HS256 « signé » avec la clé publique, passent. `PyJWKClient` bloquerait la boucle |
| Horloge | `CLOCK_LEEWAY_SECONDS` = 30 s de tolérance sur `iat`, `nbf` et `exp` | `make run-be` tourne sur le Mac, Keycloak dans la VM de Docker Desktop, dont l'horloge dérive après une veille ; `pyjwt` refuse un `iat` en avance de 5 s, et le premier `/me` après la connexion serait un 401 |
| Rotation | Un `kid` inconnu recharge le JWKS, au plus une fois par intervalle | Une rotation est prise sans redémarrage ; un flot de `kid` forgés ne devient pas un appel à Keycloak par requête |
| Démarrage | Le JWKS est sondé au démarrage, avec `EA_AUTH_CA_CERT` | Comme les deux bases : une API qui ne peut vérifier aucun jeton ne démarre pas |
| Dépendance | `pyjwt[crypto]>=2.13` déclaré en dépendance directe | Déjà verrouillé par `mcp`, mais on l'importe : le plancher est au-dessus de CVE-2024-53861 |
| Qui appelle | `current_caller`, un `ContextVar` (`services/caller.py`), posé par l'adaptateur | Même mécanique que l'identifiant de requête de `0021` |
| Autorisation | Chaque méthode publique des trois services commence par `require_caller()` ou `require_editor()` | La règle est dans `services/`, pas dans un routeur : `/mcp` et l'API la reçoivent sans la répéter |
| Échec fermé | Aucun appelant posé → `NotAuthenticatedError` | Un branchement oublié est un 401 dans un test, pas une porte ouverte |
| Erreurs | `NotAuthenticatedError` → 401 `unauthenticated` + `WWW-Authenticate: Bearer` ; `NotAuthorisedError` → 403 `forbidden` | Messages génériques, dans l'enveloppe habituelle |
| REST | Dépendance `Authenticated` (`api/auth.py`) sur chaque routeur sauf `health` | `/health` est lu par une sonde qui n'a pas de jeton |
| `GET /me` | `MeRead {username, can_write}` | Le SPA **demande** s'il peut écrire ; il ne lit pas le jeton |
| Auth coupée | `EA_AUTH_ENABLED=false` accepté seulement avec `EA_DEBUG`, chaque appel est `LOCAL_DEVELOPER`, éditeur, et le démarrage l'annonce | Même règle que le mot de passe Neo4j |
| Scripts | `ea.reindex` agit comme `SYSTEM` | Pas de requête derrière, mais les services exigent un appelant |

### `/mcp`

Le transport devient resource server du même realm (`mcp/auth.py`) : le SDK
reçoit un `KeycloakTokenVerifier` au-dessus du même `JwtVerifier`, publie
`/.well-known/oauth-protected-resource/mcp` (RFC 9728, ressource
`EA_MCP_RESOURCE_URL`), et `speaking_plainly` pose l'appelant tiré du jeton
avant l'outil. Claude Code découvre Keycloak par ces métadonnées et se connecte
comme `ea-mcp` (`.mcp.json` : `oauth.clientId`, `oauth.callbackPort: 33418`).

**Claude Code doit faire confiance au CA de l'Infra.** La découverte et
l'échange de jetons se font dans Claude Code, un processus Node, auprès de
`https://keycloak.famillelallier.net` ; Node ne lit ni `EA_AUTH_CA_CERT` ni,
par défaut, le trousseau macOS, et la première connexion à `/mcp` échoue en
TLS. Claude Code se lance donc avec
`NODE_EXTRA_CA_CERTS=~/OpenCode/Infra/certs/infra-ca.crt`. Si la découverte
elle-même échoue, `oauth.authServerMetadataUrl` dans `.mcp.json` nomme
directement les métadonnées de Keycloak — repli connu, volontairement non
posé.

**La garde de boucle locale de `0023` reste**, en défense en profondeur : un
jeton ne rend pas `/mcp` servable derrière un proxy, dont le pair est le proxy.
La stack déployée garde `EA_MCP_ENABLED=false` ; servir `/mcp` derrière NGINX
sera une décision à part, avec son ADR.

### Le SPA

**`oidc-client-ts`, nouvelle dépendance d'exécution** — OIDC générique, pas
liée à Keycloak. Code + PKCE, jetons en `InMemoryWebStorage` (jamais
`localStorage`), seul l'état PKCE en `sessionStorage` : il est à usage unique
et ne contient aucun jeton. Un rechargement vide la mémoire ; la garde du
routeur renvoie vers Keycloak, dont le cookie de session répond par une
redirection, sans formulaire. `/auth/callback` est une route hors `SECTIONS`.
Une redirection qui ne peut pas partir (Keycloak injoignable, métadonnées
illisibles, page hors contexte sécurisé) mène à `/auth/failed`, qui le dit et
propose *Réessayer*, la raison dans la console — pas une page blanche.

`lib/api.ts` pose `Authorization: Bearer` et, sur un 401, relance la connexion
en gardant l'URL (`?element=` survit). **Une seule redirection par chargement
de page**, et **aucune** si le 401 arrive peu après une connexion terminée
(`REAUTH_GUARD_MS`) : l'erreur reste à l'écran. Sans cela, une API qui refuse
un jeton que Keycloak accepte — audience, horloge, JWKS injoignable —
ferait boucler le navigateur sur Keycloak.

`UserBadge` affiche le nom et la déconnexion ; les contrôles d'écriture sont
masqués quand `/me` dit `can_write: false`. L'API reste seule juge.

### Le pipeline et le déploiement

Le worker obtient son jeton par *client credentials* (`ClientCredentials`,
un `httpx.Auth` dans `pipelines/src/pipelines/auth.py`), le réutilise jusqu'à
expiration, en redemande un sur 401. Il joint Keycloak par
`extra_hosts: keycloak.famillelallier.net:host-gateway` et le CA de l'Infra
déjà monté pour MinIO.

`deploy/ea.stack.yml` monte ce CA en lecture seule dans l'API
(`INFRA_CA_CERT`, obligatoire, → `EA_AUTH_CA_CERT`) : dans `infra-net`,
`keycloak.famillelallier.net` est un alias de `nginx`, qui présente le
certificat de l'Infra. L'image du SPA reçoit `VITE_AUTH_AUTHORITY` et
`VITE_AUTH_CLIENT_ID` en arguments de build, puisque Vite les écrit dans le
bundle.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Passerelle oauth2-proxy + `auth_request`, comme Jarvis | Rien à écrire dans l'application | N'existe qu'en déploiement ; ne couvre ni `/mcp` ni le pipeline ; l'application fait confiance à un en-tête que l'appelant peut écrire (`0023`) ; l'autorisation n'est plus dans `services/` | Écarté |
| BFF à cookie de session | Aucun jeton dans le navigateur | Une table de sessions, une défense CSRF, et un second mécanisme puisque `/mcp` et le pipeline ont de toute façon besoin d'un jeton porteur | Écarté |
| `keycloak-js` | L'adaptateur officiel | Lie le SPA à un fournisseur ; `oidc-client-ts` parle OIDC et fait la même chose | Écarté |
| PKCE écrit à la main | Aucune dépendance | Vérificateur, état, échange de code, renouvellement, déconnexion : du code de sécurité à maintenir seul pour ce qu'une bibliothèque éprouvée fait | Écarté |
| OAuth2 password + `argon2` (l'ancienne ligne de `CLAUDE.md`) | Aucun service externe | Des mots de passe à stocker et à réinitialiser, alors que Keycloak est déjà là ; le flux *password* est déconseillé par OAuth 2.1 | Écarté |

## Conséquences

- **Keycloak arrêté, l'API ne démarre pas.** Une API déjà démarrée continue de
  servir avec les clés en cache ; seule une rotation pendant la panne échoue.
- **Chaque nouvelle méthode de service doit faire sa vérification.**
  `tests/unit/test_service_guards.py` lit le source (AST) et échoue sur une
  méthode publique qui ne commence pas par `require_caller()` ou
  `require_editor()`.
- **Les tests ont un appelant par défaut** : la fixture autouse
  `_an_editor_is_calling` ; `nobody_calling`, `acting_as(a_reader())` pour les
  refus. Les tests d'API construisent `Settings(auth_enabled=False)` sauf
  s'ils testent l'auth, avec alors `StaticVerifier`.
- **Aucun test Playwright du vrai parcours de connexion** : Playwright n'est
  pas en place et la CI n'a pas de Keycloak. Le point « comportement visible
  couvert par un E2E » de la définition de fini reste ouvert.
- **Une origine LAN en http ne se connecte pas.** `oidc-client-ts` construit
  PKCE avec `crypto.subtle`, que le navigateur ne donne qu'à un contexte
  sécurisé : sur `http://<ip-LAN>:5173` (`0022`), la redirection vers Keycloak
  lève avant de partir, et aucune URI ajoutée à `ea-spa` n'y change rien. Avec
  l'auth, le SPA s'ouvre depuis un contexte sécurisé : `http://localhost:5173`
  sur la machine qui sert Vite ; depuis un autre poste, par
  `ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 <hôte>` puis
  `http://localhost:5173` — `localhost` est un contexte sécurisé, déjà parmi
  les redirections d'`ea-spa` et dans `EA_CORS_ORIGINS`, qui n'a besoin
  d'aucune entrée LAN ; ou par le vhost https. `0022` reste vrai pour une page
  qui ne demande pas de connexion ; il ne l'est plus pour ce SPA.
- **Les redirections sont exactes.** L'unique redirection d'`ea-mcp` suit le `callbackPort` de `.mcp.json` :
  changer l'un sans l'autre casse la connexion de l'agent.
- **Deux dépôts bougent ensemble** : le realm vit dans l'Infra, les noms de
  client, de rôle et d'audience ici. En renommer un d'un côté refuse tout le
  monde de l'autre.
- **`EA_MCP_ALLOW_REMOTE_CLIENTS` ne disparaît pas** (question laissée ouverte
  par `0023`) : la garde de boucle locale reste la décision sur le *lieu*, le
  jeton celle sur l'*identité*.
- **`EA_AUTH_ENABLED=false` ne coupe que l'API.** Le SPA n'a pas
  d'interrupteur : `make run-fe` redirige toujours vers Keycloak, qui doit
  donc être joignable pour développer le frontend. À reprendre si ce besoin se
  présente.
- **Rien n'est déployé** : le realm n'a pas été importé ni la stack
  redéployée. D'où le statut *Proposition*, qu'il ne quitte qu'une fois
  vérifiés en vrai :
  - la découverte RFC 8414 à chemin inséré
    (`/.well-known/oauth-authorization-server/realms/ea`) sur Keycloak 26.7,
    pour l'émetteur `/realms/ea` ;
  - ce que Keycloak fait du paramètre `resource=` (RFC 8707) que Claude Code
    peut envoyer ;
  - que les clients importés reçoivent les *client scopes* par défaut `basic`
    et `roles` — sans eux, pas de `sub` ni de `realm_access` dans le jeton ;
  - une connexion du SPA au vhost, puis `GET /api/me` comme lecteur et comme
    éditeur ;
  - une exécution d'`alimenter-catalogue` dont le jeton du compte de service
    porte `realm_access.roles` ;
  - une connexion à `/mcp` depuis Claude Code.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md),
  [0021](0021-des-logs-que-quelqu-un-peut-lire.md),
  [0023](0023-mcp-reserve-a-la-boucle-locale.md),
  [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md),
  [0028](0028-pipelines-python-dans-le-depot-ea.md)
- Infra : `keycloak/realm-import/ea-realm.json`
- Conception : `docs/superpowers/specs/2026-09-13-keycloak-authentication-design.md`
- Code concerné : voir `affects` ci-dessus
- Tests : `backend/tests/unit/test_service_guards.py`,
  `backend/tests/e2e/test_auth_api.py`, `backend/tests/unit/test_mcp_auth.py`,
  `backend/tests/unit/test_deploy_stack.py`
