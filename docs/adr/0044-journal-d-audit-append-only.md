---
titre: Journal d'audit append-only écrit par les services dans la transaction de l'écriture
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/audit.py, backend/src/ea/services/audit.py, backend/src/ea/services/architecture.py, backend/src/ea/services/documents.py, backend/src/ea/services/diagrams.py, backend/src/ea/services/packages.py, backend/src/ea/services/saved_queries.py, backend/src/ea/repositories/audit_store.py, backend/src/ea/api/audit.py, backend/src/ea/mcp/server.py, backend/migrations/versions/0011_audit_events.py, frontend/src/features/history
---

# 44. Journal d'audit append-only écrit par les services dans la transaction de l'écriture

Date : 2026-10-10
Statut : Proposition

## Contexte

Le catalogue sait ce qu'il contient, pas ce qui l'a fait changer. Les lignes de
log de `services/` (`action=created`, `docs/adr/0021`) sont jetables, non
interrogeables et sans état avant/après. Les agents MCP écrivent dans le même
graphe que le SPA et le pipeline : « qui a changé quoi, quand, par quelle
porte » est devenu une question d'exploitation. Le déploiement n'a qu'une base
PostgreSQL et un seul rôle propriétaire `ea` (`docs/adr/0029`, `0033`).

## Décision

- Une table `audit_events` (migration `0011`), `entity_id` **sans clé
  étrangère** : l'événement doit survivre à l'entité qu'il décrit.
- **Un événement par cas d'usage**, pas par ligne SQL. `delete_element` écrit un
  `element.deleted` dont `before` liste les relations, documents et diagrammes
  emportés par les clés étrangères : au plus cent identifiants par sorte de
  chose, avec le total exact à côté (`AUDIT_LISTED_IDS`).
- Les `AuditEvent` sont construits par `services/` (`AuditTrail`) : acteur du
  `Caller` vérifié, horloge injectée, `request_id` du contexte de log, canal
  (`http|mcp|system`) posé par l'adaptateur (`api/auth.py`, `speaking_plainly`).
  Jamais un paramètre d'appelant.
- **L'identifiant de requête du journal est celui du serveur**, le même que
  l'en-tête `X-Request-Id` de la réponse. L'identifiant que le client envoie
  éventuellement n'est qu'une étiquette : il est journalisé à part
  (`client_request_id`) et jamais écrit dans le journal, où il permettrait de
  forger la corrélation de requêtes qu'on n'a pas faites.
- Les dépôts reçoivent `audit=` (keyword-only, **obligatoire**) sur chacune de
  leurs écritures et l'insèrent par `write_events(session, events)` dans
  **leur** `begin()`, après le `flush` de l'écriture : l'événement existe si et
  seulement si l'écriture a tenu. Un refus (404, 409, 403, 422) n'écrit rien.
  Quand l'événement dépend de la ligne remplacée (`before`), `audit` est une
  fonction de cette ligne, lue **dans** la transaction, sous verrou : deux
  écrivains concurrents ne journalisent pas le même `before`, et un
  `update_element` applique un patch pur à la ligne lue sous verrou, de sorte
  que l'un n'annule pas l'autre.
- `tests/unit/test_audit_coverage.py` lit les sources (AST) et échoue sur une
  écriture de dépôt sans `audit`, sur un service qui l'appelle sans en nommer
  un, ou sur un `AuditEvent` construit hors de `AuditTrail`. La seule écriture
  permise sans événement porte un nom, `UNAUDITED_REINDEX` : réindexer réécrit
  les passages *dérivés* d'un document dont le texte n'a pas changé.
- `before`/`after` sont les vues de lecture des entités ; le texte d'un
  document n'est **jamais** copié (nom, taille, sha256). Les secrets n'y
  figurent pas.
- Immuabilité : triggers `BEFORE UPDATE OR DELETE` (par ligne) et `BEFORE
  TRUNCATE` qui lèvent.
- Rétro-remplissage dans la migration : un événement `element.created` /
  `relationship.created` par élément et lien existants, `ea-system`,
  `correlation='backfill'`, rejouable sans doublon. Les diagrammes, documents,
  paquets et requêtes enregistrées existants n'en reçoivent pas.
- **Lecture** : `GET /audit` (filtres `entity_kind`, `entity_id`, `actor`,
  `action`, `since` inclus, `until` exclu, `request_id`, `cursor`, `limit` ≤
  200), `GET /elements/{id}/history`, outils MCP `list_audit_events` et
  `get_element_history` (annotés lecture seule), écran SPA « Historique » et
  panneau sur la fiche d'un élément. **Tout appelant authentifié lit**
  (`require_caller()` dans `AuditService.list_events`) : l'historique n'est pas
  plus sensible que le catalogue qu'il décrit, et restreindre la lecture serait
  une seule garde à changer. Sans jeton : 401. Aucune route ni aucun outil
  n'écrit dans `audit_events`.
