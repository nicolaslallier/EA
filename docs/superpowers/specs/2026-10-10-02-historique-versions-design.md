# Sous-projet 2 — Historique et versions (audit, baselines, approbation) — design

Date : 2026-10-10. Statut : proposition. Couvre B5 (journal d'audit), B4 (baselines), B6 (verrouillage et approbation).

## 1. Intention

**Dit (demande) :** savoir qui a changé quoi et quand (B5) ; figer des instantanés du modèle, les comparer et restaurer (B4) ; faire passer un élément par brouillon → en revue → approuvé, et le verrouiller (B6). Les outils MCP obéissent aux mêmes règles. Les décisions transverses sont fixées : audit écrit par `services/` dans la même transaction, table append-only `audit_events` ; baseline limitée à un paquet ou globale ; restauration éditeur explicite, transactionnelle, auditée ; nouveau rôle Keycloak éventuel.

**Hypothèses (retenues, à contester) :**
- Le volume reste petit (centaines à quelques milliers d'éléments, quelques écritures par minute au pire) : le stockage et les requêtes naïves suffisent.
- Une seule base PostgreSQL : audit, baselines et état d'approbation y vivent (ADR 0033), rien dans MinIO.
- L'audit couvre le graphe (éléments, relations), les diagrammes, les documents et les baselines elles-mêmes ; pas les fichiers MinIO (leur `file_metadata` porte déjà l'uploader).
- Le sous-projet 1 est livré avant les lots B4 « par paquet » : sans lui, une baseline est globale seulement.

## 2. Périmètre

**Dans :** table `audit_events` et sa lecture (filtrée, paginée) ; instantanés, liste, diff champ par champ, restauration ; états d'approbation, verrou, rôle `ea-approver` ; écrans SPA Historique, Baselines, bandeau d'état sur la fiche élément ; outils MCP de lecture de l'audit/baselines et de transition d'état.

**Hors :** annuler (undo) une écriture isolée depuis l'audit ; branches/fusion de modèles ; signature cryptographique ou chaîne de hachage du journal (cf. risque R2) ; notifications (mail/chat) de demande de revue ; approbation des relations, diagrammes et documents (l'élément seul porte l'état) ; export du journal vers un SIEM ; rétention automatique (purge) — seulement décrite.

## 3. Modèle de domaine

Nouveaux modules purs dans `domain/` (aucun import framework) :
- `domain/audit.py` : `AuditEvent` (valeur immuable : `id`, `at`, `actor_sub`, `actor_name`, `action`, `entity_kind`, `entity_id`, `before`, `after`, `request_id`), `AuditAction` (énumération fermée : `element.created|updated|deleted`, `relationship.created|deleted`, `diagram.*`, `document.*`, `element.submitted|approved|rejected|reopened|locked|unlocked`, `baseline.created|deleted|restored`), `diff_fields(before, after)` pur (liste `FieldChange(field, before, after)`).
- `domain/baselines.py` : `Baseline` (id, nom, description, `package_id | None`, créé par/à), `ModelSnapshot` (éléments + relations en valeurs), `BaselineDiff` (`added`, `removed`, `modified` pour éléments et relations ; `modified` = liste de `FieldChange`), `plan_restore(snapshot, current)` pur → `RestorePlan` (à créer, à mettre à jour, à supprimer, avertissements).
- `domain/approval.py` : `ApprovalState` = `draft | in_review | approved` ; `locked: bool` ; table de transitions pure `allowed_transitions(state, roles)` ; `Caller` gagne `can_approve` (rôle `APPROVER_ROLE = "ea-approver"` dans `domain/auth.py`).

**Granularité de l'audit : un événement par use case**, pas par ligne SQL. `delete_element` écrit un événement `element.deleted` dont `before` contient l'élément ; les relations et boîtes emportées par cascade FK sont listées dans `before.cascaded` (ids et types), pas en événements séparés. Une restauration de baseline écrit un événement `baseline.restored` (résumé du `RestorePlan`) puis un événement par élément/relation touché, tous avec le même `request_id` et un champ `correlation` (`restore:<baseline_id>`).

**Vues de lecture de l'état :** `before`/`after` portent la représentation de lecture de l'entité (champs du domaine, `properties` inclus), jamais de secret ; le contenu des documents (`TEXT`) n'est pas copié : l'événement garde nom, taille et sha256 du texte avant/après.

## 4. Données

Migration `audit_events` (nom proposé : « journal d'audit ») :
```
audit_events(id uuid pk, at timestamptz not null, actor_sub text not null, actor_name text not null,
             action text not null, entity_kind text not null, entity_id uuid null,
             before jsonb null, after jsonb null, request_id text not null, correlation text null)
index (entity_kind, entity_id, at desc) ; index (at desc) ; index (actor_sub, at desc)
```
`entity_id` **sans FK** : l'événement doit survivre à la suppression de l'entité (comme `file_metadata`, mais ici volontaire). `at` vient de l'horloge injectée du service.

**Immuabilité — tranchée : droits retirés, pas de trigger.** La migration crée le rôle d'écriture applicatif avec `INSERT, SELECT` seuls sur `audit_events` (`REVOKE UPDATE, DELETE, TRUNCATE`), et ajoute en plus un trigger `BEFORE UPDATE OR DELETE` qui lève une exception : le trigger couvre le cas où l'application tourne sous le rôle propriétaire (c'est le cas du déploiement actuel : un seul rôle `ea`, qui possède les tables). Donc : trigger = garantie effective aujourd'hui ; REVOKE = ceinture dès qu'un rôle propriétaire distinct existera. Les tests d'intégration vérifient que `UPDATE`/`DELETE` échouent. La rétention (R3) passe par une procédure opérateur explicite qui désactive le trigger, hors application.

**Transaction commune.** Les dépôts ouvrent aujourd'hui une unité de travail par appel (`async with self._sessions.begin()`). Décision : les méthodes d'écriture du dépôt (`save_element`, `delete_element`, `add_relationship`, `delete_relationship`, écritures de diagrammes et documents) reçoivent un paramètre `audit: tuple[AuditEvent, ...]` construit par le service, et le dépôt les `INSERT` dans **la même** `begin()` via un petit `AuditWriter` (`repositories/audit_store.py`) appelé avec la session en cours. Le service décide *quoi* écrire ; le dépôt ne fait que l'insérer atomiquement. Le routeur n'y touche jamais. Pour `delete_element`, le service lit l'élément et ses liens avant (déjà fait pour 404) ; un test garantit l'atomicité (écriture refusée ⇒ aucun événement).

**Baselines — représentation retenue : copie normalisée**, tables `baselines`, `baseline_elements`, `baseline_relationships`, `baseline_boxes` (boîtes de diagrammes : optionnel, voir R5). Chaque ligne reprend les colonnes de `elements`/`relationships` + `baseline_id` (FK `ON DELETE CASCADE`), clé `(baseline_id, id)`. Justification : le diff devient un `FULL OUTER JOIN` SQL ou une comparaison de deux ensembles en mémoire sur des colonnes typées ; l'espace est de l'ordre de la taille du graphe par baseline (acceptable, hypothèse volume) ; une baseline reste lisible même si l'audit est purgé. Écartés : **JSONB unique** (simple à écrire, mais diff et recherche d'un élément dans une baseline passent par du JSON, pas d'index, la ligne grossit sans borne) ; **reconstruction depuis l'audit** (zéro stockage dupliqué, mais exact seulement depuis le début du journal : le modèle existant n'a pas d'historique — il faudrait un rétro-remplissage fictif — et une purge de rétention casserait les baselines). La baseline stocke aussi `package_id` (nullable) et, pour un paquet, l'ensemble des ids du sous-arbre au moment de la capture.

**Approbation.** Colonnes ajoutées à `elements` : `approval_state text not null default 'draft'` (CHECK sur les trois valeurs), `locked boolean not null default false`, `approved_by_sub text null`, `approved_at timestamptz null`. **Hors de `properties`** : ni l'unicité `(type, nom)`, ni les index IPAM (`properties ->> 'vrf'/'ip_address'/'cidr'`) ne sont touchés, et `PATCH` qui remplace `properties` ne peut pas effacer l'état. Un `CHECK` impose `locked ⇒ approval_state = 'approved'`.

**Rétro-remplissage de l'existant :** (a) éléments existants → `draft`, non verrouillés (pas d'approbation rétroactive inventée) ; (b) `audit_events` reçoit, dans la migration de données, un événement `element.created`/`relationship.created` par ligne existante avec `actor_sub='ea-system'`, `at = created_at`, `after` = état courant et `correlation='backfill'` — pour que l'historique ne commence pas par un « trou » ; (c) aucune baseline créée automatiquement (l'opérateur en fait une avant la mise en service : `make` cible prévue).

## 5. API

Routeurs `api/audit.py`, `api/baselines.py`, extension de `api/architecture.py`. Bornes dans `api/schemas.py` (pages, longueurs de nom), importées par MCP.
| Méthode | Chemin | Rôle | Réponse |
|---|---|---|---|
| GET | `/audit` | lecteur | page `AuditEventRead` ; filtres `entity_kind`, `entity_id`, `actor`, `action`, `since`, `until`, `request_id`, `cursor`/`limit` (tri `at desc`, pagination par curseur `(at,id)`) |
| GET | `/elements/{id}/history` | lecteur | raccourci `/audit?entity_kind=element&entity_id=` |
| GET/POST | `/baselines` | lecteur / éditeur | liste ; création `{name, description, package_id?}` → `201` ; `409` nom dupliqué |
| GET/DELETE | `/baselines/{id}` | lecteur / éditeur | résumé (compteurs) ; `204` |
| GET | `/baselines/{id}/diff?against=<baseline_id\|current>` | lecteur | `BaselineDiffRead` ; `against` omis = modèle courant |
| POST | `/baselines/{id}/restore` | éditeur | corps `{dry_run: bool=true, confirm: bool}` ; `dry_run` renvoie le `RestorePlan` sans écrire ; sinon applique, `200` résumé ; `409` si un élément verrouillé serait touché |
| POST | `/elements/{id}/approval` | selon transition | corps `{transition: submit\|approve\|reject\|reopen\|lock\|unlock}` → `ElementRead` ; `409` transition interdite |
Les lectures d'`ElementRead` gagnent `approval_state`, `locked`, `approved_by`, `approved_at`. Le schéma OpenAPI et le client sont régénérés (`make openapi`).

**Restauration — comportement.** Opération unique en une transaction, sous le même verrou de session que les autres écritures : (1) éléments présents dans la baseline et absents du modèle : recréés avec **leur id d'origine** (les diagrammes et documents désormais orphelins ne peuvent être récupérés — voir ci-dessous) ; (2) éléments modifiés depuis : champs remis à la valeur de la baseline (`name`, `description`, `documentation`, `properties`) ; (3) éléments créés *depuis* la baseline : **supprimés par défaut** si la restauration est « exacte », conservés si `keep_new=true` — défaut retenu : `keep_new=false` pour une baseline globale, `true` pour une baseline de paquet (on ne supprime pas hors du périmètre) ; leurs relations partent par cascade FK, leurs documents et boîtes aussi, avec la liste exacte affichée par le `dry_run` (« 3 éléments, 7 relations, 2 documents, 5 boîtes seront supprimés ») ; (4) relations : recréées/supprimées pour égaler la baseline sur le périmètre, sous contrôle de validité ArchiMate par `ArchitectureService` (une relation devenue illégale est un avertissement, pas un échec). Les documents d'un élément supprimé depuis ne reviennent pas (non capturés, voir R5) ; les boîtes de diagrammes sont restaurées seulement si `baseline_boxes` existe et si le diagramme existe encore. L'unicité IPAM/(type, nom) est vérifiée par les contraintes : un conflit annule toute la transaction avec un `409` listant le premier élément fautif. Aucun élément verrouillé n'est modifié ni supprimé : le plan les signale et la restauration est refusée (`409`) tant qu'un `unlock` n'a pas eu lieu — défaut sûr.

**Règles d'approbation :**
| De → vers | Qui |
|---|---|
| draft → in_review (`submit`) | éditeur |
| in_review → approved (`approve`) | `ea-approver` (et pas l'auteur de la dernière modification : séparation des tâches, voir R4) |
| in_review → draft (`reject`) | `ea-approver` |
| approved → draft (`reopen`) | `ea-approver` ; efface `approved_by/at` et `locked` |
| approved → locked (`lock`) / locked → approved (`unlock`) | `ea-approver` |
Tant qu'un élément est `approved`, `update_element` et `delete_element` sont refusés à un éditeur simple (`ElementLockedError`, 409) ; seul `ea-approver` peut modifier, et toute modification d'un élément `approved` le **repasse en `in_review`** (jamais `approved` silencieusement). `locked` interdit toute modification, même à l'approbateur, jusqu'à `unlock`. Les relations ajoutées/retirées à un élément verrouillé sont refusées ; à un élément seulement `approved`, autorisées mais l'élément repasse en `in_review` (hypothèse R6). Un élément `in_review` reste modifiable par un éditeur (la revue regarde l'état courant), l'événement d'audit le consigne.

## 6. MCP

Même fournisseur de service que les autres outils, `@speaking_plainly`, ajouts à la liste complète de `test_mcp_server.py` : `list_audit_events` (lecture, mêmes filtres/bornes que HTTP), `get_element_history`, `list_baselines`, `diff_baselines` (lecture), `create_baseline` (écriture, annotation non destructive), `restore_baseline` (**destructif**, annotation et docstring le disent ; `dry_run=true` par défaut, l'agent doit rappeler avec `dry_run=false`), `transition_element` (écriture ; refus du service si le jeton n'a pas `ea-approver`). Les outils d'écriture existants héritent du verrouillage via le service : `update_element` sur un élément `approved` renvoie un `ToolError` explicite. Les `INSTRUCTIONS` du serveur mentionnent : lire l'historique avant de réécrire un élément, toujours faire un `dry_run` avant une restauration. L'acteur dans l'audit est celui du jeton (`speaking_plainly` positionne déjà le `Caller`) ; un champ `channel` (`http|mcp|system`) est ajouté au `after.meta` pour distinguer l'origine.

## 7. SPA

Sections ajoutées à `router/sections.ts` (groupe « Gouvernance », menu et routes dérivés) :
- **Historique** `/history` : journal filtrable, état dans l'URL (`?kind=`, `?entity=`, `?actor=`, `?action=`, `?since=`, `?cursor=`), chaque ligne déplie le diff champ par champ (composant partagé `FieldDiff.vue`), lien vers l'élément ; chargements via `useLatestRequest()`.
- **Baselines** `/baselines` : liste, bouton « Capturer » (éditeur), sélection de deux entrées (`?a=&b=`, `b` absent = courant) → vue diff ajouté/supprimé/modifié en trois colonnes ; « Restaurer » ouvre une boîte de dialogue montrant le `dry_run` (comptes, éléments verrouillés bloquants) et exige une saisie de confirmation du nom de la baseline.
- **Fiche élément** (catalogue, `?element=`) : bandeau d'état (brouillon / en revue / approuvé / verrouillé) avec les seuls boutons de transition que `GET /me` autorise (le SPA masque, l'API décide ; `GET /me` expose `can_approve`), et onglet « Historique » de l'élément. Aucun état d'approbation dupliqué en TypeScript : les transitions permises viennent de la réponse `ElementRead.allowed_transitions`.
Les libellés d'actions d'audit sont une table de traduction (comme `LAYER_LABELS`), sans règle.

