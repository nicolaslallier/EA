---
titre: /mcp servi derrière le NGINX de l'Infra, pour LibreChat
date: 2026-09-14
statut: Proposition
affects: deploy/ea.stack.yml, backend/tests/unit/test_deploy_stack.py, backend/src/ea/mcp/transport.py, backend/src/ea/core/config.py, backend/src/ea/main.py, CLAUDE.md, Infra nginx/conf.d/ea.conf, Infra keycloak/realm-import/ea-realm.json, ~/OpenCode/LibreChat
---

# 34. Servir `/mcp` derrière le NGINX de l'Infra, pour LibreChat

Date : 2026-09-14
Statut : Proposition

Amende [`0023`](0023-mcp-reserve-a-la-boucle-locale.md) et
[`0032`](0032-authentification-par-keycloak.md) pour la stack déployée
(`0027`) ; `make run-be` n'est pas concerné.

## Contexte

LibreChat tourne sur le Mac, stack Portainer `librechat` servie par le NGINX de
l'Infra à `https://chat.famillelallier.net`. On veut y utiliser les outils MCP
d'EA.

Trois faits bloquaient :

- La stack d'EA déployée pose `EA_MCP_ENABLED=false`, et `ea.conf` répond 404 à
  `/api/mcp` : `0023` et `0032` réservaient la décision de servir `/mcp`
  derrière un proxy à un ADR à part. C'est celui-ci.
- LibreChat s'authentifie par comptes locaux, pas par OpenID : il ne peut pas
  relayer un jeton Keycloak de son utilisateur. Il sait en revanche mener
  lui-même un flux OAuth vers un serveur MCP, par utilisateur, avec le rappel
  `${DOMAIN_SERVER}/api/mcp/<serveur>/oauth/callback`.
- Son conteneur ne fait confiance ni au CA de l'Infra
  (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`), ni, par sa protection SSRF, à une
  adresse privée comme `ea.infra.famillelallier.net` (192.168.2.35).

## Décision

**LibreChat appelle `https://ea.infra.famillelallier.net/api/mcp`, par le
NGINX de l'Infra, et chaque utilisateur s'y connecte au realm `ea` par le
client confidentiel `ea-librechat`.**

| Où | Quoi |
|---|---|
| `deploy/ea.stack.yml` | `EA_MCP_ENABLED=true`, `EA_MCP_ALLOW_REMOTE_CLIENTS=true`, `EA_MCP_ALLOWED_HOSTS` et `EA_MCP_RESOURCE_URL` au nom du vhost ; `test_deploy_stack.py` les lie à `EA_CORS_ORIGINS` |
| Infra `nginx/conf.d/ea.conf` | `= /api/mcp` vers `ea-api:8000/mcp`, sans tampon et avec un long délai de lecture (flux SSE) ; `= /.well-known/oauth-protected-resource/api/mcp` vers `ea-api` sans réécriture |
| Infra `ea-realm.json` + Keycloak en service | client confidentiel `ea-librechat` : code + PKCE `S256`, redirection exacte `https://chat.famillelallier.net/api/mcp/ea/oauth/callback`, audience `ea-api` ; secret hors du fichier |
| LibreChat | `mcpServers.ea` (`requiresOAuth`, `client_secret_post`), `mcpSettings.allowedAddresses` pour le vhost et Keycloak, CA de l'Infra monté en lecture seule sous `NODE_EXTRA_CA_CERTS` |

Deux détails ne se devinent pas :

- **La métadonnée de ressource n'est pas sous `/api/`.** Le SDK la publie à
  `https://<hôte>/.well-known/oauth-protected-resource` suivi du chemin de la
  ressource (RFC 9728 §3.1), soit `…/.well-known/oauth-protected-resource/api/mcp`,
  route qu'il enregistre telle quelle sur l'API. Sans une `location` dédiée,
  NGINX l'envoie au SPA, qui répond 200 en HTML, et la découverte échoue sans
  bruit.
- **L'import de realm ne crée pas le client.** `--import-realm` ignore un realm
  qui existe déjà : `ea-realm.json` documente le client, `kcadm` le crée dans le
  Keycloak en service.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Réseau Docker dédié `ea-mcp` (API + LibreChat seuls), `EA_MCP_ALLOWED_PEERS` en CIDR, NGINX jamais sur le chemin | La garde par pair TCP garde un sens | Un réglage et un réseau de plus, un sous-réseau figé dans deux stacks | Écarté au profit de la simplicité |
| Un jeton de compte de service dans les `headers` de LibreChat | Pas de connexion par utilisateur | Tout utilisateur du chat écrit comme le même éditeur ; un jeton Keycloak expire en minutes et un en-tête statique ne se renouvelle pas | Écarté |
| LibreChat vers `make run-be` sur le Mac | Rien à déployer | Le serveur de développement, pas l'application ; pair non local depuis un conteneur | Écarté |

## Conséquences

- **Sur la stack déployée, la garde de boucle locale est levée.** Derrière
  NGINX elle ne distinguait personne ; `/mcp` est désormais joignable de tout
  client qui atteint le vhost, et **le jeton du realm est la seule barrière** :
  lecture pour tout appelant authentifié, écriture pour `ea-editor`, décidé dans
  `services/`. `EA_AUTH_ENABLED=false` y est déjà refusé hors `EA_DEBUG`, et
  `test_deploy_stack.py` refuse de le voir dans la stack.
- **Chaque utilisateur de LibreChat a besoin d'un compte du realm `ea`** —
  avec `ea-editor` pour écrire. Le chat n'élève personne.
- **Le secret d'`ea-librechat` vit dans le `.env` de LibreChat**, jamais dans
  un dépôt.
- **Trois dépôts et deux stacks bougent ensemble** : le nom du serveur MCP dans
  `librechat.yaml` (`ea`) est dans la redirection du client ; les renommer d'un
  côté casse la connexion.
- **`make run-be` ne change pas** : boucle locale par défaut, `.mcp.json`
  inchangé.
- À surveiller : un proxy intermédiaire qui tamponnerait le SSE bloque les
  réponses d'outil sans erreur ; `proxy_buffering off` sur la seule `location`
  de `/api/mcp`.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md),
  [0023](0023-mcp-reserve-a-la-boucle-locale.md),
  [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md),
  [0032](0032-authentification-par-keycloak.md)
- Code concerné : `deploy/ea.stack.yml`, `backend/tests/unit/test_deploy_stack.py`