- Les bornes des filtres (`Actor`, `RequestIdFilter`, `CursorToken`, `Limit`)
  sont déclarées une fois dans `api/schemas.py` et importées par `mcp/` ; un
  NUL est refusé à la porte, parce que PostgreSQL ne sait pas le comparer et
  répondrait par un 500. Un curseur forgé ou hors plage est un `422
  invalid_cursor` ; un instant dont la forme UTC sort des dates représentables
  est un 422. L'ordre est `at desc, id desc`, la pagination par curseur `(at,
  id)` opaque.
- Dans le SPA, un `before`/`after` est affiché comme texte (jamais `v-html`) :
  une valeur longue est coupée à 300 caractères avec sa longueur, et dépliable.
  Les types d'entités et d'actions viennent du schéma généré ; les libellés
  sont des tables de traduction exhaustives (`Record`), sans règle.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Triggers PostgreSQL qui écrivent l'audit | Rien à oublier côté code | Ne connaissent ni l'acteur du jeton, ni le canal, ni le cas d'usage ; un événement par ligne | Écartée |
| Audit écrit après coup par le service (2e transaction) | Aucun changement de dépôt | Écriture et trace peuvent diverger au crash | Écartée |
| `REVOKE UPDATE, DELETE` seul | Standard | Sans effet sur le rôle propriétaire unique | Écartée (trigger ; rôle séparé à faire hors de ce repo) |
| Chaîne de hachage du journal | Détecte une falsification par un opérateur | Hors périmètre ; `pg_dump` suffit pour l'instant | Écartée |
| Lecture réservée à `ea-editor` | Moins d'exposition | Les lecteurs lisent déjà tout le catalogue | Écartée (une garde à changer si cela évolue) |

## Conséquences

Limites, dites sans détour :

- **L'immuabilité n'est qu'un garde-fou.** Le trigger se déclenche pour tout
  rôle, propriétaire compris, mais le propriétaire peut le `DROP` ou le
  `DISABLE`, et un superutilisateur le contourne par
  `session_replication_role = replica`. Comme le seul rôle du
  déploiement est propriétaire (`ea`), la garantie réelle aujourd'hui est
  « aucune modification par erreur ou par bogue applicatif », pas « aucune
  falsification par quelqu'un qui tient le rôle ». Elle ne devient plus forte
  qu'avec un rôle d'écriture `INSERT, SELECT` seul sur `audit_events` : **à
  faire hors de ce repo** (dépôt Infra, `make provision-app`).
- **L'historique par élément ne voit pas un changement de paquet.**
  `package.elements_moved` a pour `entity_id` le paquet de destination (ou
  `null` pour la racine) ; `GET /elements/{id}/history` filtre
  `entity_kind=element&entity_id=` à la lettre. Un re-parentage ou un
  `move_elements` se retrouve par `?kind=package` ou par `request_id`, pas dans
  la fiche de l'élément. Même chose pour les événements `document.*`, dont le
  `entity_kind` est `document`.
- **Pas d'événement pour une mise à jour sans changement** : un `PATCH` qui
  réécrit les valeurs qu'il y avait déjà n'écrit rien.
- **L'ordre de pagination sous création concurrente** n'est pas un instantané :
  `at` est pris dans l'écriture, avant son commit : deux transactions
  concurrentes peuvent committer dans un ordre différent de leur `at`, et une
  page déjà servie ne montrera pas un événement committé après coup avec un
  `at` plus ancien. La
  pagination ne double ni ne perd un événement existant au moment de la lecture ;
  elle ne promet rien sur ceux qui arrivent pendant qu'on pagine.
- **Les tests de concurrence acceptent plusieurs issues** (aucune barrière ne
  force l'entrelacement) : ils prouvent que le résultat est sain pour chaque
  ordre qui s'est présenté, pas que tous les ordres ont été joués.
- **Exclus avec leur raison.** `file_metadata` : c'est la fiche d'un objet de
  MinIO (`docs/adr/0039`), pas du modèle, et la table est elle-même le registre
  de qui a déposé quoi. `graph_import` : copie unique depuis Neo4j
  (`docs/adr/0033`), supprimée après la bascule. La réindexation, vue plus haut.
- Les écritures de dépôt ouvrent chacune leur transaction : l'atomicité couvre
  *une* écriture et son événement, pas plusieurs cas d'usage. Un cas d'usage à
  plusieurs écritures dans une même transaction (échanges, restauration)
  demandera une interface commune de session partagée, à concevoir une seule
  fois avec lui.
- **Rétention : aucune purge automatique.** Volume de l'ordre de 2 Ko par
  événement ; la table grossit sans borne. Une purge est une décision
  d'opérateur, hors de ce repo, et doit passer par un rôle qui a le droit de
  désactiver le trigger — c'est voulu.
- `request_id` vaut `-` hors requête (script `ea.reindex`).
- Le SPA n'a pas été vérifié à l'écran : Keycloak était injoignable dans
  l'environnement où il a été écrit. Il est couvert par Vitest et par les
  tests d'API, pas par un navigateur.

## Références

- ADR liés : [0021](0021-des-logs-que-quelqu-un-peut-lire.md), [0029](0029-nouvelle-adresse-du-cluster-et-postgresql-sur-le-mac.md), [0032](0032-authentification-par-keycloak.md), [0033](0033-postgresql-seul-pour-le-graphe.md), [0039](0039-metadonnees-des-fichiers-dans-postgresql.md)
- Code : `backend/src/ea/domain/audit.py`, `backend/src/ea/repositories/audit_store.py`, `backend/migrations/versions/0011_audit_events.py`, `frontend/src/features/history/`
