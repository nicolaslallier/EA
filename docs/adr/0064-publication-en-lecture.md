---
titre: Publication en lecture par un mode lecteur du SPA
date: 2026-10-11
statut: Proposition
affects: frontend/src/features/view/, frontend/src/router/sections.ts, frontend/src/router/index.ts, frontend/src/App.vue, backend/src/ea/api/documents.py
---

# 64. Publication en lecture par un mode lecteur du SPA

Date : 2026-10-11
Statut : Proposition

## Contexte

Des personnes ont besoin de lire le modèle (paquets, éléments, relations, diagrammes, documents) sans l'application complète ni le droit de la modifier : l'équivalent de WebEA pour Sparx. Le dépôt n'a aucune route anonyme (`require_caller` partout, ADR 0032) et aucune raison d'en ouvrir une pour cela.

## Décision

1. **Un mode lecteur du SPA** : routes `/view/*` dans la même application, même origine, même authentification `oidc-client-ts`, mêmes endpoints. Aucun jeton de plus, aucune surface réseau de plus. C'est une section du menu (« Consultation », groupe « Lecture »), dont la mise en page retire le menu d'édition (`route.meta.reader`).
2. **Aucune écriture dans `/view/*`** : ni bouton, ni appel `POST`/`PUT`/`PATCH`/`DELETE` ; testé écran par écran (les appels enregistrés sont tous des `GET`) et par construction (un test lit les sources de `features/view/` et refuse tout verbe d'écriture du client). La boîte de rapport y est en téléchargement seul. **Cela ne protège rien à elle seule : l'API reste l'autorité** — chaque lecture traverse `require_caller`, et un lecteur qui forgerait une écriture reçoit un 403 du service.
3. **L'état est dans l'URL** (règle du dépôt) : le sujet est dans le chemin (`/view/package/:id`, `/view/element/:id`, `/view/diagram/:id`), `?relation=` et `?q=` dans la requête.
4. **Le dessin d'un diagramme est celui du serveur** (ADR 0063), récupéré avec l'en-tête du login et affiché depuis une URL de blob dans une balise `<img>` : un `<img src>` ne peut pas porter de jeton, et un jeton ne va jamais dans une URL ; une image SVG ne peut pas exécuter de script. Sous le dessin, la liste des éléments (chacun un lien) est la lecture accessible, au clavier.
5. **Les documents markdown sont rendus par le serveur** (`GET /documents/{id}/html`, ADR 0062) : HTML brut désactivé, images désactivées, schémas `javascript:`/`data:` refusés, réponse avec politique de contenu fermée et `nosniff`. Le SPA l'insère avec `v-html` seulement parce que cette sortie est neutralisée et testée.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Site statique exporté | Hébergeable partout | Copie périmée dès l'écriture suivante ; droits figés à la génération ; un second rendeur à tenir | Écarté |
| Lien partageable à jeton | Lecteurs sans compte | Amende « tout appelant est authentifié » (ADR 0032) : jeton, expiration, révocation, audit, route anonyme | Écarté tant qu'aucun besoin externe n'est exprimé ; s'il vient : partager un rapport déposé, jamais le modèle vivant |
| Redirection automatique des comptes sans `ea-editor` vers `/view` | Atterrissage adapté | Rend l'arrivée sur `/` asynchrone (un appel `/me`) | Reportée ; `/view` est un choix du menu |

## Conséquences

- Les lecteurs ont un compte du realm `ea` : pas de partage externe en l'état. « Envoyer à un client » = télécharger un rapport et le transmettre soi-même.
- `/view/diagram` appelle un rendu à chaque ouverture ; `ETag` et `Cache-Control: private, no-cache` évitent de retransmettre un dessin inchangé, sans cache serveur partagé.
- La page d'un paquet liste ses éléments directs (une page de 100) ; au-delà, elle renvoie au catalogue.
- Le mode lecteur ne porte pas la date `?asof=` jusque dans ses lectures : il montre le modèle d'aujourd'hui.
- Ce qui n'est pas vérifié par un navigateur : les écrans ont été testés avec Testing Library et jsdom, jamais vus dans un navigateur.

## Références

- ADR liés : [0008](0008-menu-et-routage-du-spa.md), [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md), [0032](0032-authentification-par-keycloak.md), [0036](0036-fichiers-dans-minio.md), [0039](0039-metadonnees-des-fichiers-dans-postgresql.md), [0062](0062-generation-de-documents.md), [0063](0063-un-seul-dessin-de-diagramme.md)
- Code concerné : `frontend/src/features/view/`, `backend/src/ea/api/documents.py`
