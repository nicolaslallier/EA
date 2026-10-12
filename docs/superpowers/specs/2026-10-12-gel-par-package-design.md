# Gel par package et vue arbre — spec de design

Date : 2026-10-12
Statut : à relire
Sources : DOC-ADM-008 (packages contrôlés, versions, baselines), DOC-ADM-010 (structure racine), ADR 0042 (packages), 0047 (baselines), 0048 (approbation).
ADR à écrire : 0074.

## 1. Intention

Geler ne se fait plus seulement par élément (`locked`) ni pour tout le modèle (baselines). Un **package** se gèle en publiant une **version `vX.Y.Z`** de son sous-arbre. L'approbateur le fait en **un seul geste**. Le gel s'applique à tout le sous-arbre, il est **dérivé** (jamais copié sur les éléments), et une **baseline limitée au sous-arbre** est prise dans la même transaction. Le catalogue gagne une **vue arbre** (packages, sous-packages, éléments) qui montre version et gel.

Décisions du brainstorming : option 3 (verrou hérité + baselines scopées), geste unique de l'approbateur, gel dérivé (approche B), vue arbre demandée.

**Hypothèse conservée** : le verrou par élément (ADR 0048) reste tel quel et indépendant. Le gel de package s'ajoute.

**Hors périmètre** : glisser-déposer (le déplacement par lots existant reste), baseline mensuelle automatique (aucun ordonnanceur ; `make baseline-capture` suffit), suppression de `locked` par élément, droits par package, vérification serveur de l'analyse d'impact et du plan de migration d'un MAJEUR.

## 2. Données

**Migration `0020`, table `package_versions`** (append-only) :

| Colonne | Rôle |
|---|---|
| `id`, `package_id` | FK `packages` `RESTRICT` |
| `version` | `vX.Y.Z` |
| `bump` | `major` / `minor` / `patch` |
| `notes` | obligatoires pour tout niveau (DOC-ADM-008 § 7) |
| `adr` | obligatoire si `major`, sinon nul |
| `baseline_id` | FK `baselines` `RESTRICT` : une baseline référencée ne se supprime pas |
| `published_by_sub`, `published_at` | |
| `reopened_by_sub`, `reopened_at`, `reopen_reason` | nuls tant que la version est en vigueur |

Un index unique partiel `(package_id) WHERE reopened_at IS NULL` garantit au plus une version en vigueur. **Un package est gelé s'il a une ligne en vigueur** : pas de booléen redondant. La version suivante se calcule sur la dernière ligne, rouverte ou non (`v1.0.0` pour la première).

**`baselines`** gagne `scope_package_id` (nullable, sans FK, comme le reste des copies) et `scope_path` (chemin copié, il survit à la suppression du package). Les tables `baseline_*` ne changent pas. Sans périmètre, la baseline est celle d'aujourd'hui (tout le modèle).

## 3. Application du gel : des triggers PostgreSQL (décision du 2026-10-12)

Le refus est appliqué **par la base**, pas par une garde Python dans chaque porte : aucune porte ne peut être oubliée, y compris un futur script, l'import ou le pipeline.