## 8. Sécurité

- Lecture de l'audit : tout appelant authentifié (`require_caller`) — hypothèse, voir R7. Création/suppression de baseline et restauration : `require_editor`. Transitions : nouvelle garde `require_approver()` dans `services/caller.py` (échoue fermé, 403) ; `tests/unit/test_service_guards.py` impose toujours un `require_*` en première instruction.
- **Nouveau rôle Keycloak `ea-approver`** : à ajouter au realm `ea` (rôle de royaume, mapper de rôles déjà exploité pour `ea-editor`) ; l'approbateur n'implique pas `ea-editor` (rôles cumulables). ADR 0032 est complété (un ADR nouveau « rôle d'approbation » le complète sans le réécrire) ; l'import du realm et la liste des rôles du jeton vérifié (`repositories/keycloak.py`) gagnent ce rôle. 0032 étant encore une Proposition (realm non importé), le rôle entre dans le même import. `LOCAL_DEVELOPER` et `SYSTEM` reçoivent aussi le rôle (debug seulement).
- Le journal ne contient jamais de jeton ni de mot de passe ; `before/after` sont des vues de lecture (pas les `properties` sensibles, il n'y en a pas par construction du modèle). Le `RedactingFilter` de logs reste distinct : l'audit est une donnée, pas un log.
- Vecteur d'abus : un éditeur falsifie un acteur ? Non, l'acteur vient du jeton vérifié via `current_caller`, jamais d'un paramètre. `ea.reindex` et les scripts agissent comme `SYSTEM`, visible dans le journal.
- Le trigger d'immuabilité empêche aussi une injection SQL d'effacer des traces par `UPDATE`/`DELETE`.

## 9. Tests

TDD, test d'échec d'abord.
- **Unit (`tests/unit`)** : `diff_fields`, `plan_restore` (tous les cas : créé depuis, modifié, supprimé, verrouillé, paquet), table de transitions par rôle, `ElementLockedError`, passage automatique `approved → in_review` à la modification ; garde AST (`test_service_guards`) ; borne des filtres HTTP = bornes MCP (`TestTheSameBoundsAsTheHttpAdapter`).
- **Services avec dépôt factice** : chaque use case d'écriture produit exactement un événement (action, acteur, avant/après) ; un refus (404, 409, 403) n'en produit aucun.
- **Intégration (PostgreSQL jetable)** : atomicité (échec simulé au milieu ⇒ ni écriture ni événement) ; `UPDATE`/`DELETE` sur `audit_events` rejetés ; migration de rétro-remplissage sur une base déjà peuplée puis retour arrière ; capture + diff sur grand graphe ; restauration complète avec élément ajouté depuis (cascade vers relations, documents, boîtes), conflit d'unicité IPAM ⇒ rollback total ; `CHECK locked ⇒ approved`.
- **API (`tests/e2e` backend)** : 401 sans jeton, 403 lecteur qui crée une baseline, 403 éditeur qui approuve, 409 sur transition interdite, pagination par curseur stable, `dry_run` sans écriture.
- **MCP** : liste complète des outils, `restore_baseline` sans `dry_run=false` n'écrit rien, rôle manquant ⇒ `ToolError`.
- **Frontend (Vitest + Testing Library)** : boutons de transition selon `/me` et `allowed_transitions`, diff à trois colonnes, boîte de restauration ; E2E Playwright dès qu'il existe (capturer → modifier → comparer → restaurer).

## 10. ADR à écrire

1. « Journal d'audit append-only écrit par les services dans la transaction de l'écriture » (granularité, immuabilité trigger + droits, pas de FK, rétro-remplissage).
2. « Baselines en copie normalisée du graphe » (alternatives JSONB et reconstruction ; périmètre paquet ; restauration).
3. « Approbation et verrouillage des éléments, rôle `ea-approver` » (colonnes hors `properties`, règles de modification, complète l'ADR d'authentification).

## 11. Risques et questions ouvertes (défaut retenu)

- **R1 Coût de stockage de l'audit** : `before`+`after` complets doublent le volume. Défaut : stocker les deux (diff simple et indépendant du temps) ; ordre de grandeur ~2 Ko/événement ⇒ ~10 000 événements/an par utilisateur actif = ~20 Mo, négligeable. À reconsidérer (delta seulement) au-delà de ~5 Go.
- **R2 Falsification par un opérateur de la base** : pas de chaîne de hachage. Défaut : non, hors périmètre ; `pg_dump` régulier (ADR 0025) suffit pour détecter une divergence.
- **R3 Rétention** : défaut : aucune purge automatique ; politique manuelle documentée (au-delà de 24 mois, archivage par `pg_dump` puis purge opérateur hors application). Une baseline ne dépend pas de l'audit.
- **R4 Séparation des tâches** : l'approbateur ne peut pas approuver ce qu'il a modifié en dernier. Défaut : oui, appliqué (comparaison avec le dernier événement `updated`) ; désactivable par réglage `EA_APPROVAL_FOUR_EYES=false` pour un dépôt à un seul utilisateur.
- **R5 Documents et boîtes dans la baseline** : les documents (TEXT lourds) ne sont pas capturés ; les boîtes de diagrammes le sont (petites). Défaut : ainsi ; un document d'un élément supprimé puis restauré est perdu (le dry_run l'annonce). Rejeter si les utilisateurs veulent une restauration complète : ajouter `baseline_documents` (coût stockage notable).
- **R6 Relations d'un élément approuvé** : défaut : ajout/retrait autorisé mais repasse l'élément en `in_review`.
- **R7 Qui lit l'audit** : défaut : tout appelant authentifié ; le restreindre aux éditeurs demanderait un seul changement de garde.
- **R8 Restauration vs charge concurrente** : une restauration longue contre des écritures concurrentes. Défaut : transaction `SERIALIZABLE` sur le périmètre, `409` retryable en cas de conflit ; au pire quelques secondes à l'échelle visée.
- **R9 Couplage avec sous-projet 1** : le périmètre « paquet » d'une baseline dépend de `packages`. Défaut : le lot baselines globales ne l'utilise pas ; la colonne `package_id` est ajoutée au lot suivant, sans changer les lots déjà livrés.

## 12. Dépendances et taille

Taille globale **L** (audit M, baselines M, approbation M). Dépend de : migration `0008` appliquée (état courant) ; sous-projet 1 pour le périmètre paquet (non bloquant, voir R9) ; import du realm Keycloak pour `ea-approver` (bloquant seulement pour le lot 3).

Lots livrables, dans l'ordre :
1. **Audit (B5), M** : domaine `audit`, migration + trigger + rétro-remplissage, écriture atomique dans tous les services (éléments, relations, diagrammes, documents), `GET /audit`, historique d'élément, outils MCP de lecture, écran Historique. Livrable seul ; tout le reste y écrit.
2. **Baselines globales (B4), M** : tables, capture, liste, diff API + écran, `dry_run` et restauration (événements d'audit inclus), outils MCP baselines. Portée paquet dès que le sous-projet 1 existe (extension S).
3. **Approbation et verrouillage (B6), M** : colonnes, `ea-approver` dans le realm et `domain/auth.py`, règles dans `update_element`/`delete_element`/`connect`, transitions API/MCP, bandeau SPA ; la restauration apprend à refuser un élément verrouillé.
4. **Finition, S** : audit des baselines suppression/restauration dans l'écran, cible `make` de baseline avant mise en service, documentation de rétention.
