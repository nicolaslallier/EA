---
titre: Le serveur Vite écoute sur toutes les interfaces, et l'URL de l'API suit la page
date: 2026-09-08
statut: Accepté
affects: frontend/vite.config.ts, frontend/src/lib/api.ts, frontend/.env.example, backend/.env.example, Makefile
---

# 22. Servir le SPA depuis le réseau local

Date : 2026-09-08
Statut : Accepté

Termine ce que [`0016`](0016-ecoute-sur-toutes-les-interfaces.md) avait
explicitement laissé ouvert : « Le serveur Vite, lui, écoute toujours en local.
Servir le SPA depuis un autre poste demandera `vite --host`, qui n'est pas
décidé ici. »

## Contexte

L'API écoute `0.0.0.0` depuis `0016`. Le SPA, non : Vite écoute `localhost` par
défaut, donc l'écran n'existait que sur le poste qui le compilait. Ouvrir le
catalogue depuis un portable, un téléphone ou le poste d'un collègue était
impossible alors même que l'API, elle, leur répondait déjà.

Le changement paraît tenir en un drapeau, `--host`. Il n'y tient pas, pour deux
raisons.

**L'URL de l'API était une constante.** `src/lib/api.ts` repliait sur
`http://localhost:8000`. Cette URL est résolue *par le navigateur*, donc depuis
un autre poste elle désigne le `localhost` **de ce poste-là** — une machine qui
ne fait tourner aucun backend. Le SPA s'affichait et chaque appel échouait sur
« Backend injoignable », en désignant l'API comme fautive.

**Vite a la même mécanique de protection que le SDK MCP, en sens inverse.**
`server.allowedHosts` refuse un nom de domaine arbitraire qui pointerait vers
cette adresse — c'est la protection anti-*DNS rebinding*, et par défaut elle
laisse passer les noms de bouclage et les IP littérales, ce qui est exactement
ce dont on a besoin. La régler à `true` l'aurait désactivée au moment précis où
le serveur cesse d'être local, la faute que `0016` a évitée côté MCP.

## Décision

**Le serveur Vite écoute `0.0.0.0`, et l'URL par défaut de l'API est déduite de
la page plutôt qu'écrite en dur.**

| Question | Réglage |
|---|---|
| Sur quelle interface le SPA est servi | `vite.config.ts` → `server.host: true`, surchargeable par `make run-fe FE_HOST=…` |
| Quels `Host` Vite accepte | `server.allowedHosts`, laissé à son défaut (bouclage + IP littérales) |
| Quelle API le SPA appelle | l'hôte de la page, port 8000 — `VITE_API_BASE_URL` si ce n'est pas ça |
| Quels navigateurs le backend sert | `EA_CORS_ORIGINS`, inchangé et toujours pas déduit |

`defaultApiBaseUrl()` est une fonction pure qui prend `protocol` et `hostname` :
elle se teste sans monter quoi que ce soit, et garde le schéma, pour qu'une page
servie en TLS ne bascule jamais sur du HTTP en clair.

La règle de `0016` tient donc pour les deux serveurs : **l'adresse d'écoute
n'autorise personne.** Un poste voisin charge le SPA, et ses appels sont refusés
par CORS tant que son origine n'est pas dans `EA_CORS_ORIGINS`. Comme une
origine est une chaîne exacte — le validateur refuse `*` —, `make run-fe`
affiche l'IP LAN et l'origine à coller, pour que l'étape soit visible au lieu
d'être devinée.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| `--host` dans `run-fe` seul | Une ligne | `npm run dev` et `make run-fe` cessent de faire la même chose ; l'URL de l'API reste fausse | Écarté |
| Garder `http://localhost:8000` et exiger `VITE_API_BASE_URL` | Explicite | Un fichier à écrire sur chaque poste, pour une valeur que la page connaît déjà ; oublié, l'erreur accuse l'API | Écarté |
| Proxy Vite `/api` → backend | Plus de CORS du tout, une seule origine | Cache une différence réelle en dev, et disparaît en production où le SPA est du statique ; contredit le commentaire de `api.ts` sur l'URL absolue | Écarté pour l'instant |
| `server.allowedHosts: true` | Marche derrière n'importe quel nom | Désactive la protection anti-rebinding quand elle devient utile | Écarté |
| Élargir `EA_CORS_ORIGINS` par défaut (ex. `192.168.1.*`) | Rien à configurer | Le validateur refuse le joker, et à raison : une origine est exacte | Écarté |

## Conséquences

- **Le SPA s'ouvre depuis n'importe quel poste du réseau**, et ses appels
  partent vers l'API du même hôte.
- **Un poste voisin reçoit un refus CORS** jusqu'à ce que son origine soit
  ajoutée. C'est le refus voulu, et `make run-fe` dit quoi coller.
- **Le serveur de dev est exposé au LAN.** C'est du code source et une carte de
  l'architecture, sur un réseau de confiance ; le SPA ne détient aucun secret
  (`.env.example` le rappelle : seules des variables `VITE_` y arrivent).
  L'auth reste la prochaine chose à écrire, pour l'API comme pour lui.
- **`vite preview` et un déploiement statique n'héritent de rien de tout ceci** :
  `server` ne les concerne pas, et `defaultApiBaseUrl` les sert justement bien,
  puisque l'API y est sur l'hôte de la page.

## Références

- ADR liés : [0016](0016-ecoute-sur-toutes-les-interfaces.md),
  [0007](0007-client-openapi-genere-pour-le-spa.md)
- Code concerné : `frontend/vite.config.ts`, `frontend/src/lib/api.ts`
  (`defaultApiBaseUrl`), `frontend/tests/api.spec.ts`
