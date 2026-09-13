---
titre: /mcp ne répond qu'aux clients de la machine tant que l'auth n'existe pas
date: 2026-09-13
statut: Accepté
affects: backend/src/ea/mcp/transport.py, backend/src/ea/main.py, backend/src/ea/core/config.py, backend/.env.example
---

# 23. Réserver `/mcp` à la boucle locale

Date : 2026-09-13
Statut : Accepté

Complète [`0014`](0014-serveur-mcp-pour-les-agents.md) et corrige ce que
[`0016`](0016-ecoute-sur-toutes-les-interfaces.md) laissait croire de
`EA_MCP_ALLOWED_HOSTS`.

## Contexte

`/mcp` expose vingt-huit outils, dont la création, la modification et la
suppression d'éléments, de liens, de documents et d'adresses IP. **Il n'y a pas
encore d'authentification** : quiconque atteint l'endpoint peut écrire dans le
graphe partagé.

Depuis `0016`, l'API écoute `0.0.0.0` : elle est joignable de toute machine du
réseau local. `0016` présentait `EA_MCP_ALLOWED_HOSTS` comme ce qui décide
« quels `Host` `/mcp` répond », et la configuration comme ce qui décide qui est
servi. C'est inexact. Cette liste alimente le `TransportSecurityMiddleware` du
SDK, qui compare l'en-tête `Host` (et `Origin`) à la liste. Cela protège contre
le *DNS rebinding* — une page ouverte dans un navigateur, qui fait résoudre son
propre nom vers notre machine, mais ne peut pas choisir le `Host` que le
navigateur envoie. Cela ne protège de rien d'autre : **l'en-tête `Host` est
écrit par l'appelant**, et un script sur le réseau envoie
`Host: localhost:8000` sans effort.

```bash
curl -H 'Host: localhost:8000' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",...}' http://192.168.1.x:8000/mcp
```

Cette requête, venue d'une autre machine, était servie.

## Décision

**Tant que l'authentification n'existe pas, `/mcp` ne sert que les clients dont
le pair TCP est la boucle locale.** Les autres reçoivent un 403, dans
l'enveloppe d'erreur de l'API :

```json
{"error": "remote_client_refused", "detail": "/mcp only answers clients on this machine ..."}
```

| Question | Réglage | Défaut |
|---|---|---|
| Sur quelle interface on écoute | `EA_HOST` | `0.0.0.0` |
| Quels navigateurs l'API REST sert | `EA_CORS_ORIGINS` | `http://localhost:5173` |
| **Qui peut appeler un outil MCP** | `EA_MCP_ALLOW_REMOTE_CLIENTS` | `false` |
| Quels `Host` `/mcp` accepte (anti-rebinding) | `EA_MCP_ALLOWED_HOSTS` | boucle locale |

- Le pair est lu dans `scope["client"]` de l'ASGI — l'adresse que uvicorn
  rapporte pour la socket, la seule chose qu'un appelant n'écrit pas lui-même.
  `127.0.0.0/8`, `::1` et leur forme `::ffff:127.x.y.z` sont la boucle locale.
  Un pair absent ou qui n'est pas une adresse est refusé : sur un chemin
  d'écriture, ce qu'on ne peut pas vérifier ne passe pas.
- Le contrôle **enveloppe la route** du transport (`mcp/transport.py`,
  `LoopbackClientsOnly`) plutôt qu'un middleware qui comparerait le chemin : il
  ne peut pas diverger du chemin réellement servi, et il s'exécute avant que le
  SDK ouvre une session.
- `EA_MCP_ALLOW_REMOTE_CLIENTS=true` lève la restriction, explicitement, pour un
  déploiement qui l'assume. Un agent distant doit alors *aussi* avoir son `Host`
  dans `EA_MCP_ALLOWED_HOSTS`.
- La liste des `Host` est **conservée** : elle reste la défense contre le DNS
  rebinding, qui vise justement un client local — le navigateur du poste.
- L'API REST n'est pas concernée : le SPA est utilisé depuis d'autres machines.

`.mcp.json` pointe Claude Code vers `http://127.0.0.1:8000/mcp` : il continue de
fonctionner sans rien changer.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder la seule liste de `Host` | Rien à écrire | Un en-tête que l'appelant choisit n'autorise personne | Écarté |
| Revenir à `EA_HOST=127.0.0.1` | Supprime l'exposition | Rend l'API REST et le SPA injoignables du réseau, ce que `0016` a voulu | Écarté |
| `EA_MCP_ENABLED=false` par défaut | Plus aucun chemin d'écriture non authentifié | Prive l'agent local, le cas d'usage de `0014`, sans raison : lui n'est pas exposé | Écarté |
| Liste d'adresses IP autorisées | Plus fin qu'un booléen | Une IP de réseau local n'est pas une identité (DHCP, NAT, proxy) ; donne l'illusion d'une auth | Écarté |
| Jeton partagé dans un en-tête | Une vraie preuve d'identité | C'est le début de l'auth, qui mérite sa propre décision (JWT, rafraîchissement, `argon2`) plutôt qu'un secret statique de plus | Reporté à l'ADR d'auth |
| Faire confiance à `X-Forwarded-For` | Fonctionnerait derrière un proxy | Encore un en-tête écrit par l'appelant, sauf à configurer des proxys de confiance | Écarté |

## Conséquences

- **Un agent sur une autre machine reçoit 403.** Pour le servir : un tunnel SSH
  (`ssh -L 8000:127.0.0.1:8000 hôte`), qui fait de lui un client local, ou
  `EA_MCP_ALLOW_REMOTE_CLIENTS=true` plus son `Host` dans
  `EA_MCP_ALLOWED_HOSTS` — en acceptant que tout le réseau puisse alors écrire.
- **Derrière un reverse proxy, le pair est le proxy.** Le proxy sur la même
  machine rend tout client « local » : un déploiement derrière un proxy doit
  soit ne pas exposer `/mcp` par le proxy, soit attendre l'auth. Le réglage le
  dit, `.env.example` aussi.
- **Un serveur ASGI qui ne rapporte pas le pair** (socket Unix, par exemple)
  voit `/mcp` refuser tout le monde. C'est volontaire ; l'ouverture explicite
  reste possible.
- **L'authentification reste à écrire.** Cette décision n'en est pas une : elle
  réduit l'exposition à la machine, elle ne dit pas *qui* est l'appelant.
  L'ADR d'auth devra dire si `EA_MCP_ALLOW_REMOTE_CLIENTS` disparaît avec elle.
- **Le paragraphe « trois listes d'autorisation » de `CLAUDE.md`** doit être
  réécrit : c'est le pair, et non `EA_MCP_ALLOWED_HOSTS`, qui décide qui appelle
  `/mcp`.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md),
  [0016](0016-ecoute-sur-toutes-les-interfaces.md)
- Code concerné : `backend/src/ea/mcp/transport.py`, `backend/src/ea/main.py`
  (`_mount_mcp`), `backend/src/ea/core/config.py` (`mcp_allow_remote_clients`)
- Tests : `backend/tests/unit/test_mcp_transport.py`,
  `backend/tests/e2e/test_mcp_endpoint.py::TestWhoIsServed`
