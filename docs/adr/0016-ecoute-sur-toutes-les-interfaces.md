---
titre: L'API écoute sur toutes les interfaces, et les listes d'autorisation cessent d'en découler
date: 2026-09-07
statut: Accepté
affects: backend/src/ea/core/config.py, backend/src/ea/main.py, backend/.env.example, Makefile
---

# 16. Écouter sur 0.0.0.0, sans élargir qui est servi

Date : 2026-09-07
Statut : Accepté

Complète [`0014`](0014-serveur-mcp-pour-les-agents.md), qui a monté `/mcp` sur
l'API sans dire sur quelle interface celle-ci écoute.

## Contexte

Le backend écoutait `127.0.0.1`. C'est intenable dès qu'autre chose que le poste
de développement appelle l'API : un navigateur sur une autre machine, le
cluster, un téléphone sur le même réseau.

Le changement paraît tenir en une valeur. Il n'y tient pas, à cause d'une
mécanique du SDK MCP :

```python
if transport_security is None and host in ("127.0.0.1", "localhost", "::1"):
    transport_security = TransportSecuritySettings(...)   # protection activée
```

Le SDK n'active sa protection anti-*DNS rebinding* que si l'hôte qu'on lui donne
est un hôte de bouclage. `main._mount_mcp` lui passait `settings.host`. Faire
passer `EA_HOST` à `0.0.0.0` **désactivait donc la protection**, en silence,
au moment exact où elle devenait utile — sur `/mcp`, qui reste jusqu'à l'auth un
chemin d'*écriture* non authentifié sur le graphe. Un test le montre : la même
requête portant `Host: evil.example:8000` recevait 200 au lieu de 421.

## Décision

**L'API écoute `0.0.0.0`, et plus aucune liste d'autorisation n'est déduite de
cette adresse.**

| Question | Réglage |
|---|---|
| Sur quelle interface on écoute | `EA_HOST`, désormais `0.0.0.0` |
| Quels navigateurs sont servis | `EA_CORS_ORIGINS` |
| Quels `Host` `/mcp` répond | `EA_MCP_ALLOWED_HOSTS` |

`mcp_allowed_hosts` vaut par défaut la liste de bouclage que le SDK aurait
choisie (`127.0.0.1:*`, `localhost:*`, `[::1]:*`) : le comportement de `/mcp` ne
change pas, seule sa cause change — il est énoncé au lieu d'être déduit.
`_transport_security()` construit les `TransportSecuritySettings` et les passe
explicitement, avec `enable_dns_rebinding_protection=True` écrit noir sur blanc.

Les origines sont dérivées des hôtes plutôt que configurées deux fois : une
entrée est un hôte et un port éventuel, et un navigateur l'atteint par l'un des
deux schémas.

Le validateur qui refusait déjà `*` dans `cors_origins` couvre la nouvelle
liste. `localhost:*` reste légal — le joker porte sur le port, l'hôte est nommé.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Changer `EA_HOST` seul | Une ligne | Supprime la protection anti-rebinding sur un chemin d'écriture non authentifié | Écarté |
| Garder `127.0.0.1` et publier via un reverse proxy | Rien à changer côté application | Une brique de plus à installer et configurer pour un réseau local de confiance | Écarté pour l'instant |
| Passer `EA_MCP_ENABLED=false` dès que l'API n'est plus en bouclage | Supprime le risque à la racine | Prive l'agent du référentiel, qui est justement ce que `0014` a construit | Écarté |
| Déduire l'autorisation MCP de `EA_CORS_ORIGINS` | Une liste au lieu de deux | Ce ne sont pas les mêmes clients : un agent n'envoie pas d'`Origin`, un navigateur n'est pas un agent | Écarté |

## Conséquences

- **L'API est joignable depuis le réseau local.** C'est le but ; c'est aussi la
  raison pour laquelle l'auth devient la prochaine chose à écrire.
- **Un agent sur une autre machine reçoit 421** tant que son `Host` n'est pas
  ajouté à `EA_MCP_ALLOWED_HOSTS`. C'est le refus voulu, pas une panne — le
  code de statut le dit.
- **Un navigateur sur une autre machine reçoit un refus CORS** tant que son
  origine n'est pas dans `EA_CORS_ORIGINS`. Même logique, réglage distinct.
- **Le serveur Vite, lui, écoute toujours en local.** Servir le SPA depuis un
  autre poste demandera `vite --host`, qui n'est pas décidé ici.
- **À surveiller :** `EA_MCP_ALLOWED_HOSTS` est la seule chose entre le LAN et
  un chemin d'écriture non authentifié. Elle cesse d'être la dernière ligne le
  jour où l'auth existe, et pas avant.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md)
- Code concerné : `backend/src/ea/main.py` (`_transport_security`),
  `backend/src/ea/core/config.py`
