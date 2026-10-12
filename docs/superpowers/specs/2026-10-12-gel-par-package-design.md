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

## 3. Domaine et garde

- `domain/packages.py` : calcul pur de la version suivante, `FrozenBy(package_id, path, version)`.
- `domain/approval.py` : `guard_element_write(element, kind, caller, frozen_by=None)`. Si `frozen_by` est renseigné, **tout** est refusé, approbateur compris (`409 package_frozen`, message « rouvrir X vN d'abord »). Le verrou d'élément garde sa logique.
- Le dépôt résout l'ancêtre gelé par une CTE sur les ancêtres **dans la requête qui verrouille la ligne**, donc dans la transaction qui écrit.
- `tests/unit/test_review_guard.py` (AST) est étendu : une porte d'écriture qui n'appelle pas la résolution échoue.

**Portée du gel** dans un sous-arbre gelé, sont refusés : modifier ou supprimer un élément, ajouter ou retirer un lien dont un bout est dedans (même si l'autre est dehors), documents, rangement dedans ou dehors, restauration d'une baseline qui le touche, renommer / déplacer / supprimer un package ou créer un sous-package, modifier / supprimer / déplacer un diagramme.

## 4. Services

**`PackageService.publish`** (`require_approver`, une transaction `SERIALIZABLE`) :
1. verrou de l'arbre (celui de l'ADR 0042) ;
2. refus si le package ou un ancêtre est gelé (`409 package_already_frozen`) ;
3. refus si le sous-arbre dépasse les plafonds des baselines (`422 scope_too_large`), jamais de coupe silencieuse ;
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
- Erreurs typées : `package_frozen`, `package_already_frozen`, `adr_required`, `scope_too_large`, avec leur ligne dans `_STATUS` (`api/errors.py`).

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

## 8. Risques

- Chaque porte d'écriture doit résoudre l'ancêtre : c'est le coût du gel dérivé, couvert par le test AST.
- Un gros sous-arbre rend la publication lente : bornée par les plafonds de baseline, refus plutôt que coupe.
- Un MAJEUR repose sur une référence d'ADR saisie à la main ; le serveur n'en juge pas le contenu.