- Fonction `ea_frozen_ancestor(pkg)` (CTE sur les ancêtres, jointe aux versions en vigueur) et `ea_assert_unfrozen(pkg, table, op)` qui lève l'erreur `EA001` quand le package ou un ancêtre est gelé. Sortie rapide quand aucune version n'est en vigueur nulle part (une sonde d'index) : le coût est nul tant que rien n'est gelé.
- Triggers `BEFORE INSERT/UPDATE/DELETE` sur `elements` (ancien et nouveau `package_id`), `relationships` (package de chacun des deux bouts), `element_documents`, `element_stereotypes` (package de l'élément), `diagrams` (ancien et nouveau `package_id`), `diagram_nodes`, `diagram_objects`, `diagram_edges` (package du diagramme) et `packages` (le package lui-même et son nouveau parent). `document_chunks` (index dérivé), `file_metadata`, `external_references` (cache d'un outil tiers) et `audit_events` ne sont pas gelés.
- Traduction **centrale** dans `db/unit_of_work.work()` : l'erreur `EA001` devient `PackageFrozenError` (`409 package_frozen`), avec le package gelant, sa version, la table et l'opération, jamais un nom ni un contenu. Chaque écriture refusée annule sa transaction ; l'événement d'audit du refus est écrit après, dans sa propre transaction (§ 9).
- Le test AST de `test_review_guard.py` n'est plus le contrat du gel ; il reste celui du verrou par élément (ADR 0048), inchangé. Un test d'intégration parcourt **toutes** les tables du tableau ci-dessus et prouve qu'une écriture directe est refusée.
- Conséquences assumées : supprimer un élément non gelé que représente un diagramme gelé, ou lié à un élément gelé, est refusé tant que le package n'est pas rouvert ; restaurer une baseline qui touche un sous-arbre gelé échoue en bloc (rien n'est écrit).

**Portée** dans un sous-arbre gelé : modifier ou supprimer un élément (révision, approbation, verrou compris), ajouter ou retirer un lien dont un bout y est, documents, stéréotypes, rangement dedans ou dehors, renommer / déplacer / supprimer un package ou créer un sous-package, modifier / supprimer / déplacer un diagramme ou sa scène.

## 4. Services

**`PackageService.publish`** (`require_approver`, une transaction qui prend d'abord `LOCK TABLE … IN SHARE MODE` sur les tables gelées : elle attend les écritures en cours, bloque les nouvelles, et la baseline lit donc exactement l'état qui sera gelé) :
1. verrou de l'arbre (celui de l'ADR 0042) ;
2. refus si le package ou un ancêtre est gelé (`409 package_already_frozen`) ;
3. refus si le sous-arbre dépasse les plafonds des baselines (`422 baseline_too_large`, le code existant), jamais de coupe silencieuse ;
4. version calculée ; `adr` exigé si MAJEUR (`422 adr_required`) ;
5. baseline du sous-arbre, nommée `<chemin> vX.Y.Z` (éléments, liens dont les deux bouts sont dedans, boîtes de ses diagrammes) ;
6. insertion de la ligne de version ;
7. un événement d'audit `package.published` (ni notes ni noms).

**`PackageService.reopen`** clôt la version en vigueur, exige un motif, audite `package.reopened`.

**Rôles.** Publier et rouvrir : `ea-approver`, qui n'implique pas `ea-editor`. Lire les versions : tout appelant.

`BaselineService.diff` et `plan_restore` ne travaillent que dans le périmètre de la baseline. La restauration d'un sous-arbre gelé est bloquée jusqu'à la réouverture.

## 5. API et MCP

