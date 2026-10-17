# Gel par package — plan 2 : lectures, MCP, baselines scopées, vue arbre

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** finir le gel par package du plan 1 (PR #128) : chaque lecture d'élément et de diagramme dit qui le gèle, un agent publie et rouvre par MCP, une baseline de sous-arbre se capture, se compare et se restaure, et le SPA montre l'arbre, les versions et les gels sans rien décider lui-même.

**Architecture :** trois lots. **Lot A (serveur, une PR mergeable seule)** : `PackageService.freezes()` sert la carte `package → FrozenBy` que chaque `ElementRead.of` / `DiagramRead.of` reçoit en argument obligatoire ; trois outils MCP au-dessus de `PackageService` ; `BaselineService` lève `refuse_scoped` et compare / restaure dans le périmètre grâce à une restriction pure du modèle courant (`domain/baselines.restricted_to`). **Lot B (SPA, une PR qui consomme le client régénéré du lot A)** : un mode « Arbre » du catalogue, des badges, deux dialogues, des contrôles masqués d'après `frozen_by` / `allowed_actions`, un périmètre dans les baselines. **Lot C** : la revue de sécurité de toute la fonctionnalité (avec mutation des gardes) et la documentation.

**Tech Stack :** FastAPI, Pydantic v2, SQLAlchemy 2 async, `mcp` SDK 2.x, pytest ; Vue 3 `<script setup>` + TypeScript, `openapi-fetch`, Vitest + Testing Library.

**Spec :** `docs/superpowers/specs/2026-10-12-gel-par-package-design.md` — avec `docs/adr/0074-gel-par-package-et-triggers.md` et le plan 1 `docs/superpowers/plans/2026-10-12-gel-par-package-backend.md`, qui annonce celui-ci.

**Prérequis :** la PR #135 (`fix/sparx-08-review-fixes`) est fusionnée sur `main` avant la tâche A1 : elle apporte les migrations `0021`/`0022`, l'ADR `0076`, `external_references` dans `repositories/frozen_tables.py` et **`PackageService.frozen_by(package_id)`**, que ce plan réutilise. Toute nouvelle migration de ce plan serait la `0023` (aucune n'est prévue) ; le nouvel ADR est le `0077`.

## Global Constraints

- Branches : lot A `feat/gel-par-package-lectures-mcp`, lot B `feat/gel-par-package-spa` (créée depuis `main` **après** la fusion du lot A), lot C sur la branche du lot B. Conventional Commits, un commit par tâche, dernière ligne `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Jamais `--no-verify`.
- Depuis `backend/` : `uv run pytest <fichier> -q` (sans `--cov`), `uv run ruff format . && uv run ruff check --fix .`, `uv run mypy src migrations`. Intégration : `make test-integration` (conteneurs jetables, jamais la base partagée).
- Depuis `frontend/` : `npm test -- --run tests/<fichier>.spec.ts`, `npm run lint && npm run typecheck`.
- `api → services → domain ← repositories` ; `mcp/` à côté d'`api/`, jamais derrière, jamais de dépôt.
- Toute méthode publique de service commence par `require_caller()` / `require_editor()` / `require_approver()` (`tests/unit/test_service_guards.py`).
- Une borne est déclarée une fois dans `api/schemas.py` (`PublicationNotes`, `PublicationAdr`, `ReopeningReason` existent) ; `mcp/server.py` la réimporte et la paire entre dans `TestTheSameBoundsAsTheHttpAdapter`.
- Un outil MCP : docstring lue par le modèle, annotation (`READS` / `EDITS`), `@speaking_plainly`, entrée dans l'assertion de liste complète de `tests/unit/test_mcp_server.py` (76 aujourd'hui sur `main`, 79 après la tâche A4).
- Le schéma bouge → `make openapi` dans le même commit (`backend/openapi.json` et `frontend/src/api/schema.d.ts`). Aucune interface TypeScript écrite à la main pour un modèle Pydantic.
- Le SPA ne restate aucune règle : il dessine un bouton par entrée de `allowed_actions` / `allowed_transitions`, masque les écritures d'après `frozen_by` reçu de l'API, et montre le refus de l'API tel quel (`messageOf`). Chaque chargement déclenché par l'URL ou une saisie passe par `useLatestRequest()`. Ce qu'on partage est dans l'URL (`?package=`, `?element=`, `?vue=`, `?perimetre=`). Textes de l'interface en français. Aucun `v-html` (`tests/noVHtml.spec.ts`). Couleurs : jetons `--surface --muted --accent --success --warning --danger` de `frontend/src/style.css` (ADR 0075), rien en dur.
- Vocabulaire : « publié / gelé par » pour un package ; « figé » reste celui des vues générées (ADR 0071) et « verrouillé » celui de l'élément (ADR 0048). Dans le code : `frozen_by`, `FrozenBy`, `Freezes`.
- Un refus ne cite jamais un nom ni un texte : identifiants, version, table, opération (ADR 0074).

## Review Focus

1. **Rien n'est publié nulle part** (le cas de toute instance aujourd'hui) : `freezes()` coûte une requête et ne charge pas l'arbre ; toutes les lectures rendent `frozen_by: null` et les transitions habituelles (tâche A1, `test_nothing_published_costs_one_query_and_freezes_nothing`).
2. **Un élément sorti du sous-arbre depuis la baseline** : le diff scopé le montre *modifié* (son package), pas *supprimé*, et la restauration le remet dedans au lieu de tenter de le recréer (tâche A3, `test_an_element_moved_out_since_is_modified_not_removed`).
3. **Une restauration scopée ne touche jamais ce qui est hors du sous-arbre** : ni un élément d'à côté, ni un lien entre deux éléments d'à côté (tâche A3, `test_a_scoped_restore_never_deletes_outside_its_subtree`).
4. **Publier deux fois de suite depuis le dialogue** (double clic, ou aperçu périmé) : le second envoi reçoit `package_already_frozen`, le dialogue l'affiche et l'arbre se recharge (tâche B3, `it shows the refusal of a second publication and reloads the tree`).
5. **Un lien de l'URL vers un package supprimé, ou une feuille dont le chargement échoue** : l'arbre s'affiche quand même, le nœud fautif porte son erreur, les autres nœuds ne sont pas annulés (tâche B2, `one failing node does not cancel the others`).

## Écarts constatés (spec ↔ `main` au 2026-10-17, PR #135 supposée fusionnée)

1. **Baselines scopées.** Spec § 4 : diff et restauration « ne travaillent que dans le périmètre ». `main` les **refuse** (`services/baselines.refuse_scoped`, `baseline_store._plan` → `409 baseline_scoped`), choix provisoire de l'ADR 0074 en attendant ce plan. La tâche A3 lève ce refus et l'ADR 0077 remplace la conséquence correspondante de l'ADR 0074.
2. **`external_references`.** Spec § 3 la dit *non gelée* (cache d'un tiers) ; l'ADR 0076 (PR #135) l'ajoute à `GUARDED_TABLES` avec un trigger (migration `0022`). Ce plan suit `main` + #135 ; la spec est en retard sur ce point.
3. **Dry run HTTP.** Spec § 5 : « dry run vrai par défaut côté MCP ; côté HTTP l'aperçu est la même route avec `dry_run=true` ». `main` met déjà `dry_run=True` par défaut côté HTTP aussi (`PublishPackage`). Plus strict, conservé ; MCP fait pareil.
4. **Erreurs sans ligne dans la spec.** `main` ajoute `422 live_view_in_subtree`, `409 package_has_versions`, `409 baseline_in_use` (ADR 0074). Le SPA les affiche par `messageOf`, sans traitement propre.
5. **Fiche d'un package.** Spec § 9 : « le SPA affiche, sur la fiche d'un package, les versions et l'historique ». Il n'existe pas de fiche de package sur `main` (`PackageManager.vue` est une barre de contrôles). La tâche B3 ajoute un panneau sous la barre ; `ElementHistory.vue` est code en dur sur `kind: 'element'` et devient générique.
6. **Catalogue.** Spec § 6 : « `PackageTree.vue` et `packageTree.ts` réutilisés ». Sur `main` l'arbre est déjà la barre latérale de filtre du catalogue (`?package=` = `''` tous, `root` sans package, sinon un id ; `?subpackages=0`). Le mode « Arbre » réutilise **le même** `?package=` (le nœud choisi) ; le mode lui-même va dans `?vue=arbre` (la spec ne dit pas où le mettre).
7. **Ajouts non demandés par la spec, gardés pour la cohérence :** le dry run d'une restauration scopée prédit le gel (décision Q3) ; `allowed_transitions` vide sur un élément gelé (le serveur décide, le SPA dessine — sinon le bandeau de revue offrirait des boutons que le trigger refuse) ; `create_baseline` (MCP) accepte `package_id` comme `POST /baselines` (deux adaptateurs, une seule capacité).
8. **Non fait, la spec ne le demande pas :** `frozen_by` sur `DiagramSummary` (liste des diagrammes) et sur `GraphRead` hors des éléments qu'il porte — décision Q5.
9. **Revue par mutation.** La consigne demande « la mutation des gardes, comme les plans précédents » : aucun plan de `docs/superpowers/plans/` n'en contient. La tâche C1 en définit la procédure (mutants manuels, un test qui tue chacun).
10. **Compte des outils.** La spec dit 76 → 79 : vérifié, `main` (et #135) exposent 76 outils ; aucun outil de version de package n'existe encore.

## Décisions (2026-10-17)

Tranchées par l'utilisateur sur les questions ouvertes de la première version de ce plan.

- **Q1 — périmètres différents.** Comparer une baseline scopée à une baseline d'un autre périmètre (ou globale) est refusé : `409 baseline_scoped` (« même périmètre exigé »). Le SPA n'offre que les baselines du même périmètre (B5).
- **Q2 — `keep_new`.** `False` par défaut pour une restauration scopée, comme la globale : exacte dans le sous-arbre, ce qui y a été créé depuis est supprimé.
- **Q3 — restaurer un sous-arbre gelé : prédit, pas seulement refusé.** Quand le package du périmètre ou un de ses ancêtres a une version en vigueur, le **dry run** le dit : le plan porte `frozen_by` et est *bloqué*, sur le même mécanisme que les éléments approuvés ou verrouillés (`RestorePlan.blocked` / `is_blocked`, ADR 0049). L'application est refusée **avant toute écriture** par `409 package_frozen`. Les triggers restent l'autorité pour la course entre le dry run et l'écriture (ils refusent en bloc). Le SPA grise le bouton de confirmation et affiche « gelé par X vN — rouvrir d'abord » (B5). Cela vaut pour une baseline **scopée** (le périmètre est connu). Pour une baseline globale qui touche un sous-arbre gelé, rien n'est prédit : les triggers refusent à l'application, comme aujourd'hui. Une prédiction partielle (sans les boîtes ni les liens) se lirait comme une prédiction complète.
- **Q4 — package du périmètre supprimé.** Le diff marche (les éléments sont lus là où ils sont) ; la restauration est refusée : `409 baseline_scoped`.
- **Q5 — `DiagramSummary`.** Pas de `frozen_by` sur la liste des diagrammes (la spec ne le demande pas).

---

# Lot A — serveur (PR 1)

### Task A1 : `frozen_by` sur `ElementRead` et `DiagramRead`

**Files :**
- Modify : `backend/src/ea/domain/package_versions.py` (alias `Freezes`)
- Modify : `backend/src/ea/domain/approval.py:88` (`allowed_transitions`)
- Modify : `backend/src/ea/services/packages.py` (`freezes()`, `frozen_by()` réécrit dessus)
- Modify : `backend/src/ea/api/schemas.py` (`ElementRead`, `DiagramRead` et chaque `of` qui construit un `ElementRead`)
- Modify : `backend/src/ea/api/architecture.py`, `api/diagrams.py`, `api/queries.py`, `api/profiles.py`, `api/timeline.py`, `api/diagram_templates.py`, `backend/src/ea/mcp/server.py` (chaque appel à `ElementRead.of` / `DiagramRead.of` / un `of` qui en construit)
- Test : `backend/tests/unit/test_package_service.py`, `backend/tests/unit/test_approval.py`, `backend/tests/unit/test_element_read_freeze.py` (nouveau), `backend/tests/e2e/test_package_publication_api.py`, `backend/tests/unit/test_mcp_server.py`

**Interfaces :**
- Consumes : `FrozenBy`, `effective_freezes(packages, in_force)` (plan 1) ; `PackageService.frozen_by(package_id) -> FrozenBy | None` (PR #135).
- Produces :
  - `Freezes = Mapping[UUID, FrozenBy]` dans `domain/package_versions.py`.
  - `PackageService.freezes(self) -> dict[UUID, FrozenBy]` — `require_caller()` ; `{}` sans charger l'arbre quand `in_force()` est vide. `frozen_by(package_id)` devient `(await self.freezes()).get(package_id)` (même garde, même comportement).
  - `allowed_transitions(state, locked, caller, *, frozen: bool) -> tuple[Transition, ...]` — `()` quand `frozen`.
  - `ElementRead.frozen_by: FrozenByRead | None` (description : « The publication that freezes this element's package: nothing below it can be written until it is reopened. ») ; `ElementRead.of(element: Element, *, freezes: Freezes) -> ElementRead`.
  - `DiagramRead.frozen_by: FrozenByRead | None` ; `DiagramRead.of(detail: DiagramDetail, *, freezes: Freezes) -> DiagramRead` (ses `elements` reçoivent la même carte).
  - Tout autre `of` de `schemas.py` qui construit un `ElementRead` prend `*, freezes: Freezes` et le passe ; **aucune valeur par défaut** : mypy trouve chaque porte. Chaque routeur et chaque outil MCP lit `freezes = await packages.freezes()` **dans le corps** (après que la dépendance d'authentification a posé l'appelant), une fois par requête.

- [ ] **Step 1 : les tests qui échouent**

`backend/tests/unit/test_package_service.py`, nouvelle classe `TestTheFreezesOfTheTree` (doubles existants du fichier) :

```python
async def test_nothing_published_costs_one_query_and_freezes_nothing(self, service, repository):
    assert await service.freezes() == {}
    assert repository.calls.count("list_all") == 0   # the tree is not read

async def test_a_publication_freezes_the_package_and_everything_below(self, service, repository):
    # Finance (v1.0.0 in force) > Budget ; Achats unpublished
    freezes = await service.freezes()
    assert freezes[budget.id] == FrozenBy(finance.id, "Finance", "v1.0.0")
    assert freezes[finance.id].version == "v1.0.0"
    assert achats.id not in freezes

async def test_frozen_by_reads_the_same_map(self, service, repository):
    assert await service.frozen_by(budget.id) == (await service.freezes())[budget.id]

async def test_nobody_calling_is_refused(self, service, nobody_calling):
    with pytest.raises(NotAuthenticatedError):
        await service.freezes()
```

(Si le double ne compte pas ses appels, ajouter une liste `calls` au double existant — pas un nouveau double.)

`backend/tests/unit/test_approval.py` :

```python
def test_a_frozen_element_is_offered_no_transition_whoever_asks() -> None:
    for state, locked in [("draft", False), ("in_review", False), ("approved", False), ("approved", True)]:
        assert allowed_transitions(state, locked, an_approver(), frozen=True) == ()
```

`backend/tests/unit/test_element_read_freeze.py` :

```python
def test_an_element_in_a_published_subtree_names_its_freeze_and_offers_nothing():
    with acting_as(an_approver()):
        read = ElementRead.of(an_element(package_id=budget), freezes={budget: FrozenBy(finance, "Finance", "v2.1.0")})
    assert read.frozen_by == FrozenByRead(package_id=finance, path="Finance", version="v2.1.0")
    assert read.allowed_transitions == []

def test_an_unfiled_element_is_never_frozen():
    with acting_as(an_approver()):
        read = ElementRead.of(an_element(package_id=None), freezes={uuid4(): FrozenBy(...)})
    assert read.frozen_by is None and read.allowed_transitions != []

def test_a_diagram_and_the_elements_it_draws_carry_their_own_freeze():
    # diagram filed in Budget (frozen); one element drawn on it filed outside
    read = DiagramRead.of(detail, freezes={budget: frozen})
    assert read.frozen_by == FrozenByRead.of(frozen)
    assert {e.id: e.frozen_by for e in read.elements} == {inside.id: FrozenByRead.of(frozen), outside.id: None}
```

`backend/tests/e2e/test_package_publication_api.py` (fixtures existantes : approbateur, publication réelle par la route) :

```python
async def test_an_element_below_a_published_package_says_who_froze_it(client, published_finance):
    page = (await client.get("/elements", params={"package": str(budget)})).json()
    assert {item["frozen_by"]["version"] for item in page["items"]} == {"v1.0.0"}
    one = (await client.get(f"/elements/{inside}")).json()
    assert one["frozen_by"]["path"] == "Finance" and one["allowed_transitions"] == []

async def test_a_diagram_below_a_published_package_says_so(client, published_finance):
    assert (await client.get(f"/diagrams/{diagram}")).json()["frozen_by"]["version"] == "v1.0.0"

async def test_reopening_clears_frozen_by_on_the_next_read(client, published_finance):
    await client.post(f"/packages/{finance}/reopen", json={"reason": "correction"})
    assert (await client.get(f"/elements/{inside}")).json()["frozen_by"] is None
```

`backend/tests/unit/test_mcp_server.py`, classe `TestThePackageTools` :

```python
async def test_get_element_tells_the_agent_the_element_is_frozen(self, server, published):
    read = await call(server, "get_element", element_id=str(inside))
    assert read["frozen_by"]["version"] == "v1.0.0"
```

- [ ] **Step 2 : les lancer, ils échouent**

Run : `cd backend && uv run pytest tests/unit/test_package_service.py tests/unit/test_approval.py tests/unit/test_element_read_freeze.py tests/unit/test_mcp_server.py -q -k "freeze or frozen"`
Expected : FAIL — `AttributeError: 'PackageService' object has no attribute 'freezes'`, `TypeError: ... unexpected keyword argument 'frozen'`, `frozen_by` absent.

- [ ] **Step 3 : implémenter**

`Freezes`, `PackageService.freezes()`, `allowed_transitions(..., frozen=)`, les deux champs et les `of(..., freezes=)` selon les interfaces ci-dessus ; `ElementRead.of` calcule `frozen = freezes.get(element.package_id) if element.package_id else None` et passe `frozen=frozen is not None`. Puis `uv run mypy src` liste chaque appel à corriger : dans chaque routeur et chaque outil, `freezes = await packages.freezes()` (ajouter la dépendance `Packages` au routeur qui ne l'a pas, `get_packages()` côté MCP). Ne pas ajouter de valeur par défaut pour faire taire mypy.

- [ ] **Step 4 : vérifier**

Run : `cd backend && uv run mypy src migrations && uv run pytest tests/unit tests/e2e -q`
Expected : mypy `Success`, tous les tests PASS (les tests existants qui appellent `ElementRead.of(x)` reçoivent `freezes={}`).

- [ ] **Step 5 : régénérer le contrat**

Run (racine) : `make openapi && make openapi-check`
Expected : `backend/openapi.json` et `frontend/src/api/schema.d.ts` gagnent `frozen_by` sur `ElementRead` et `DiagramRead` ; `openapi-check` vert ; `cd frontend && npm run typecheck` vert.

- [ ] **Step 6 : commit**

```bash
git add backend frontend/src/api
git commit -m "feat(packages): frozen_by on every element and diagram read

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task A2 : capturer une baseline d'un sous-arbre, et lire son périmètre

**Files :**
- Modify : `backend/src/ea/api/schemas.py` (`BaselineCreate`, `BaselineRead`)
- Modify : `backend/src/ea/api/baselines.py` (`create_baseline`, réponse 404)
- Modify : `backend/src/ea/services/baselines.py` (`BaselineService.create`)
- Modify : `backend/src/ea/domain/ports.py` (`BaselineRepository.create`)
- Modify : `backend/src/ea/repositories/baseline_store.py` (`PostgresBaselineRepository.create`)
- Modify : `backend/src/ea/domain/baselines.py` (`baseline_view` gagne `scope_package_id`)
- Modify : `backend/src/ea/mcp/server.py` (`create_baseline`)
- Modify : le double `InMemoryBaselines` des tests unitaires
- Test : `backend/tests/unit/test_baseline_service.py`, `backend/tests/integration/test_baseline_scope.py`, `backend/tests/e2e/test_baseline_api.py`, `backend/tests/unit/test_mcp_server.py`

**Interfaces :**
- Consumes : `capture_scope(session, baseline, scope, *, package_id, path)`, `scope_size(session, scope)`, `refuse_oversize(size, limits, what=)` (plan 1) ; `descendants_of(package_id, packages)`, `path_of(package, packages)` (`domain/packages.py`).
- Produces :
  - `BaselineCreate.package_id: UUID | None = None` (description : « Copy only this package and everything below it; leave out for the whole model. »).
  - `BaselineRead.scope_package_id: UUID | None`, `BaselineRead.scope_path: str | None` (description : « The subtree this baseline copies, as its path was then; null for the whole model. »).
  - `BaselineService.create(self, *, name: str, description: str = "", package_id: UUID | None = None) -> Baseline` — `require_editor()` (comme la globale).
  - `BaselineRepository.create(self, baseline: Baseline, *, audit: AuditOf[Baseline], scope: UUID | None = None) -> Baseline` — avec `scope`, dans une transaction : lit l'arbre, `PackageNotFoundError` si le package n'existe pas, sous-arbre = `{scope} | descendants_of(scope, tree)`, `refuse_oversize(await scope_size(...), self._limits, what="the package")` **avant** la copie, puis `capture_scope(..., path=path_of(...))`.
  - `baseline_view` : `"scope_package_id": str | None` ajouté à côté de `scope_path`.
  - MCP `create_baseline(name, description="", package_id: UUID | None = None)`, docstring augmentée : « With `package_id`, copies only that package and everything below it, which is what a published version takes. »

- [ ] **Step 1 : les tests qui échouent**

`backend/tests/unit/test_baseline_service.py` :

```python
async def test_a_baseline_of_one_package_says_so(self, baseline_service, baseline_store):
    made = await baseline_service.create(name="Finance avant refonte", package_id=finance)
    assert baseline_store.created_with_scope[made.id] == finance

async def test_a_reader_cannot_capture_a_subtree(self, baseline_service):
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await baseline_service.create(name="x", package_id=uuid4())
```

`backend/tests/integration/test_baseline_scope.py` :

```python
async def test_the_repository_captures_a_subtree_by_its_package(engine_at_head):
    # Finance > Budget (one element each), one element outside
    stored = await repository.create(a_baseline("Finance"), audit=no_audit, scope=finance)
    assert (stored.scope_package_id, stored.scope_path, stored.element_count) == (finance, "Finance", 2)

async def test_an_unknown_package_is_a_404_and_copies_nothing(engine_at_head):
    with pytest.raises(PackageNotFoundError):
        await repository.create(a_baseline("x"), audit=no_audit, scope=uuid4())
    assert await count_rows(engine_at_head, "baselines") == 0

async def test_a_subtree_past_the_ceiling_is_refused_before_it_is_copied(engine_at_head):
    small = PostgresBaselineRepository(sessions, limits=BaselineLimits(elements=1))
    with pytest.raises(BaselineTooLargeError):
        await small.create(a_baseline("Finance"), audit=no_audit, scope=finance)
    assert await count_rows(engine_at_head, "baseline_elements") == 0
```

`backend/tests/e2e/test_baseline_api.py` :

```python
async def test_post_baselines_takes_a_package_and_the_list_shows_the_scope(client, finance):
    made = await client.post("/baselines", json={"name": "Finance", "package_id": str(finance)})
    assert made.status_code == 201 and made.json()["scope_path"] == "Finance"
    listed = (await client.get("/baselines")).json()
    assert {b["scope_package_id"] for b in listed} >= {str(finance)}

async def test_an_unknown_package_is_a_typed_404(client):
    answer = await client.post("/baselines", json={"name": "x", "package_id": str(uuid4())})
    assert (answer.status_code, answer.json()["error"]["code"]) == (404, "not_found")

async def test_a_whole_model_baseline_has_no_scope(client):
    made = (await client.post("/baselines", json={"name": "Tout"})).json()
    assert (made["scope_package_id"], made["scope_path"]) == (None, None)
```

(Lire l'enveloppe d'erreur réelle dans un test voisin du fichier et s'y conformer.)

`backend/tests/unit/test_mcp_server.py` : `test_create_baseline_takes_a_package` — `call(server, "create_baseline", name="F", package_id=str(pkg))["scope_path"] == "Finance"`.

- [ ] **Step 2 : les lancer, ils échouent**

Run : `cd backend && uv run pytest tests/unit/test_baseline_service.py tests/e2e/test_baseline_api.py -q -k "package or scope"`
Expected : FAIL — `unexpected keyword argument 'package_id'`, `extra_forbidden` sur `package_id`.

- [ ] **Step 3 : implémenter** selon les interfaces. Le chemin global reste inchangé quand `scope is None`.

- [ ] **Step 4 : vérifier**

Run : `cd backend && uv run pytest tests/unit tests/e2e -q && cd .. && make test-integration`
Expected : PASS.

- [ ] **Step 5 : contrat et commit**

```bash
make openapi && make openapi-check
git add backend frontend/src/api
git commit -m "feat(baselines): capture one package's subtree, and expose the scope

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task A3 : comparer et restaurer dans le périmètre

**Files :**
- Modify : `backend/src/ea/domain/baselines.py` (nouvelle `restricted_to` ; `RestorePlan.frozen_by`, `is_blocked`)
- Modify : `backend/src/ea/domain/package_versions.py` (alias `FreezeLookup`)
- Modify : `backend/src/ea/domain/ports.py` (`current_snapshot`)
- Modify : `backend/src/ea/repositories/baseline_store.py` (`_current_snapshot`, `current_snapshot`, `_plan`)
- Modify : `backend/src/ea/services/baselines.py` (supprimer `refuse_scoped` ; `__init__`, `diff`, `restore`)
- Modify : `backend/src/ea/main.py` (les deux constructions de `BaselineService`, lignes ~531 et ~927 : `frozen_by=package_service.frozen_by`)
- Modify : `backend/src/ea/api/schemas.py` (`RestorePlanRead.frozen_by`)
- Modify : `backend/src/ea/api/baselines.py` (docstrings : diff et restauration d'un sous-arbre, `409 package_frozen`)
- Modify : `backend/src/ea/mcp/server.py` (docstrings de `diff_baselines` / `restore_baseline`)
- Modify : la fixture `baseline_service` des tests unitaires (un `FreezeLookup` factice, `None` par défaut)
- Test : `backend/tests/unit/test_baselines_domain.py`, `backend/tests/unit/test_baseline_service.py` (réécrire `TestASubtreeBaselineIsNotTheWholeModel`), `backend/tests/e2e/test_baseline_api.py`, `backend/tests/integration/test_baseline_scope.py` (réécrire `test_a_scoped_baseline_is_not_restorable_and_the_bytes_are_measured`), `backend/tests/integration/test_package_publication.py`

**Interfaces :**
- Consumes : `ModelSnapshot`, `plan_restore`, `diff_snapshots`, `plan_digest` (inchangés) ; `BaselineScopedError` (`409 baseline_scoped`, existant, réutilisé pour les deux refus de Q1 et Q4) ; `PackageFrozenError` (`409 package_frozen`, plan 1) ; `PackageService.frozen_by(package_id) -> FrozenBy | None` (PR #135, relu sur `freezes()` en A1) ; `FrozenByRead.of` (plan 1).
- Produces :
  - `FreezeLookup = Callable[[UUID], Awaitable[FrozenBy | None]]` dans `domain/package_versions.py`.
  - `BaselineService.__init__(self, repository, *, frozen_by: FreezeLookup, clock=_utc_now)` — **obligatoire**, sans défaut : un câblage oublié est une erreur de type, pas un gel non prédit. `BaselineService` ne connaît pas les packages, seulement cette fonction (comme `ScopeResolver` pour les analyses).
  - `RestorePlan.frozen_by: FrozenBy | None = None` ; `RestorePlan.is_blocked` devient `bool(self.blocked) or self.frozen_by is not None`. `plan_digest` ne lit pas `frozen_by` : un plan gelé n'est jamais appliqué, et la vérification du gel précède celle du digest.
  - `RestorePlanRead.frozen_by: FrozenByRead | None` (description : « The publication that freezes the subtree this baseline restores: reopen it first. »), rempli par `RestoreRead.of`.
  - `restricted_to(current: ModelSnapshot, *, packages: frozenset[UUID], diagrams: frozenset[UUID], known: frozenset[UUID]) -> ModelSnapshot` (pur) : garde les éléments dont le `package_id` est dans `packages` **ou** dont l'id est dans `known` ; les liens dont **les deux** bouts sont gardés ; les boîtes des `diagrams` ; `package_paths` inchangé.
  - `BaselineRepository.current_snapshot(self, scope: UUID | None = None, *, known: frozenset[UUID] = frozenset()) -> ModelSnapshot` — avec `scope`, le sous-arbre **actuel** (`{scope} | descendants_of`) et ses diagrammes, lus dans le même instant que le modèle, puis `restricted_to`.
  - `_plan` d'une baseline scopée : `current = restricted_to(..., known=ids des éléments de la baseline)` ; refus `BaselineScopedError("the package this baseline copies no longer exists: it cannot be restored")` si `scope_package_id` n'est plus dans `packages` (Q4).
  - `BaselineService.diff(baseline_id, against=None)` : contre le modèle courant, `current_snapshot(scope=baseline.scope_package_id, known=ids de la baseline)` ; contre une autre baseline, `BaselineScopedError("a baseline compares only with one of the same scope")` si les deux `scope_package_id` diffèrent (Q1).
  - `BaselineService.restore` : plus de refus de périmètre ; `keep_new` par défaut `False` (Q2). Pour une baseline scopée, après le calcul du plan (dry run **et** application, donc relu au moment d'appliquer) : `plan = replace(plan, frozen_by=await self._frozen_by(baseline.scope_package_id))`. En dry run, le plan bloqué est rendu tel quel. En application, l'ordre est : (1) `plan.frozen_by` → `PackageFrozenError("the subtree of this baseline is published (package <id>, <version>): reopen it first")` (ids et version, jamais un nom), avec une ligne de log `action=restore_blocked_frozen` (`baseline_id`, `package_id`, `version`) et sans événement d'audit, puisque rien n'a été tenté en base ; (2) digest ; (3) `plan.blocked` (éléments protégés), comme aujourd'hui. Le dépôt n'est pas appelé quand (1) refuse. Une publication entre ce contrôle et l'écriture reste refusée en bloc par les triggers : le `ROW EXCLUSIVE` d'`apply_restore` attend la publication.

- [ ] **Step 1 : les tests qui échouent**

`backend/tests/unit/test_baselines_domain.py` :

```python
class TestRestrictingTheModelToASubtree:
    def test_it_keeps_the_subtree_its_inner_links_and_its_boxes(self):
        kept = restricted_to(model, packages=frozenset({finance}), diagrams=frozenset({d1}), known=frozenset())
        assert {e.id for e in kept.elements} == {inside_a.id, inside_b.id}
        assert {r.id for r in kept.relationships} == {a_to_b.id}          # a_to_outside dropped
        assert {b.diagram_id for b in kept.boxes} == {d1}

    def test_an_element_the_baseline_knows_is_kept_wherever_it_is_now(self):
        kept = restricted_to(model, packages=frozenset({finance}), diagrams=frozenset(), known=frozenset({moved_out.id}))
        assert moved_out.id in {e.id for e in kept.elements}

    def test_an_empty_scope_keeps_nothing(self):
        assert restricted_to(model, packages=frozenset(), diagrams=frozenset(), known=frozenset()).elements == ()
```

`backend/tests/unit/test_baseline_service.py`, la classe réécrite `TestASubtreeBaselineIsComparedInItsScope` :

```python
async def test_diff_against_the_model_reads_the_scope_and_what_the_baseline_knows(self, baseline_service, baseline_store):
    scoped = await self.scoped(baseline_service, baseline_store)
    await baseline_service.diff(scoped)
    assert baseline_store.current_calls[-1] == (finance, frozenset(known_ids))

async def test_two_baselines_of_different_scopes_are_not_compared(self, baseline_service, baseline_store):
    ordinary = await baseline_service.create(name="whole")
    scoped = await self.scoped(baseline_service, baseline_store)
    with pytest.raises(BaselineScopedError):
        await baseline_service.diff(ordinary.id, against=scoped)
    with pytest.raises(BaselineScopedError):
        await baseline_service.diff(scoped, against=ordinary.id)

async def test_a_scoped_baseline_restores_as_a_dry_run_by_default(self, baseline_service, baseline_store):
    outcome = await baseline_service.restore(await self.scoped(baseline_service, baseline_store))
    assert outcome.applied is False and outcome.digest
    assert outcome.plan.frozen_by is None and not outcome.plan.is_blocked


class TestAFrozenSubtreeIsPredictedNotOnlyRefused:
    """`frozen_by` is a fake `FreezeLookup` answering FrozenBy(finance, "Finance", "v1.2.0") for the scope."""

    async def test_the_dry_run_says_the_plan_is_blocked_and_by_what(self, frozen_service, baseline_store, scoped_id):
        outcome = await frozen_service.restore(scoped_id)
        assert outcome.plan.frozen_by == FrozenBy(finance, "Finance", "v1.2.0")
        assert outcome.plan.is_blocked and outcome.applied is False

    async def test_applying_is_refused_before_the_repository_is_asked_to_write(self, frozen_service, baseline_store, scoped_id):
        digest = (await frozen_service.restore(scoped_id)).digest
        with pytest.raises(PackageFrozenError) as raised:
            await frozen_service.restore(scoped_id, dry_run=False, expected_digest=digest)
        assert baseline_store.applied == []
        assert "Finance" not in str(raised.value) and "v1.2.0" in str(raised.value)

    async def test_the_freeze_is_read_again_when_applying(self, baseline_store, scoped_id):
        answers = iter([None, FrozenBy(finance, "Finance", "v1.2.0")])   # published between the two calls
        service = BaselineService(baseline_store, frozen_by=lambda _: _async(next(answers)))
        digest = (await service.restore(scoped_id)).digest
        with pytest.raises(PackageFrozenError):
            await service.restore(scoped_id, dry_run=False, expected_digest=digest)

    async def test_a_whole_model_baseline_never_asks_for_a_freeze(self, frozen_service, baseline_store):
        ordinary = await frozen_service.create(name="whole")
        assert (await frozen_service.restore(ordinary.id)).plan.frozen_by is None
        assert frozen_service_lookups(frozen_service) == []
```

(Fixtures du fichier : `baseline_service` est construit avec un `FreezeLookup` factice qui rend toujours `None` ; `frozen_service` est le même service avec un `FreezeLookup` factice qui rend le gel ci-dessus et note chaque id demandé ; `scoped_id` est la baseline scopée de `scoped()`. `InMemoryBaselines` gagne une liste `applied` des appels à `apply_restore`. `_async` est une coroutine qui rend une valeur, `frozen_service_lookups` la liste des ids notés.)

`backend/tests/e2e/test_baseline_api.py` (publication réelle par les routes, application câblée comme en production) :

```python
async def test_the_dry_run_of_a_published_version_reports_it_blocked_with_frozen_by(client, published_finance):
    answer = (await client.post(f"/baselines/{version_baseline}/restore", json={})).json()
    assert answer["applied"] is False
    assert answer["plan"]["frozen_by"] == {"package_id": str(finance), "path": "Finance", "version": "v1.0.0"}

async def test_applying_it_is_a_409_package_frozen_and_writes_nothing(client, published_finance):
    digest = (await client.post(f"/baselines/{version_baseline}/restore", json={})).json()["digest"]
    before = (await client.get("/audit")).json()
    answer = await client.post(f"/baselines/{version_baseline}/restore",
                               json={"dry_run": False, "expected_digest": digest})
    assert (answer.status_code, answer.json()["error"]["code"]) == (409, "package_frozen")
    assert (await client.get("/audit")).json() == before
```

`backend/tests/integration/test_baseline_scope.py` (remplace le test qui exigeait le refus) :

```python
async def test_an_element_moved_out_since_is_modified_not_removed(engine_at_head):
    # capture Finance; then move one of its elements to the root
    diff = await service.diff(scoped_id)
    assert [m.id for m in diff.elements_modified] == [moved]
    assert diff.elements_removed == ()

async def test_a_scoped_restore_never_deletes_outside_its_subtree(engine_at_head):
    # capture Finance; then create one element in Finance, one at the root, and a link between two root elements
    outcome = await service.restore(scoped_id)                      # dry run
    assert {e.id for e in outcome.plan.elements_to_delete} == {created_inside}
    await service.restore(scoped_id, dry_run=False, expected_digest=outcome.digest)
    assert await exists(engine_at_head, "elements", created_at_root)
    assert await exists(engine_at_head, "relationships", root_link)

async def test_a_scoped_restore_puts_a_moved_out_element_back(engine_at_head):
    ...
    assert await package_of(engine_at_head, moved) == finance

async def test_a_baseline_whose_package_is_gone_is_diffed_but_not_restored(engine_at_head):
    # capture a never-published package, then delete it (move_contents_to=root)
    await service.diff(scoped_id)
    with pytest.raises(BaselineScopedError):
        await service.restore(scoped_id)
```

`backend/tests/integration/test_package_publication.py` :

```python
async def test_restoring_a_published_version_is_refused_until_it_is_reopened(engine_at_head):
    # `baselines` is a BaselineService wired to a real PackageService.frozen_by, as main.py does
    version = await publish_it(repo, finance)
    outcome = await baselines.restore(version.baseline_id)
    assert outcome.plan.frozen_by.version == version.version and outcome.plan.is_blocked
    with pytest.raises(PackageFrozenError):
        await baselines.restore(version.baseline_id, dry_run=False, expected_digest=outcome.digest)
    await reopen_it(repo, finance)
    outcome = await baselines.restore(version.baseline_id)
    assert outcome.plan.frozen_by is None
    await baselines.restore(version.baseline_id, dry_run=False, expected_digest=outcome.digest)

async def test_the_triggers_still_refuse_when_the_prediction_is_bypassed(engine_at_head):
    """The race: the service checked before the publication; the database is the authority."""
    stale = BaselineService(baseline_repo, frozen_by=lambda _: _async(None))   # predicts nothing
    first = await publish_it(repo, finance)                  # v1.0.0 copies Finance as it was
    await reopen_it(repo, finance)
    await rename(engine_at_head, inside, "renamed")          # the plan back to v1.0.0 is not a no-op
    await publish_it(repo, finance)                          # v1.1.0 in force
    outcome = await stale.restore(first.baseline_id)
    assert outcome.plan.elements_to_update                   # there is something to write
    with pytest.raises(PackageFrozenError):
        await stale.restore(first.baseline_id, dry_run=False, expected_digest=outcome.digest)
    assert await name_of(engine_at_head, inside) == "renamed"
```

- [ ] **Step 2 : les lancer, ils échouent**

Run : `cd backend && uv run pytest tests/unit/test_baselines_domain.py tests/unit/test_baseline_service.py -q`
Expected : FAIL — `ImportError: cannot import name 'restricted_to'`, `BaselineScopedError` levée par `diff(scoped)`, `unexpected keyword argument 'frozen_by'`.

- [ ] **Step 3 : implémenter** selon les interfaces ; supprimer `refuse_scoped` et le refus de `_plan` (une seule source du périmètre : `Baseline.scope_package_id`). Le `LOCK TABLE … ROW EXCLUSIVE` d'`apply_restore` reste la première instruction. Câbler `frozen_by=` dans les deux constructions de `main.py`.

- [ ] **Step 4 : vérifier**

Run : `cd backend && uv run mypy src && uv run pytest tests/unit tests/e2e -q && cd .. && make test-integration`
Expected : PASS, y compris `test_baseline_restore.py` (le chemin global n'a pas bougé : `frozen_by` n'est jamais demandé pour lui).

- [ ] **Step 5 : commit**

```bash
make openapi && make openapi-check   # RestorePlanRead.frozen_by moves the schema
git add backend frontend/src/api
git commit -m "feat(baselines): diff and restore a subtree baseline within its scope

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task A4 : trois outils MCP — `list_package_versions`, `publish_package`, `reopen_package`

**Files :**
- Modify : `backend/src/ea/mcp/server.py` (section « Packages », `INSTRUCTIONS`, imports de `PublicationNotes`, `PublicationAdr`, `ReopeningReason`, `PublishResult`, `PackageVersionRead`, `Bump`)
- Test : `backend/tests/unit/test_mcp_server.py`

**Interfaces :**
- Consumes : `PackageService.versions(package_id)`, `.publish(package_id, *, bump, notes, adr, dry_run)`, `.reopen(package_id, *, reason)` ; `PublishResult.of`, `PackageVersionRead.of` (plan 1).
- Produces :
  - `list_package_versions(package_id: PackageId) -> list[PackageVersionRead]`, `READS`. Docstring : « Every version a package was published under, the latest first: who published it, when, the notes, the ADR of a major, and who reopened it and why. A package with a version in force freezes everything below it. »
  - `publish_package(package_id: PackageId, bump: Bump, notes: PublicationNotes, adr: PublicationAdr | None = None, dry_run: Annotated[bool, Field(description="True previews the version and what it would freeze, and writes nothing. Set it to false to publish.")] = True) -> PublishResult`, `EDITS`. Docstring : « Publish a version of a package (vX.Y.Z) and freeze it and everything below it: no element, link, document, diagram or sub-package there can be changed until an approver reopens it. Needs the ea-approver role. A dry run unless `dry_run` is false: call it once as is, show the person the version and the counts, and only then call it again with `dry_run=false`. Notes are required; a major names its ADR. »
  - `reopen_package(package_id: PackageId, reason: ReopeningReason) -> PackageVersionRead`, `EDITS`. Docstring : « Close the version in force so the subtree can be changed again. Needs the ea-approver role and a reason, which is kept in the history. Confirm with the person you are working for before calling it. »
  - `INSTRUCTIONS` gagne un paragraphe qui contient les phrases `publish_package`, `dry_run`, `reopen_package`, `frozen_by` (un élément qui porte `frozen_by` ne s'écrit pas : demander la réouverture plutôt que réessayer).

- [ ] **Step 1 : les tests qui échouent**

Dans `TestTheToolset.test_it_exposes_the_whole_service_and_nothing_else` : `assert len(names) == 79`, commentaire « seventy-six, plus the three of the package versions (docs/adr/0074) », et les trois noms dans l'ensemble.

Nouvelle classe `TestThePackageVersionTools` :

```python
async def test_publish_package_is_a_dry_run_unless_told_false(self, server, an_approver_calls):
    preview = await call(server, "publish_package", package_id=pkg, bump="minor", notes="Première")
    assert preview["published"] is None and preview["preview"]["version"] == "v1.0.0"
    assert (await call(server, "list_package_versions", package_id=pkg)) == []

async def test_publishing_then_reopening_leaves_two_lines_of_history(self, server, an_approver_calls):
    made = await call(server, "publish_package", package_id=pkg, bump="minor", notes="n", dry_run=False)
    assert made["published"]["version"] == "v1.0.0"
    await call(server, "reopen_package", package_id=pkg, reason="correction")
    [only] = await call(server, "list_package_versions", package_id=pkg)
    assert only["reopen_reason"] == "correction"

async def test_an_editor_cannot_publish_or_reopen(self, server):
    with acting_as(an_editor()):
        for tool, args in [("publish_package", {"bump": "patch", "notes": "n", "dry_run": False}),
                           ("reopen_package", {"reason": "r"})]:
            with pytest.raises(ToolError, match="approver"):
                await call(server, tool, package_id=pkg, **args)

async def test_a_major_without_adr_is_refused_with_its_reason(self, server, an_approver_calls):
    with pytest.raises(ToolError, match="ADR"):
        await call(server, "publish_package", package_id=pkg, bump="major", notes="n")

async def test_the_instructions_teach_the_dry_run_and_the_reopening(self):
    for phrase in ("publish_package", "dry_run", "reopen_package", "frozen_by"):
        assert phrase in INSTRUCTIONS, phrase
```

(Adapter `match=` au message réel de `NotAuthorisedError` / `AdrRequiredError`, lu dans `services/caller.py` et `domain/package_versions.py`.)

Annotations : dans `test_every_tool_says_whether_it_writes` rien à ajouter ; ajouter `assert annotations["publish_package"].read_only_hint is False and annotations["publish_package"].destructive_hint is False` et l'équivalent pour `reopen_package`, `list_package_versions.read_only_hint is True`.

`TestTheSameBoundsAsTheHttpAdapter`, trois lignes :

```python
("publish_package", "notes", PublishPackage, "notes"),
("publish_package", "adr", PublishPackage, "adr"),
("reopen_package", "reason", ReopenPackage, "reason"),
```

- [ ] **Step 2 : les lancer, ils échouent**

Run : `cd backend && uv run pytest tests/unit/test_mcp_server.py -q -k "toolset or PackageVersion or SameBounds"`
Expected : FAIL — `76 != 79`, `Unknown tool: publish_package`.

- [ ] **Step 3 : implémenter** les trois fonctions dans la section « Packages », après `move_elements`, sur le modèle de `create_package` (`get_packages()`, `@server.tool(annotations=...)`, `@speaking_plainly`).

- [ ] **Step 4 : vérifier**

Run : `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_untrusted_registry.py -q`
Expected : PASS (aucun outil d'intégration externe ne change : le registre reste exact).

- [ ] **Step 5 : commit**

```bash
git add backend
git commit -m "feat(mcp): list_package_versions, publish_package (dry run by default), reopen_package

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task A5 : ADR 0077, CLAUDE.md côté serveur, porte du lot A

**Files :**
- Create : `docs/adr/0077-gel-par-package-lectures-mcp-et-baselines-scopees.md`
- Modify : `CLAUDE.md`, `docs/adr/0074-gel-par-package-et-triggers.md` (seulement l'en-tête : `Remplacé en partie par 0077` sur la conséquence « Les baselines scopées ne se comparent ni ne se restaurent » — l'historique n'est pas réécrit)

- [ ] **Step 1 : l'ADR 0077** — gabarit `docs/adr/TEMPLATE.md`, statut `Proposition`, `affects:` les fichiers des tâches A1 à A4. Décision : la carte `Freezes` lue une fois par requête et passée en argument obligatoire (pourquoi pas une jointure SQL par ligne : une CTE récursive par élément sur une page de 500 ; pourquoi pas de défaut : une porte oubliée dirait « non gelé ») ; `allowed_transitions` vide sous un gel ; la restriction `restricted_to` (sous-arbre actuel ∪ éléments connus de la baseline, liens aux deux bouts gardés) ; même périmètre exigé pour comparer deux baselines ; restauration d'un sous-arbre gelé **prédite** par le dry run (`RestorePlan.frozen_by`, plan bloqué comme pour les éléments protégés de l'ADR 0049) et refusée avant écriture (`409 package_frozen`), les triggers restant l'autorité pour la course ; `FreezeLookup` injecté et obligatoire dans `BaselineService` ; package disparu ; les trois outils MCP. Alternatives écartées : matérialiser `frozen_by` sur `elements` (déjà écartée par 0074) ; restreindre l'autre baseline au périmètre (Q1) ; laisser les seuls triggers refuser sans prédiction (l'ancien défaut de Q3) ; prédire aussi pour une baseline globale (partiel, donc trompeur). Conséquences : la carte et l'élément sont lus dans deux transactions (un badge peut retarder d'une publication, jamais le refus) ; un refus prédit n'écrit pas d'événement `package.write_refused_frozen` (rien n'a été tenté en base), seulement le log `restore_blocked_frozen` ; les décisions Q1 à Q5 du 2026-10-17.

- [ ] **Step 2 : CLAUDE.md** — (a) « Project status » : « seventy-six tools » → « seventy-nine », et ajouter `list_package_versions`, `publish_package` (dry run until told otherwise), `reopen_package` à l'énumération ; (b) « Baselines freeze the model… » : remplacer « A scoped baseline cannot be diffed or restored until the scoped diff exists (`BaselineScopedError`, `409 baseline_scoped`) » par la règle de l'ADR 0077 (comparée dans son périmètre, à une baseline du même périmètre seulement ; restaurée dans son sous-arbre, jamais au-delà ; tant que le sous-arbre est gelé, son dry run est bloqué avec `frozen_by` et l'appliquer est un `409 package_frozen`) ; (c) « A published subtree is frozen by the database » : une phrase — chaque `ElementRead` / `DiagramRead` porte `frozen_by`, lu par `PackageService.freezes()`, argument obligatoire de `of`.

- [ ] **Step 3 : la porte**

Run (racine) : `make check && make test-integration && make audit`
Expected : vert. Une alerte d'`audit` étrangère au lot est notée dans la PR, pas corrigée ici.

- [ ] **Step 4 : commit, puis PR du lot A** (description : quoi, pourquoi, l'écart 1 et les décisions Q1–Q5 du 2026-10-17)

```bash
git add docs CLAUDE.md
git commit -m "docs(adr): 0077, element and diagram reads, MCP versions, scoped baselines

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Lot B — SPA (PR 2, après la fusion du lot A)

Chaque tâche de ce lot part du client régénéré par le lot A : `components['schemas']['ElementRead']['frozen_by']`, `PackageRead['allowed_actions']`, `BaselineRead['scope_path']`, les routes `/packages/{package_id}/publish|reopen|versions`. Aucun type n'est écrit à la main.

### Task B1 : lire un gel, et le montrer

**Files :**
- Create : `frontend/src/features/packages/freeze.ts`
- Create : `frontend/src/features/packages/FreezeBadge.vue`
- Modify : `frontend/src/features/packages/PackageTree.vue` (badge par nœud), `frontend/src/features/elements/ElementCatalogue.vue` (badge par ligne), `frontend/src/features/elements/ElementDetail.vue` (badge en tête de fiche)
- Test : `frontend/tests/freeze.spec.ts`, `frontend/tests/FreezeBadge.spec.ts`, `frontend/tests/PackageTreeComponent.spec.ts`, `frontend/tests/ElementCatalogue.spec.ts`

**Interfaces :**
- Produces :
  - `type FrozenBy = components['schemas']['FrozenByRead']`
  - `isFrozen(subject: { frozen_by?: FrozenBy | null } | null | undefined): boolean`
  - `frozenLabel(frozenBy: FrozenBy): string` → `` `gelé par ${frozenBy.path} ${frozenBy.version}` ``
  - `FreezeBadge.vue`, props `{ ownVersion?: string | null; frozenBy: FrozenBy | null }` : si `ownVersion`, cadenas (SVG en ligne, `aria-hidden`) + `vX.Y.Z` en `--accent`, `aria-label="Publié en vX.Y.Z"` ; sinon si `frozenBy`, cadenas grisé (`--muted`) + `frozenLabel(frozenBy)` ; sinon rien. Le badge de revue existant n'est pas touché.

- [ ] **Step 1 : les tests qui échouent**

```ts
// freeze.spec.ts
it('reads a freeze from what the API sent, and nothing else', () => {
  expect(isFrozen({ frozen_by: null })).toBe(false)
  expect(isFrozen({ frozen_by: { package_id: 'p', path: 'Finance', version: 'v1.2.0' } })).toBe(true)
  expect(isFrozen(null)).toBe(false)
  expect(frozenLabel({ package_id: 'p', path: 'Finance / Budget', version: 'v2.0.0' })).toBe('gelé par Finance / Budget v2.0.0')
})

// FreezeBadge.spec.ts
it('names its own version on the published package', () => {
  render(FreezeBadge, { props: { ownVersion: 'v1.3.0', frozenBy: frozen('Finance', 'v1.3.0') } })
  expect(screen.getByLabelText('Publié en v1.3.0')).toBeTruthy()
  expect(screen.queryByText(/gelé par/)).toBeNull()
})
it('names the ancestor on a descendant', () => {
  render(FreezeBadge, { props: { ownVersion: null, frozenBy: frozen('Finance', 'v1.3.0') } })
  expect(screen.getByText('gelé par Finance v1.3.0')).toBeTruthy()
})
it('draws nothing when nothing freezes it', () => {
  const { container } = render(FreezeBadge, { props: { frozenBy: null } })
  expect(container.textContent).toBe('')
})

// PackageTreeComponent.spec.ts
it('shows the version on the published node and the freeze on its children', ...)
// ElementCatalogue.spec.ts
it('marks a row whose element is frozen', ...)  // fixture item with frozen_by → 'gelé par Finance v1.0.0' in that row only
```

- [ ] **Step 2 : lancer** `cd frontend && npm test -- --run tests/freeze.spec.ts tests/FreezeBadge.spec.ts tests/PackageTreeComponent.spec.ts tests/ElementCatalogue.spec.ts` — Expected : FAIL (modules absents, texte absent).

- [ ] **Step 3 : implémenter** selon les interfaces ; les nœuds de `PackageTree` passent `node.package.frozen_version` et `node.package.frozen_by`.

- [ ] **Step 4 : vérifier** `npm test -- --run && npm run lint && npm run typecheck` — Expected : PASS, `noVHtml.spec.ts` compris.

- [ ] **Step 5 : commit** `feat(spa): freeze badges on packages and elements`

---

### Task B2 : le mode « Arbre » du catalogue

**Files :**
- Create : `frontend/src/features/elements/CatalogueTree.vue`
- Create : `frontend/src/features/elements/useTreeLeaves.ts`
- Modify : `frontend/src/features/elements/ElementCatalogue.vue` (bascule « Tableau / Arbre », `?vue=arbre`)
- Test : `frontend/tests/useTreeLeaves.spec.ts`, `frontend/tests/CatalogueTree.spec.ts`, `frontend/tests/ElementCatalogue.spec.ts`

**Interfaces :**
- Consumes : `buildTree`, `visibleNodes`, `ancestorsOf`, `ROOT` (`packageTree.ts`) ; `FreezeBadge` (B1) ; `useLatestRequest()` ; `GET /elements` avec `package`, `include_subpackages=false`, `root_only=true`, `limit`, `offset`.
- Produces :
  - `useTreeLeaves()` → `{ leavesOf(key: string): Leaves; open(key: string): Promise<void>; more(key: string): Promise<void>; forget(key: string): void; reloadAll(): Promise<void> }` où `type Leaves = { items: ElementRead[]; total: number; status: 'idle'|'loading'|'ready'|'error'; error: string }` ; `key` = un id de package ou `ROOT`. **Une instance de `useLatestRequest()` par clé ouverte** (créée à la première ouverture, gardée dans une `Map`). Page de 50 ; `more` charge la suivante (`offset = items.length`) et ajoute. `ROOT` lit `root_only=true` ; un package lit `package=<id>&include_subpackages=false`.
  - `CatalogueTree.vue`, props `{ packages: PackageRead[]; selected: string; element: string }`, émet `select: [packageKey: string]` et `open: [elementId: string]`. Nœuds : « Racine (sans paquet) » puis l'arbre des packages ; sous chaque nœud déplié, ses feuilles (nom, type, badge de revue, `FreezeBadge`), puis « Afficher plus (n restants) » si `items.length < total`. Nœuds dépliés dans une `ref` ; le chemin vers `selected` est déplié au montage (`ancestorsOf`). Rôles ARIA `tree` / `treeitem` comme `PackageTree.vue`.
  - `ElementCatalogue.vue` : `?vue=arbre` montre `CatalogueTree` à la place du tableau (le reste de l'URL est commun) ; sélectionner un nœud fait `router.push` de `?package=` ; ouvrir une feuille fait `router.push` de `?element=` (la fiche existante s'ouvre) ; la bascule fait `router.replace` de `?vue=`. Après une écriture (création, déplacement, publication), `leaves.reloadAll()`.

- [ ] **Step 1 : les tests qui échouent**

```ts
// useTreeLeaves.spec.ts  (api mocké comme dans useElementCatalogue.spec.ts)
it('loads a package\'s direct elements only, and the root\'s unfiled ones', async () => {
  await leaves.open('pkg-1'); await leaves.open(ROOT)
  expect(calls).toEqual([
    { package: 'pkg-1', include_subpackages: false, limit: 50, offset: 0 },
    { root_only: true, limit: 50, offset: 0 },
  ])
})
it('one failing node does not cancel the others', async () => {
  failNext('pkg-1'); await Promise.all([leaves.open('pkg-1'), leaves.open('pkg-2')])
  expect(leaves.leavesOf('pkg-1').status).toBe('error')
  expect(leaves.leavesOf('pkg-2').status).toBe('ready')
})
it('asking a node again drops the answer still in flight', ...)   // second open of the same key wins
it('more appends the next page', ...)                              // offset 50, items concatenated, total kept

// CatalogueTree.spec.ts
it('opens the path to the selected package from the URL', ...)
it('shows an element\'s freeze and review badges on its leaf', ...)
it('offers more only while the node has more than it shows', ...)
it('a package id that names nothing selects nothing and breaks nothing', ...)

// ElementCatalogue.spec.ts
it('switches to the tree with ?vue=arbre and keeps ?package= and ?element=', ...)
it('opening a leaf pushes ?element= and opens the sheet', ...)
```

- [ ] **Step 2 : lancer** `npm test -- --run tests/useTreeLeaves.spec.ts tests/CatalogueTree.spec.ts tests/ElementCatalogue.spec.ts` — Expected : FAIL.

- [ ] **Step 3 : implémenter** selon les interfaces ; textes : « Tableau », « Arbre », « Afficher plus ({n} restants) », « Chargement… », l'erreur par `messageOf`.

- [ ] **Step 4 : vérifier** `npm test -- --run && npm run lint && npm run typecheck` — Expected : PASS.

- [ ] **Step 5 : commit** `feat(spa): a tree mode in the catalogue, loaded node by node`

---

### Task B3 : publier, rouvrir, et l'historique d'un package

**Files :**
- Create : `frontend/src/features/packages/usePackageVersions.ts`
- Create : `frontend/src/features/packages/PublishDialog.vue`
- Create : `frontend/src/features/packages/ReopenDialog.vue`
- Create : `frontend/src/features/packages/PackageVersions.vue`
- Rename+Modify : `frontend/src/features/history/ElementHistory.vue` → `EntityHistory.vue`, props `{ kind: EntityKind; entityId: string }` (mettre à jour ses usages et `tests/ElementHistory.spec.ts` → `EntityHistory.spec.ts`)
- Modify : `frontend/src/features/packages/PackageManager.vue` (boutons « Publier… » / « Rouvrir… », panneau des versions et de l'historique du package choisi)
- Test : `frontend/tests/usePackageVersions.spec.ts`, `frontend/tests/PublishDialog.spec.ts`, `frontend/tests/ReopenDialog.spec.ts`, `frontend/tests/PackageManager.spec.ts`, `frontend/tests/EntityHistory.spec.ts`

**Interfaces :**
- Consumes : `PackageRead.allowed_actions`, `PackageRead.frozen_version` ; `usePackages().load` ; `useLatestRequest()`.
- Produces :
  - `usePackageVersions()` → `{ versions: Ref<PackageVersionRead[]>; status; error; load(packageId: string): Promise<void>; preview(packageId: string, body: PublishBody): Promise<PublicationPreviewRead>; publish(packageId: string, body: PublishBody): Promise<PackageVersionRead>; reopen(packageId: string, reason: string): Promise<PackageVersionRead> }`, `type PublishBody = Omit<components['schemas']['PublishPackage'], 'dry_run'>`. `preview` envoie `dry_run: true` à travers sa propre instance de `useLatestRequest()` (le dernier aperçu gagne) ; `publish` envoie **le même corps** avec `dry_run: false` ; `publish` et `reopen` lèvent le refus (l'écran l'affiche).
  - `PublishDialog.vue`, props `{ package: PackageRead }`, émet `done` / `cancel` : niveau (« Correctif », « Mineure », « Majeure » → `patch|minor|major`), notes (textarea), champ « ADR » visible seulement pour « Majeure » ; à l'ouverture et à chaque changement de niveau, aperçu : « {version} — {elements} éléments, {relationships} liens, {diagrams} diagrammes, {boxes} boîtes seront gelés. » ; bouton « Publier {version} » ; succès → `done`, refus → message sous le formulaire, bouton réactivé.
  - `ReopenDialog.vue`, props `{ package: PackageRead }` : « Rouvrir {frozen_version} », motif (textarea), bouton « Rouvrir » ; refus affiché.
  - `PackageManager.vue` : un bouton par entrée de `current.allowed_actions` (`publish` → « Publier… », `reopen` → « Rouvrir… »), aucun autre test de rôle ; après `done`, `store.load()` puis émettre `changed` (le catalogue appelle `leaves.reloadAll()` et recharge sa liste). Panneau `PackageVersions` (version, niveau, notes en texte, ADR, publié par / le, rouvert par / le / motif) puis `<EntityHistory kind="package" :entity-id="current.id" />`.

- [ ] **Step 1 : les tests qui échouent**

```ts
// PublishDialog.spec.ts
it('previews before it publishes, and publishes exactly what it previewed', async () => {
  // pick Mineure, type notes → POST {bump:'minor', notes:'n', adr:null, dry_run:true}
  await user.click(screen.getByRole('button', { name: 'Publier v1.3.0' }))
  expect(lastBody()).toEqual({ bump: 'minor', notes: 'n', adr: null, dry_run: false })
})
it('asks for the ADR only on a major', ...)
it('shows the refusal of a second publication and reloads the tree', async () => {
  respond409('package_already_frozen', 'already published')
  await user.click(screen.getByRole('button', { name: /Publier/ }))
  expect(screen.getByText('already published')).toBeTruthy()
})
it('renders notes as text, never as markup', ...)   // '<b>x</b>' visible verbatim
// ReopenDialog.spec.ts
it('sends the reason and shows a refusal as it came', ...)
// PackageManager.spec.ts
it('draws one button per allowed action and none otherwise', () => {
  // allowed_actions [] → neither button; ['publish'] → « Publier… » only; ['reopen'] → « Rouvrir… » only
})
// usePackageVersions.spec.ts
it('a newer preview replaces an older one still in flight', ...)
// EntityHistory.spec.ts
it('loads the history of the kind and entity it is given', ...)  // {kind:'package', entity:id}
```

- [ ] **Step 2 : lancer** `npm test -- --run tests/PublishDialog.spec.ts tests/ReopenDialog.spec.ts tests/PackageManager.spec.ts tests/usePackageVersions.spec.ts tests/EntityHistory.spec.ts` — Expected : FAIL.

- [ ] **Step 3 : implémenter** selon les interfaces.

- [ ] **Step 4 : vérifier** `npm test -- --run && npm run lint && npm run typecheck` — Expected : PASS.

- [ ] **Step 5 : commit** `feat(spa): publish and reopen a package, its versions and its history`

---

### Task B4 : pas de contrôle d'écriture dans un sous-arbre gelé

**Files :**
- Modify : `frontend/src/features/elements/ElementCatalogue.vue` (création, glisser-déposer, cible de rangement), `frontend/src/features/elements/ElementDetail.vue` (modifier, supprimer, documents, stéréotypes, références, liens), `frontend/src/features/packages/PackageManager.vue` (créer un sous-package, renommer, déplacer, supprimer), `frontend/src/features/diagrams/DiagramsSection.vue` (la palette, la poignée de lien, la mise en page, renommer, supprimer — le même chemin que pour un lecteur)
- Test : `frontend/tests/ElementCatalogue.spec.ts`, `frontend/tests/ElementDetail.spec.ts`, `frontend/tests/PackageManager.spec.ts`, `frontend/tests/DiagramsSection.spec.ts`

**Interfaces :**
- Consumes : `isFrozen` (B1) ; `useMe().canWrite`.
- Produces : une seule règle d'affichage par écran, `const writable = computed(() => canWrite.value && !isFrozen(<sujet>))`, où le sujet est le package choisi (catalogue, gestion des packages), l'élément ouvert (fiche) ou le diagramme ouvert (diagrammes). Les options de la cible de rangement dont le package `isFrozen` sont `disabled`. Le bandeau de revue ne change pas : il reçoit `allowed_transitions: []` de l'API (A1). Un refus `package_frozen` qui arrive quand même (course) est affiché par `messageOf`, comme tout refus.

- [ ] **Step 1 : les tests qui échouent**

```ts
it('an editor sees no write control on an element frozen by its package', ...)   // ElementDetail: no « Modifier », « Supprimer », « Joindre »
it('an editor sees no create, rename, move or delete in a frozen package', ...)  // PackageManager with selected.frozen_by set
it('a frozen package cannot be chosen as a filing target', ...)                  // option disabled
it('a frozen diagram opens read-only for an editor', ...)                        // DiagramsSection: no palette
it('the same controls are back once the API stops sending frozen_by', ...)
```

- [ ] **Step 2 : lancer** les quatre spécifications — Expected : FAIL.

- [ ] **Step 3 : implémenter** ; ne tester nulle part le rôle `ea-approver` ni un ancêtre : seul `frozen_by` reçu compte.

- [ ] **Step 4 : vérifier** `npm test -- --run && npm run lint && npm run typecheck` — Expected : PASS.

- [ ] **Step 5 : commit** `feat(spa): hide write controls inside a published subtree`

---

### Task B5 : le périmètre dans les baselines

**Files :**
- Modify : `frontend/src/features/baselines/useBaselines.ts` (`capture(name, description, packageId?)`)
- Modify : `frontend/src/features/baselines/BaselinesSection.vue` (colonne, filtre, capture), `frontend/src/features/baselines/RestoreDialog.vue` (titre, gel prédit)
- Test : `frontend/tests/BaselinesSection.spec.ts`, `frontend/tests/useBaselines.spec.ts`, `frontend/tests/RestoreDialog.spec.ts`

**Interfaces :**
- Consumes : `BaselineRead.scope_package_id`, `.scope_path` (A2) ; `RestorePlanRead.frozen_by` (A3) ; `frozenLabel` (B1) ; `usePackages()`.
- Produces :
  - `capture(name: string, description: string, packageId?: string): Promise<void>` → `POST /baselines` avec `package_id` quand donné.
  - Colonne « Périmètre » : `scope_path` ou « Modèle entier ». Filtre « Périmètre » dans `?perimetre=` (`''` toutes, `modele` le modèle entier, sinon un id de package), `router.replace`. Formulaire de capture : liste « Périmètre » (« Modèle entier » + les packages par chemin). Le sélecteur « comparer avec » n'offre que les baselines du même `scope_package_id` (les données, pas une règle : l'API refuse le reste, `baseline_scoped`). `RestoreDialog` titre « Restaurer le sous-arbre {scope_path} » pour une baseline scopée.
  - **Gel prédit (décision Q3).** Quand le dry run rend `plan.frozen_by` (A3, `RestorePlanRead.frozen_by`), `RestoreDialog` désactive le bouton de confirmation et affiche, à côté, `` `${frozenLabel(plan.frozen_by)} — rouvrir d'abord` `` (soit « gelé par Finance v1.0.0 — rouvrir d'abord ») ; `frozenLabel` vient de `features/packages/freeze.ts` (B1). La condition existante `plan.blocked_count === 0` devient `plan.blocked_count === 0 && plan.frozen_by === null`. Le SPA ne calcule rien : il lit ce que le dry run a dit. Un `409 package_frozen` reçu quand même à l'application (la course) s'affiche par `messageOf`, comme `restore_blocked` aujourd'hui, et le plan est relu.

- [ ] **Step 1 : les tests qui échouent**

```ts
it('shows the scope of each baseline, or the whole model', ...)
it('filters by scope through ?perimetre=', ...)              // 'modele' keeps unscoped only; an id keeps that scope
it('captures a subtree when a package is chosen', ...)      // body {name, description, package_id}
it('offers to compare only with baselines of the same scope', ...)
it('names the subtree in the restore dialog', ...)

// RestoreDialog.spec.ts
it('greys the confirmation of a frozen subtree and says to reopen it first', async () => {
  dryRunAnswers({ plan: { ...emptyPlan, frozen_by: { package_id: 'p', path: 'Finance', version: 'v1.0.0' } } })
  await typeTheBaselineName()
  expect(screen.getByRole('button', { name: /Restaurer/ })).toBeDisabled()
  expect(screen.getByText("gelé par Finance v1.0.0 — rouvrir d'abord")).toBeTruthy()
})
it('enables the confirmation again once the dry run no longer reports a freeze', ...)  // frozen_by: null
it('shows a package_frozen refusal that arrives anyway, and reads the plan again', ...) // 409 on apply → message, second dry run
```

- [ ] **Step 2 : lancer** — Expected : FAIL.

- [ ] **Step 3 : implémenter** selon les interfaces.

- [ ] **Step 4 : vérifier** `npm test -- --run && npm run lint && npm run typecheck` — Expected : PASS.

- [ ] **Step 5 : commit** `feat(spa): baseline scope, filter, subtree capture and a predicted freeze`

---

# Lot C — revue

### Task C1 : la revue de sécurité de toute la fonctionnalité, et la documentation

**Files :**
- Modify : `backend/tests/integration/test_package_freeze_security.py` (docstring : les lignes MCP et SPA du modèle de menaces pointent leurs tests)
- Modify : `docs/adr/0077-…md` (conséquences côté SPA, modèle de menaces complété, statut inchangé `Proposition`)
- Modify : `docs/adr/0074-gel-par-package-et-triggers.md` (rien d'autre que le renvoi de l'en-tête posé en A5)
- Modify : `CLAUDE.md` (« Project status » : le mode « Arbre » du catalogue et les dialogues de publication ; « Adding a section… » inchangé, aucune section nouvelle)
- Modify : `docs/audit-retention.md` (une phrase : une baseline scopée créée à la main se supprime ; celle d'une version, jamais)

- [ ] **Step 1 : relire le modèle de menaces** de la spec § 10 avec l'ADR 0074 et compléter, dans l'ADR 0077, la colonne « Preuve » des lignes que ce plan touche :

| Menace | Preuve dans ce plan |
|---|---|
| Un éditeur publie ou rouvre (MCP) | `test_mcp_server.py::TestThePackageVersionTools::test_an_editor_cannot_publish_or_reopen` |
| Un agent MCP gèle par erreur | `test_publish_package_is_a_dry_run_unless_told_false`, annotations `EDITS`, `INSTRUCTIONS` |
| Restaurer dans un sous-arbre gelé | prédit : `test_baseline_service.py::TestAFrozenSubtreeIsPredictedNotOnlyRefused`, `test_baseline_api.py::test_the_dry_run_of_a_published_version_reports_it_blocked_with_frozen_by` / `::test_applying_it_is_a_409_package_frozen_and_writes_nothing`, `RestoreDialog.spec.ts::greys the confirmation of a frozen subtree…` ; course : `test_package_publication.py::test_the_triggers_still_refuse_when_the_prediction_is_bypassed` ; bout à bout : `::test_restoring_a_published_version_is_refused_until_it_is_reopened` |
| Une restauration scopée qui déborde | `test_baseline_scope.py::test_a_scoped_restore_never_deletes_outside_its_subtree` |
| Injection par `notes`, `adr`, `reason` (SPA) | `PublishDialog.spec.ts::renders notes as text…`, `noVHtml.spec.ts` |
| Le SPA offre une écriture gelée | `ElementDetail.spec.ts`, `PackageManager.spec.ts`, `DiagramsSection.spec.ts` (B4) ; `allowed_transitions` vide (A1) |
| Bornes qui divergent entre HTTP et MCP | `TestTheSameBoundsAsTheHttpAdapter` (trois lignes de A4) |

- [ ] **Step 2 : la mutation des gardes.** Pour chaque mutant : l'appliquer à la main, lancer la commande, lire qu'**au moins un** test échoue, puis `git checkout -- <fichier>`. Un mutant qui survit reçoit son test avant d'aller plus loin. Résultat consigné dans la description de la PR (mutant, test qui l'a tué).

| # | Mutant | Commande | Doit échouer |
|---|---|---|---|
| M1 | `services/packages.py` : `publish` commence par `require_editor()` | `uv run pytest tests/unit -q -k "publish"` | `test_an_editor_cannot_publish_or_reopen`, tests du plan 1 |
| M2 | idem pour `reopen` | même | même |
| M3 | `mcp/server.py` : `publish_package(..., dry_run=False)` par défaut | `uv run pytest tests/unit/test_mcp_server.py -q` | `test_publish_package_is_a_dry_run_unless_told_false` |
| M4 | `ElementRead.of` ignore `freezes` (`frozen_by=None`) | `uv run pytest tests/unit tests/e2e -q -k frozen` | `test_element_read_freeze.py`, `test_an_element_below_a_published_package_says_who_froze_it` |
| M5 | `allowed_transitions` ignore `frozen` | `uv run pytest tests/unit/test_approval.py -q` | `test_a_frozen_element_is_offered_no_transition_whoever_asks` |
| M6 | `restricted_to` garde les liens à **un** bout gardé | `uv run pytest tests/unit/test_baselines_domain.py -q` + `make test-integration` | `test_it_keeps_the_subtree_its_inner_links_and_its_boxes`, `test_a_scoped_restore_never_deletes_outside_its_subtree` |
| M7 | `_plan` d'une baseline scopée passe le modèle courant entier | `make test-integration` | `test_a_scoped_restore_never_deletes_outside_its_subtree` |
| M8 | `diff` n'exige plus le même périmètre | `uv run pytest tests/unit/test_baseline_service.py -q` | `test_two_baselines_of_different_scopes_are_not_compared` |
| M9 | `PackageService.freezes` sans `require_caller()` | `uv run pytest tests/unit/test_service_guards.py tests/unit/test_package_service.py -q` | `test_service_guards`, `test_nobody_calling_is_refused` |
| M10 | `PackageManager.vue` dessine « Publier… » quand `canWrite` | `npm test -- --run tests/PackageManager.spec.ts` | `draws one button per allowed action and none otherwise` |
| M11 | `PublishDialog.vue` envoie `dry_run: false` dès l'aperçu | `npm test -- --run tests/PublishDialog.spec.ts` | `previews before it publishes…` |
| M12 | `ElementDetail.vue` : `writable` ignore `isFrozen` | `npm test -- --run tests/ElementDetail.spec.ts` | `an editor sees no write control on an element frozen…` |
| M13 | `services/baselines.py` : `restore` ne demande plus `self._frozen_by(...)` (plan jamais marqué gelé) | `uv run pytest tests/unit/test_baseline_service.py tests/e2e/test_baseline_api.py -q` | `test_the_dry_run_says_the_plan_is_blocked_and_by_what`, `test_the_dry_run_of_a_published_version_reports_it_blocked_with_frozen_by` |
| M14 | `services/baselines.py` : en application, le refus `plan.frozen_by` est retiré (le dry run prédit encore) | même commande | `test_applying_is_refused_before_the_repository_is_asked_to_write` (le double enregistre l'appel d'écriture) |
| M15 | `services/baselines.py` : le gel est lu une seule fois, au dry run, et réutilisé à l'application | `uv run pytest tests/unit/test_baseline_service.py -q` | `test_the_freeze_is_read_again_when_applying` |
| M16 | `domain/baselines.py` : `RestorePlan.is_blocked` ignore `frozen_by` | `uv run pytest tests/unit/test_baseline_service.py -q` | `test_the_dry_run_says_the_plan_is_blocked_and_by_what` |
| M17 | `RestoreDialog.vue` : la condition de confirmation ignore `plan.frozen_by` | `npm test -- --run tests/RestoreDialog.spec.ts` | `greys the confirmation of a frozen subtree and says to reopen it first` |

- [ ] **Step 3 : la porte complète**

Run (racine) : `make check && make test-integration && make audit`
Expected : vert ; une alerte étrangère est notée, une alerte sur ce code est levée à la ligne (`# nosec Bxxx` et la raison), jamais par exclusion globale.

- [ ] **Step 4 : la revue indépendante** — skill `security-review` sur la branche du lot B (le lot A est déjà sur `main` : lui donner aussi le diff de la PR du lot A), puis skill `code-review` niveau `high`. Chaque constat est corrigé avec un test ou justifié par écrit dans la PR. Rien n'est coché tant que la commande n'a pas tourné et que son résultat n'a pas été lu.

- [ ] **Step 5 : documentation, commit, PR du lot B** (description : par ligne du modèle de menaces, la parade et le test ; le tableau des mutants)

```bash
git add docs CLAUDE.md backend/tests
git commit -m "docs+test: whole-feature security review of the package freeze

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Auto-revue du plan

**Couverture de la spec.** § 4 diff / `plan_restore` dans le périmètre : A3. § 5 `ElementRead` / `DiagramRead.frozen_by` : A1 ; `GET /baselines` périmètre et `POST /baselines` `package_id` : A2 ; MCP 76 → 79 avec bornes partagées : A4 ; `make openapi` : A1, A2, A3. § 6 mode Arbre, chargement par nœud, une `useLatestRequest` par nœud, racine sans package, `?package=` / `?element=`, nœuds dépliés dans une `ref` : B2 ; badges version + cadenas, « gelé par X vN » lu dans `frozen_by` : B1 ; boutons d'après `allowed_actions`, dialogue niveau / notes / ADR si majeure / aperçu : B3 ; contrôles masqués : B4 ; colonne et filtre « Périmètre », restauration limitée au sous-arbre : B5 ; texte jamais `v-html` : B1, B3. § 7 Vitest arbre / badges / dialogue / actions : B1–B4. § 9 fiche d'un package avec versions et historique (`features/history`) : B3. § 10 revue obligatoire : C1. Les parties de la spec déjà faites au plan 1 (données, triggers, publier / rouvrir HTTP, audit) ne sont pas répétées.

**Lignes qui ne décident rien.** Aucune « TBD » ; deux renvois explicites à lire un fichier voisin (l'enveloppe d'erreur exacte dans `test_baseline_api.py`, le message exact de `NotAuthorisedError` / `AdrRequiredError` pour `match=`), chacun avec la règle à suivre.

**Cohérence des types.** `Freezes`, `PackageService.freezes()`, `ElementRead.of(element, *, freezes)`, `DiagramRead.of(detail, *, freezes)`, `allowed_transitions(..., *, frozen)` (A1) ; `BaselineRepository.create(..., scope=)`, `BaselineService.create(..., package_id=)` (A2) ; `restricted_to(current, *, packages, diagrams, known)`, `current_snapshot(scope=None, *, known=frozenset())`, `FreezeLookup`, `BaselineService(repository, *, frozen_by: FreezeLookup, clock=…)`, `RestorePlan.frozen_by: FrozenBy | None` et `is_blocked`, `RestorePlanRead.frozen_by: FrozenByRead | None` (A3), lus par `RestoreDialog` (B5) sous le nom `plan.frozen_by` et avec le `frozenLabel` de B1, et par les mutants M13–M17 (C1) ; le refus d'application est `PackageFrozenError` / `409 package_frozen` partout (A3, B5, C1) ; `isFrozen`, `frozenLabel`, `FreezeBadge` (B1) consommés par B2–B4 ; `useTreeLeaves().leavesOf/open/more/forget/reloadAll` (B2) appelé par B3 (`changed`) ; `usePackageVersions().preview/publish/reopen` (B3). Les mêmes noms apparaissent dans les mutants de C1.

**Review Focus.** 1 → A1 `test_nothing_published_costs_one_query_and_freezes_nothing` ; 2 → A3 `test_an_element_moved_out_since_is_modified_not_removed` ; 3 → A3 `test_a_scoped_restore_never_deletes_outside_its_subtree` ; 4 → B3 `shows the refusal of a second publication and reloads the tree` ; 5 → B2 `one failing node does not cancel the others` et `a package id that names nothing selects nothing and breaks nothing`.

**Décisions du 2026-10-17.** Q1 : A3 (`test_two_baselines_of_different_scopes_are_not_compared`) et B5 (`offers to compare only with baselines of the same scope`). Q2 : A3 (`test_a_scoped_restore_never_deletes_outside_its_subtree`, qui suppose `keep_new=False`). Q3 : A3 (service, API, intégration, course), B5 (`RestoreDialog.spec.ts`), C1 (M13–M17). Q4 : A3 (`test_a_baseline_whose_package_is_gone_is_diffed_but_not_restored`). Q5 : rien à faire, écart 8.

**Proportion.** Signatures, noms de tests et assertions ; aucun corps de fonction hormis la règle de `restricted_to`, que la signature ne fixe pas.