**HTTP**
- `POST /packages/{id}/publish` `{bump, notes, adr?, dry_run}` (dry run vrai par défaut côté MCP ; côté HTTP l'aperçu est la même route avec `dry_run=true`).
- `POST /packages/{id}/reopen` `{reason}`.
- `GET /packages/{id}/versions`.
- `PackageRead` : `frozen_version`, `frozen_by` (ancêtre gelé effectif), `allowed_actions` (`publish`, `reopen`) selon l'état et le rôle.
- `ElementRead` et `DiagramRead` : `frozen_by`.
- `GET /baselines` expose le périmètre ; `POST /baselines` accepte `package_id`.
- Erreurs typées : `package_frozen`, `package_already_frozen`, `package_not_frozen`, `adr_required`, `invalid_publication`, `baseline_too_large`, avec leur ligne dans `_STATUS` (`api/errors.py`).

**MCP : 76 → 79 outils.** `list_package_versions` (lecture), `publish_package` (écriture, dry run par défaut, renvoie la version et le nombre d'éléments gelés), `reopen_package` (écriture). Entrée dans l'assertion de liste de `tests/unit/test_mcp_server.py`. Les bornes (notes, `adr`, motif) sont déclarées une fois dans `api/schemas.py` et couvertes par `TestTheSameBoundsAsTheHttpAdapter`.

## 6. SPA

- Mode « Arbre » dans le catalogue (`features/elements`) ; `PackageTree.vue` et `packageTree.ts` réutilisés ; les éléments sont les feuilles du package.
- Éléments chargés à l'ouverture d'un nœud (filtre `package` existant, paginé), une instance de `useLatestRequest()` par nœud ouvert. Le nœud « racine » charge les éléments sans package.
- URL : `?package=` (nœud) et `?element=` (fiche). Nœuds dépliés dans une `ref`.
- Badges : `vX.Y.Z` + cadenas sur le package gelé ; cadenas grisé « gelé par X vN » sur les descendants, lu dans `frozen_by` (le SPA n'hérite de rien). Badge de revue conservé sur chaque élément.
- Boutons « Publier… » / « Rouvrir… » dessinés d'après `allowed_actions`. Le dialogue de publication : niveau, notes, ADR si MAJEUR, aperçu par dry run. Les contrôles d'écriture sont masqués dans un sous-arbre gelé ; l'API refuse.
- `BaselinesSection.vue` : colonne et filtre « Périmètre » ; restauration limitée au sous-arbre.
- Rendu des textes en texte, jamais `v-html` (`tests/noVHtml.spec.ts`).

## 7. Tests et livraison

- **Unitaires** : version suivante (premier, patch, minor, major), garde avec `frozen_by`, ancêtres.
- **Intégration** (conteneurs jetables) : publication contre écriture concurrente (`asyncio.gather`), publication d'un parent dont un enfant est déjà gelé, baseline scopée, chaîne de migrations `0020` aller-retour.
- **API** : 401, 403 (éditeur sans `ea-approver`), 409, 422.
- **AST** : toutes les portes d'écriture résolvent le gel.
- **SPA** : Vitest sur l'arbre, les badges, le dialogue, les actions.
- **Livraison** : ADR 0074 ; `make openapi` ; CLAUDE.md mis à jour dans le même commit ; `make check` vert.

## 8. Risques (voir aussi § 9 et § 10)

- Un trigger par table écrite : une table nouvelle qui référence des éléments doit recevoir le sien (le test d'intégration liste les tables gelées et échoue sur un oubli connu).
- Un gros sous-arbre rend la publication lente : bornée par les plafonds de baseline, refus plutôt que coupe.
- Un MAJEUR repose sur une référence d'ADR saisie à la main ; le serveur n'en juge pas le contenu.

## 9. Traçabilité totale

Objectif : pour tout état d'un sous-arbre, savoir **qui**, **quand**, **par quel canal**, **pourquoi**, **sur quelle version**, et pouvoir remonter dans les deux sens.

**Chaîne navigable dans les deux sens.** `package_versions.publish_audit_event_id` et `reopen_audit_event_id` pointent l'événement d'audit ; l'événement porte `package_id`, `version`, `baseline_id`, `bump`, les comptes (éléments, liens, boîtes), le **SHA-256** des notes, de l'ADR et du motif, jamais leur texte (règle de l'audit, ADR 0046). La baseline porte son périmètre et son numéro de version dans son nom. Depuis n'importe quel maillon (version, baseline, événement) on atteint les deux autres.

**Ce qui est audité (table `audit_events`, même transaction que l'écriture)** :

| Geste | Événement |
|---|---|
| Publier | `package.published` |
| Rouvrir | `package.reopened` |
| Baseline scopée créée, supprimée, restaurée | événements baseline existants, avec le périmètre |
| Écriture **refusée** par un gel | `package.write_refused_frozen` — acteur, canal, package gelant, version, table et opération visées |

Le dernier point déroge à la règle « un refus n'écrit pas d'événement » (CLAUDE.md, audit). Raison : une tentative d'écrire dans un sous-arbre figé est un signal de sécurité, pas du bruit. L'événement est écrit dans **sa propre transaction**, après le rollback de l'écriture refusée, pour qu'un refus ne soit jamais perdu avec elle. Garde-fou : au-delà de 30 refus par minute et par `sub`, l'événement n'est plus écrit (le refus reste journalisé avec le nombre supprimé), sinon le journal devient le vecteur de déni de service.

**Ce qui est journalisé sans être audité** : les aperçus (`dry_run=true`) de publication et de restauration — ligne structurée `action=package_publish_preview`, acteur, canal, request id, compte annoncé. Ils ne changent aucun état.

**Intégrité.** `package_versions` est append-only : un trigger refuse `DELETE` et `TRUNCATE`, et refuse tout `UPDATE` hors des trois colonnes de réouverture quand elles sont nulles. Le rôle de base de données en `INSERT, SELECT` reste dû par l'Infra (comme pour `audit_events`).

**Lecture.** `GET /audit?entity_kind=package&entity_id=<id>` suffit (publications, réouvertures et refus ont le package pour entité) ; `GET /packages/{id}/versions` rend l'historique des versions avec acteur et dates. Le SPA affiche, sur la fiche d'un package, les versions et l'historique réutilisant `features/history`. Deux outils MCP en lecture existent déjà pour l'audit ; `list_package_versions` s'y ajoute.

**Rétention.** Rien ne purge : `docs/audit-retention.md` est mis à jour (versions et événements de refus).

## 10. Revue de sécurité (obligatoire)

La revue n'est pas facultative : elle est une **tâche du plan** et une **condition de la PR**.

**Modèle de menaces** (annexe de l'ADR 0074, relu avant le code) :

| Menace | Parade |
|---|---|
| Un éditeur publie ou rouvre | `require_approver` dans `services/`, jamais dans le routeur ; 403 testé HTTP et MCP |
| Contourner le gel par une porte oubliée | Triggers en base sur chaque table écrite ; test d'intégration qui les parcourt toutes |
| Course : écriture qui passe pendant la publication | `LOCK TABLE … IN SHARE MODE` + verrou d'arbre ; le trigger lit l'état validé ; test `asyncio.gather` |
| Déplacer un élément hors d'un package gelé, ou le faire entrer | `FILING` refusé aux deux extrémités |
| Rouvrir sans laisser de trace | Motif obligatoire, événement d'audit, trigger append-only |
| Un agent MCP gèle un sous-arbre par erreur | `dry_run=true` par défaut, annotation d'écriture, aperçu du nombre d'éléments |
| Publication d'un énorme sous-arbre (déni de service) | Plafonds de baseline, refus `scope_too_large`, `Admission` par appelant |
| Injection par `notes`, `adr`, `reason` | Bornes de longueur dans `api/schemas.py`, refus du NUL, rendu en texte (`noVHtml.spec.ts`), valeurs hors des logs |
| Fuite dans les logs et les erreurs | Pas de texte ni de noms dans l'audit ni dans les logs de refus ; erreurs typées génériques |
| Ancêtre gelé inconnu → écriture permise | Échec fermé : une erreur dans le trigger annule l'écriture |

**Étapes de la revue** :
1. Relecture du modèle de menaces avec la spec, avant le plan d'implémentation.
2. `test_package_freeze_security.py` : un test par ligne du tableau.
3. `make audit` (bandit, pip-audit, npm audit) et `make check` verts.
4. Revue indépendante de la branche par le skill `security-review`, puis `code-review`, avant d'ouvrir la PR ; les constats sont traités ou justifiés par écrit.
5. La description de la PR liste la menace, la parade et le test de chacune.

Je lance les étapes 3 et 4 et j'en rapporte le résultat tel quel ; elles ne sont pas faites tant que je ne l'ai pas dit.

## 11. Décisions tranchées

- **Pas de séparation des tâches à la publication** (décision du 2026-10-12). Un approbateur peut publier un sous-arbre qu'il a lui-même modifié en dernier. La publication, le motif et l'acteur restent tracés (§ 9) ; l'ADR 0074 le consigne comme choix explicite, à rouvrir si le dépôt passe à plusieurs approbateurs.
