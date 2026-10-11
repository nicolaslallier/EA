# Analyse du modèle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à EA trois fonctions de Sparx EA, toutes calculées à la demande sur `elements` et `relationships` : une matrice de relations éditable (lecture + création/suppression d'un lien par cellule), des contrôles de qualité du modèle (sept règles à code stable) et des agrégations avec carte de chaleur et tableau de bord.

**Architecture:** Trois modules de domaine purs (`domain/matrix.py`, `domain/quality.py`, `domain/analytics.py`), un seul service `services/analysis.py` (`AnalysisService`, gardes `require_caller` en première instruction), un port `AnalysisRepository` satisfait par `PostgresArchitectureRepository` (comme `IpamRepository`), un routeur `api/analysis.py`, trois outils MCP en lecture seule, trois sections du SPA (groupe « Analyse »). Aucune table, aucune migration : l'écriture d'un lien passe exclusivement par `POST /relationships` et `DELETE /relationships/{id}`.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2 async (Core, `GROUP BY` sur `properties ->> :clé`), `difflib` (stdlib) pour les doublons, MCP SDK 2.x, Vue 3 `<script setup>` + TypeScript, client OpenAPI généré, SVG écrit à la main, Vitest + Testing Library.

**Spec:** docs/superpowers/specs/2026-10-10-04-analyse-modele-design.md

## Global Constraints

- Dépendances : `api → services → domain ← repositories` ; `mcp/` à côté de `api/` ; aucun import FastAPI/SQLAlchemy dans `domain/`.
- Bornes déclarées une seule fois, dans le domaine, et réimportées par `api/schemas.py` puis `mcp/server.py` : `MAX_MATRIX_AXIS = 50`, `MAX_FINDINGS = 1000`, `MAX_BUCKETS = 200`. Tout argument borné entre dans `TestTheSameBoundsAsTheHttpAdapter`.
- `MAX_QUALITY_ELEMENTS = 5000` : au-delà, `ScopeTooLargeError` (422 `scope_too_large`).
- Codes de règles stables : `orphan`, `duplicate_probable`, `naming`, `missing_property`, `no_description`, `inconsistent_relationship` (lot 2) et `unreadable_property` (lot 3). Gravités : `error | warning | info`.
- Paramètres de qualité : `EA_QUALITY_SIMILARITY` (défaut 0.9, 0 à 1) et `EA_QUALITY_REQUIRED` (JSON `{"application_component": ["owner"]}`), validés au démarrage.
- `permitted_relationships` / `permits` de `domain/archimate/rules.py` sont la seule source des types permis : aucun type permis n'est calculé ou listé dans le SPA.
- Aucun nouveau chemin d'écriture : création `POST /relationships`, suppression `DELETE /relationships/{id}` ; le SPA masque les contrôles d'édition au lecteur, l'API décide.
- Chaque méthode publique d'`AnalysisService` commence par `require_caller()` (aucune n'écrit) ; `tests/unit/test_service_guards.py` la couvre via `WRITES[AnalysisService] = set()`.
- Pas de SQL dynamique : `by`/`metric` sont des enums, la clé de propriété suit `PROPERTY_KEY_PATTERN` (`^[A-Za-z][A-Za-z0-9_]{0,62}$`, `domain/model.py`) puis est liée en paramètre ; `statement_timeout` local à la transaction (`ANALYSIS_TIMEOUT_MS = 5000`) via `set_config(..., true)`.
- Lecture d'un nombre : seuls `.`/`,` décimaux et l'espace, l'espace insécable (U+00A0) et l'espace fine insécable (U+202F) sont tolérés ; tout le reste est « illisible ». `properties` reste `Mapping[str, str]`.
- La couleur est décidée par le serveur sous forme de `level` ordinal `0..4` ; le SPA n'associe que `level → couleur` (`LEVEL_COLOURS`, encre propre à chaque fond) et imprime la valeur dans la case.
- Pas de bibliothèque de graphiques ni de bibliothèque de grille : SVG/HTML écrits à la main, géométrie dans `lib/heatmapLayout.ts` (pur).
- Un message de constat contient des noms d'éléments (données utilisateur) : rendu en texte par Vue, jamais `v-html`. Un log `action=quality_run` avec `duration_ms` et le nombre de constats.
- État d'écran dans l'URL : matrice `?rows=`, `?cols=`, `?cell=` (changer un axe pousse, ouvrir une cellule remplace) ; qualité `?severity=`, `?code=` ; tableau de bord `?by=`, `?cols=`, `?metric=`, `?property=`.
- Toute charge déclenchée par l'URL ou la saisie passe par `useLatestRequest()` (`lib/latest.ts`) ; ESLint `no-floating-promises` actif.
- Tests : unit (sans I/O) → integration (PostgreSQL jetable, `make test-integration`) → API (`httpx.AsyncClient`) → MCP → Vitest. Couverture backend ≥ 90 %. Chaque test a un appelant (`_an_editor_is_calling` est autouse).
- Client OpenAPI régénéré par `make openapi` à chaque changement de schéma ; `make check` vert à la fin de chaque lot.
- Avant chaque commit : `make lint` (formate et corrige ruff) puis `make lint-check` ; côté frontend `npm run lint`. Les extraits de ce plan ne sont pas pré-formatés à la largeur de ligne du projet.
- Commits : Conventional Commits en anglais, terminés par `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Une ADR en français par lot (`docs/adr/TEMPLATE.md`), numéro = prochain libre au moment de l'exécution.
- Dépendance au sous-projet 1 (paquets, requêtes enregistrées) : isolée dans la Task 30, derrière une tâche de garde (Task 1). Les lots 1 à 3 livrent d'abord des axes « liste d'éléments ».

## Review Focus

1. **Matrice vide** : axes sans élément (catalogue vide, filtre qui ne trouve rien) → grille vide sans erreur ni cellule. Test dans la Task 2 (`build_matrix`), la Task 4 (service) et la Task 9 (rendu).
2. **Axe > 50** : des `element_ids` explicites au-delà de 50 sont refusés (422 `matrix_axis_too_large`), un axe défini par critères est coupé aux 50 premiers avec `truncated=true` et le total. Tests Task 4, Task 5 (HTTP) et Task 6 (MCP).
3. **Valeur de propriété illisible** : `cost = "1 200 €"` n'est jamais ignoré en silence : compté dans `unreadable`, et signalé par un constat `info`. Tests Task 21 (domaine), Task 22 (SQL = Python sur valeurs sales) et Task 26 (règle).
4. **Élément dans les deux axes** : la diagonale `(a, a)` n'a pas de cas spécial, `permits(..., same_element=True)` décide (pas de composition de soi sur soi). Test dans la Task 2 et la Task 4 (`/matrix/cell`).
5. **Deux sens de relation** : `A→B` ne vit que dans la cellule (ligne A, colonne B) ; `B→A` dans (ligne B, colonne A). Aucune symétrisation. Tests Task 2 (domaine) et Task 9 (grille).

---

## Écarts constatés avec la spec

La spec a été écrite sans lire le code ; le plan suit le code réel.

1. **Le sous-projet 1 n'existe pas** (aucun `packages`, `saved_queries`, `ElementQuery`, `elements.package_id` ; `ElementFilter` ne filtre que type, couche et nom). Les axes sont donc des *sélections* : `element_ids` explicites, ou critères `element_types` / `layers` / `search` (ou rien = tout le catalogue). `package_id`/`query_id` sont repoussés dans la Task 30, gardée par la Task 1. Même chose pour `package_id`/`query_id` de `/quality`, et pour la dimension `package` de l'analyse.
2. **Où vivent les bornes.** La spec les place dans `api/schemas.py`, mais `services/` ne peut pas importer `api/`. Elles sont déclarées dans le domaine (`domain/matrix.py`, `domain/quality.py`, `domain/analytics.py`) et réimportées par `api/schemas.py` — le motif existant (`MAX_FILE_TAGS`, `MAX_TRAVERSAL_DEPTH`). Elles restent déclarées une fois.
3. **Aucune cellule n'est « interdite » dans ArchiMate 3.2 tel qu'encodé** : `association` est permise entre tous les types, diagonale comprise. « Cellules ayant un lien ou un type permis » désignerait donc toute la grille. `MatrixRead.cells` ne contient que les cellules portant au moins un lien ; `blocked` liste les paires sans lien dont `allowed` est vide (aujourd'hui toujours vide, mais le domaine le traite si le métamodèle évolue) ; les types permis d'une cellule viennent de `GET /matrix/cell` à l'ouverture du panneau.
4. **`permitted_relationships(source_type, target_type)` ignore `same_element`.** Le domaine ajoute `allowed_between(source, target)` (filtre ce résultat avec `permits(..., same_element=...)`) : c'est lui, et non une règle recopiée, qui exclut `composition` sur la diagonale.
5. **422 vs `truncated`** (la spec dit les deux) : `element_ids` explicites > 50 → 422 `matrix_axis_too_large` ; axe par critères > 50 → `truncated=true` (50 premiers par nom, `rows_total`/`cols_total`).
6. **Orphelins en SQL (`NOT EXISTS`) supprimés.** Avec le plafond de 5 000 éléments, la règle pure sur `links_touching` donne le même résultat sans second chemin ; le test d'intégration compare donc la règle pure aux liens lus en base. À rouvrir si le plafond monte.
7. **`fix_hint`** : la spec cite `/relations?element=<id>`, mais `RelationshipsSection` ne lit pas l'URL. Le constat `inconsistent_relationship` pointe vers `/analyse/voisinage?element=<id>` (qui lit `?element=`, ADR 0010), les autres vers `/elements?element=<id>` (ADR 0011).
8. **`Finding` porte `element_name` et `related_name`** (absents de la spec) : le tableau du SPA et le tri « gravité puis nom » en ont besoin, et un lien ne porte jamais les noms de ses extrémités.
9. **Agrégation** : le SQL ne renvoie que des *groupes bruts* (`GROUP BY` sur la valeur brute de la clé), la classification (insensible à la casse, accents, énumération ordonnée) et les niveaux sont faits une seule fois, en Python — `PostgreSQL lower()` dépend de la locale de la base. L'implémentation pure (`group_elements`) sert de référence au test d'intégration et de double au service.
10. **Dimensions** : `by`/`rows`/`cols` sont des chaînes validées par `DIMENSION_PATTERN` (`element_type | layer | property:<clé>`) et non des enums, puisque `property:<clé>` est ouverte. La dimension `package` attend le sous-projet 1.
11. **Pas d'abréviation de type** dans les pastilles de la matrice : flèche + libellé de `RELATIONSHIP_LABELS`, qui suffit à lire le sens.
12. **Cases du tableau de bord → catalogue filtré** : `ElementCatalogue` ne lit pas ses filtres dans l'URL. La Task 28 lui ajoute la lecture de `?layer=` et `?type=` à l'ouverture.
13. **Timeout** : `statement_timeout` est posé localement mais son dépassement n'est pas traduit en erreur de domaine (aucun motif existant de traduction par message) ; il sort en `internal_error` 500 avec trace dans les logs. Noté dans l'ADR du lot 3.
14. **Sections** : le groupe `analysis` existe déjà dans `GROUPS` ; les trois entrées s'y ajoutent sans créer de groupe.

## Carte des fichiers

**Backend — créer**
- `backend/src/ea/domain/matrix.py` — `MAX_MATRIX_AXIS`, `AxisSelection`, `MatrixAxis`, `MatrixCell`, `Matrix`, `allowed_between`, `build_matrix`
- `backend/src/ea/domain/quality.py` — `Severity`, `RuleCode`, `Finding`, `QualityParams`, règles, `run_checks`, `QualityReport`, `page_of`
- `backend/src/ea/domain/analytics.py` — lectures de propriétés, `Dimension`, `Metric`, `RawGroup`, `group_elements`, `summarise`, `scale_for`
- `backend/src/ea/services/analysis.py` — `AnalysisService`
- `backend/src/ea/api/analysis.py` — routes `/matrix`, `/matrix/cell`, `/quality`, `/analytics/*`
- `backend/tests/support_analysis.py` — `an_element`, `a_link`, `a_stored_link`
- `backend/tests/unit/test_matrix.py`, `test_quality.py`, `test_analytics.py`, `test_analysis_service.py`, `test_quality_params.py`
- `backend/tests/e2e/test_analysis_api.py`
- `backend/tests/integration/test_analysis_store.py`

**Backend — modifier**
- `backend/src/ea/domain/errors.py` — `MatrixAxisTooLargeError`, `ScopeTooLargeError`, `InvalidAnalysisError`
- `backend/src/ea/domain/model.py` — `PROPERTY_KEY_PATTERN` public
- `backend/src/ea/domain/ports.py` — `AnalysisRepository`
- `backend/src/ea/repositories/architecture_store.py` — implémentation SQL du port
- `backend/src/ea/api/schemas.py`, `api/dependencies.py`, `api/errors.py`
- `backend/src/ea/core/config.py` — `quality_similarity`, `quality_required`
- `backend/src/ea/main.py` — câblage du service, du routeur, de l'outil MCP
- `backend/src/ea/mcp/server.py` — `get_relationship_matrix`, `check_model`, `aggregate_model`, `INSTRUCTIONS`
- `backend/tests/conftest.py` — doubles du port + fixture `analysis`
- `backend/tests/unit/test_service_guards.py`, `test_mcp_server.py`, `test_config.py`
- `backend/openapi.json`, `frontend/src/api/schema.d.ts` — régénérés
- `backend/.env.example`

**Frontend — créer**
- `frontend/src/features/matrix/{axis.ts,gridNavigation.ts,useMatrix.ts,MatrixGrid.vue,MatrixCellPanel.vue,MatrixSection.vue}`
- `frontend/src/features/quality/{labels.ts,useQuality.ts,QualitySection.vue}`
- `frontend/src/features/dashboard/{levels.ts,useDashboard.ts,HeatmapChart.vue,DashboardSection.vue}`
- `frontend/src/lib/heatmapLayout.ts`
- `frontend/tests/{matrixAxis,gridNavigation,useMatrix,MatrixGrid,MatrixSection,useQuality,QualitySection,heatmapLayout,useDashboard,DashboardSection}.spec.ts`

**Frontend — modifier**
- `frontend/src/router/sections.ts` (trois entrées), `frontend/src/features/elements/ElementCatalogue.vue` (filtres d'URL), `frontend/tests/support/api.ts` (fabriques), `frontend/tests/ElementCatalogue.spec.ts`

**Docs** — trois ADR (`docs/adr/<N>-…md`), `CLAUDE.md`.

---

# Lot 0 — Garde du sous-projet 1

### Task 1: Garde — le sous-projet 1 est-il livré ?

**Files:**
- Modify: aucun (lecture seule ; le résultat est noté dans le message de la PR)

**Interfaces:**
- Consumes: —
- Produces: une décision écrite, « SP1 présent » ou « SP1 absent », qui conditionne la seule Task 30.

- [ ] **Step 1: Chercher les marqueurs du sous-projet 1**

```bash
cd backend
grep -rn "class ElementQuery\|package_id\|saved_queries" src/ea/domain src/ea/db/models | head
ls migrations/versions | tail -3
```

Attendu à la rédaction de ce plan : aucune sortie pour le `grep`, dernière révision `0008_file_metadata.py`.

- [ ] **Step 2: Noter la décision**

Si le `grep` ne trouve rien : le sous-projet 1 est absent. Exécuter les Tasks 2 à 29 et 31, sauter la Task 30. Si `ElementQuery` et `elements.package_id` existent : exécuter aussi la Task 30 après la Task 29.

- [ ] **Step 3: Aucun commit** (aucun fichier modifié).

---

# Lot 1 — La matrice de relations

### Task 2: `domain/matrix.py` — la grille, pure

**Files:**
- Create: `backend/src/ea/domain/matrix.py`
- Create: `backend/tests/support_analysis.py`
- Create: `backend/tests/unit/test_matrix.py`
- Modify: `backend/src/ea/domain/errors.py` (ajouter à la fin du fichier)

**Interfaces:**
- Consumes: `ea.domain.archimate.{permits, permitted_relationships, RelationshipType, ElementType, Layer}`, `ea.domain.model.{Element, Relationship}`
- Produces:
```python
MAX_MATRIX_AXIS: Final = 50
Allowed = Callable[[Element, Element], tuple[RelationshipType, ...]]
def allowed_between(source: Element, target: Element) -> tuple[RelationshipType, ...]: ...
@dataclass(frozen=True, slots=True) class AxisSelection: element_ids: tuple[UUID, ...] = (); element_types: tuple[ElementType, ...] = (); layers: tuple[Layer, ...] = (); search: str | None = None
@dataclass(frozen=True, slots=True) class MatrixAxis: elements: tuple[Element, ...]; total: int  # .of(elements, total=None), .truncated
@dataclass(frozen=True, slots=True) class MatrixCell: source_id: UUID; target_id: UUID; links: tuple[Relationship, ...]; allowed: tuple[RelationshipType, ...]
@dataclass(frozen=True, slots=True) class Matrix: rows: MatrixAxis; cols: MatrixAxis; cells: tuple[MatrixCell, ...]; blocked: tuple[tuple[UUID, UUID], ...]
def build_matrix(rows: MatrixAxis, cols: MatrixAxis, relationships: Iterable[Relationship], allowed: Allowed = allowed_between) -> Matrix: ...
class MatrixAxisTooLargeError(DomainError)
```
```ts
// aucun
```

- [ ] **Step 1: Écrire le support de tests partagé**

`backend/tests/support_analysis.py` :

```python
"""Small builders shared by the analysis suites — defaults plus overrides."""

from __future__ import annotations

from uuid import uuid4

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.model import Element, Relationship
from tests.conftest import FIXED_NOW


def an_element(element_type: ElementType, name: str, **overrides: object) -> Element:
    return Element.create(
        element_type=element_type,
        name=name,
        now=FIXED_NOW,
        **overrides,  # type: ignore[arg-type]
    )


def a_link(
    relationship_type: RelationshipType, source: Element, target: Element
) -> Relationship:
    """A legal link, built through the metamodel like the service does."""
    return Relationship.between(relationship_type, source, target, now=FIXED_NOW)


def a_stored_link(
    relationship_type: RelationshipType, source: Element, target: Element
) -> Relationship:
    """A link that skips the metamodel: what an import, or an older metamodel, left behind."""
    return Relationship(
        id=uuid4(),
        relationship_type=relationship_type,
        source_id=source.id,
        target_id=target.id,
        source_type=source.element_type,
        target_type=target.element_type,
        created_at=FIXED_NOW,
    )
```

- [ ] **Step 2: Écrire les tests qui échouent**

`backend/tests/unit/test_matrix.py` :

```python
"""The relationship matrix as a pure function of two axes and the links."""

from __future__ import annotations

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.matrix import MAX_MATRIX_AXIS, MatrixAxis, allowed_between, build_matrix
from tests.support_analysis import a_link, an_element


def axis(*elements):  # type: ignore[no-untyped-def]
    return MatrixAxis.of(elements)


def test_a_matrix_of_nothing_is_empty() -> None:
    matrix = build_matrix(axis(), axis(), [])

    assert (matrix.cells, matrix.blocked) == ((), ())
    assert not matrix.rows.truncated


def test_a_link_lands_in_the_cell_of_its_source_row_and_target_column() -> None:
    api = an_element(E.APPLICATION_SERVICE, "Invoice API")
    process = an_element(E.BUSINESS_PROCESS, "Order to cash")
    link = a_link(R.SERVING, api, process)

    matrix = build_matrix(axis(api), axis(process), [link])

    assert [(c.source_id, c.target_id, c.links) for c in matrix.cells] == [
        (api.id, process.id, (link,))
    ]
    assert R.SERVING in matrix.cells[0].allowed


def test_two_links_between_one_pair_share_a_cell() -> None:
    api = an_element(E.APPLICATION_SERVICE, "Invoice API")
    process = an_element(E.BUSINESS_PROCESS, "Order to cash")
    serving, association = a_link(R.SERVING, api, process), a_link(R.ASSOCIATION, api, process)

    matrix = build_matrix(axis(api), axis(process), [serving, association])

    assert len(matrix.cells) == 1
    assert set(matrix.cells[0].links) == {serving, association}


def test_the_two_directions_are_two_cells_and_never_mirrored() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")
    forward, backward = a_link(R.FLOW, a, b), a_link(R.FLOW, b, a)

    matrix = build_matrix(axis(a, b), axis(a, b), [forward])
    assert [(c.source_id, c.target_id) for c in matrix.cells] == [(a.id, b.id)]

    both = build_matrix(axis(a, b), axis(a, b), [forward, backward])
    assert {(c.source_id, c.target_id) for c in both.cells} == {(a.id, b.id), (b.id, a.id)}


def test_a_link_leaving_the_axes_is_not_drawn() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")
    outsider = an_element(E.APPLICATION_COMPONENT, "Outsider")

    matrix = build_matrix(axis(a), axis(b), [a_link(R.FLOW, a, outsider)])

    assert matrix.cells == ()


def test_the_diagonal_is_not_special_the_rules_decide() -> None:
    component = an_element(E.APPLICATION_COMPONENT, "A")

    allowed = allowed_between(component, component)

    assert R.COMPOSITION not in allowed
    assert R.SPECIALIZATION not in allowed
    assert R.ASSOCIATION in allowed


def test_an_element_on_both_axes_gets_a_row_and_a_column() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")

    matrix = build_matrix(axis(a, b), axis(a, b), [a_link(R.FLOW, a, b)])

    assert len(matrix.cells) == 1
    assert matrix.blocked == ()


def test_a_pair_with_nothing_permitted_and_no_link_is_blocked() -> None:
    a = an_element(E.APPLICATION_COMPONENT, "A")
    b = an_element(E.APPLICATION_COMPONENT, "B")

    matrix = build_matrix(axis(a), axis(b), [], allowed=lambda source, target: ())

    assert matrix.cells == ()
    assert matrix.blocked == ((a.id, b.id),)


def test_an_axis_over_the_ceiling_is_cut_and_says_how_many_there_were() -> None:
    many = [an_element(E.NODE, f"n{index:03}") for index in range(MAX_MATRIX_AXIS + 3)]

    cut = MatrixAxis.of(many[: MAX_MATRIX_AXIS + 1], total=MAX_MATRIX_AXIS + 3)

    assert len(cut.elements) == MAX_MATRIX_AXIS
    assert cut.total == MAX_MATRIX_AXIS + 3
    assert cut.truncated


def test_an_axis_total_is_never_below_what_it_keeps() -> None:
    nodes = [an_element(E.NODE, "a"), an_element(E.NODE, "b")]

    assert MatrixAxis.of(nodes, total=0).total == 2
```

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_matrix.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.matrix'`.

- [ ] **Step 4: Implémenter**

Ajouter à la fin de `backend/src/ea/domain/errors.py` :

```python
class MatrixAxisTooLargeError(DomainError):
    """An axis of the relationship matrix names more elements than the grid shows."""
```

Créer `backend/src/ea/domain/matrix.py` :

```python
"""The relationship matrix: elements by elements, one cell per ordered pair.

Pure by construction. The matrix states no rule of its own — what may link two
elements is asked of `domain/archimate/rules.py` — and it stores nothing: a
cell is a reading of the `relationships` table, and creating a link from a
cell goes through `POST /relationships` like any other (docs/adr/0009).

A link lives in exactly one cell, (row = source, column = target). Its reverse
is another cell. That is Sparx EA's reading too, and it keeps a cell's meaning
one sentence long: "what runs from this row to this column".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from ea.domain.archimate import (
    ElementType,
    Layer,
    RelationshipType,
    permitted_relationships,
    permits,
)
from ea.domain.model import Element, Relationship

#: How many elements one axis shows. Declared here and imported by
#: `api/schemas.py` — a service cannot import the API (CLAUDE.md).
MAX_MATRIX_AXIS: Final = 50

Allowed = Callable[[Element, Element], tuple[RelationshipType, ...]]


def allowed_between(source: Element, target: Element) -> tuple[RelationshipType, ...]:
    """The relationships ArchiMate lets run from `source` to `target`.

    `permitted_relationships` answers for two *types*; whether the two ends are
    one element is what makes `A composes A` illegal while `component composes
    component` stays legal, so that is passed to `permits` here.
    """
    same = source.id == target.id
    return tuple(
        relationship
        for relationship in permitted_relationships(source.element_type, target.element_type)
        if permits(
            relationship, source.element_type, target.element_type, same_element=same
        )
    )


@dataclass(frozen=True, slots=True)
class AxisSelection:
    """What one axis is made of: named elements, or the catalogue narrowed by criteria.

    Nothing set means the whole catalogue (cut at `MAX_MATRIX_AXIS`).
    """

    element_ids: tuple[UUID, ...] = ()
    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None


@dataclass(frozen=True, slots=True)
class MatrixAxis:
    elements: tuple[Element, ...]
    total: int

    @classmethod
    def of(cls, elements: Sequence[Element], total: int | None = None) -> MatrixAxis:
        kept = tuple(elements[:MAX_MATRIX_AXIS])
        claimed = len(elements) if total is None else total
        return cls(elements=kept, total=max(claimed, len(kept)))

    @property
    def truncated(self) -> bool:
        return self.total > len(self.elements)


@dataclass(frozen=True, slots=True)
class MatrixCell:
    source_id: UUID
    target_id: UUID
    links: tuple[Relationship, ...]
    allowed: tuple[RelationshipType, ...]


@dataclass(frozen=True, slots=True)
class Matrix:
    rows: MatrixAxis
    cols: MatrixAxis
    #: Only the cells holding a link: the grid is sparse by nature.
    cells: tuple[MatrixCell, ...]
    #: Pairs with no link where the metamodel permits nothing.
    blocked: tuple[tuple[UUID, UUID], ...]


def build_matrix(
    rows: MatrixAxis,
    cols: MatrixAxis,
    relationships: Iterable[Relationship],
    allowed: Allowed = allowed_between,
) -> Matrix:
    """Place each relationship in its cell and mark the pairs nothing can link."""
    by_pair: dict[tuple[UUID, UUID], list[Relationship]] = {}
    for link in relationships:
        by_pair.setdefault((link.source_id, link.target_id), []).append(link)

    cells: list[MatrixCell] = []
    blocked: list[tuple[UUID, UUID]] = []
    for row in rows.elements:
        for col in cols.elements:
            links = tuple(
                sorted(by_pair.get((row.id, col.id), ()), key=lambda l: (l.created_at, l.id))
            )
            permitted = allowed(row, col)
            if links:
                cells.append(MatrixCell(row.id, col.id, links, permitted))
            elif not permitted:
                blocked.append((row.id, col.id))
    return Matrix(rows=rows, cols=cols, cells=tuple(cells), blocked=tuple(blocked))
```

- [ ] **Step 5: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_matrix.py -x`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/matrix.py backend/src/ea/domain/errors.py backend/tests/support_analysis.py backend/tests/unit/test_matrix.py
git commit -m "feat(analysis): add the pure relationship matrix" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Le port `AnalysisRepository` (matrice) et ses deux implémentations

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (ajouter après `IpamRepository`, vers la ligne 267)
- Modify: `backend/src/ea/repositories/architecture_store.py` (ajouter trois méthodes à `PostgresArchitectureRepository`, après `view_of`, vers la ligne 494)
- Modify: `backend/tests/conftest.py` (`InMemoryRepository`, vers la ligne 380, avant le bloc « IPAM »)
- Create: `backend/tests/integration/test_analysis_store.py`

**Interfaces:**
- Consumes: `Element`, `Relationship`, `ElementRecord`, `RelationshipRecord`, `element_from_row`, `relationship_from_row`
- Produces:
```python
class AnalysisRepository(Protocol):
    async def elements_by_ids(self, element_ids: Sequence[UUID]) -> tuple[Element, ...]: ...   # ordered by name, id; absent ids simply missing
    async def links_between(self, source_ids: Sequence[UUID], target_ids: Sequence[UUID]) -> tuple[Relationship, ...]: ...
    async def links_touching(self, element_ids: Sequence[UUID]) -> tuple[Relationship, ...]: ...  # either end in the set
```

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

`backend/tests/integration/test_analysis_store.py` :

```python
"""The three reads behind the matrix and the quality checks, on a real PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def a_small_graph(service: ArchitectureService):  # type: ignore[no-untyped-def]
    api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order to cash")
    loner = await service.create_element(element_type=E.NODE, name="Loner")
    link = await service.connect(
        relationship_type=R.SERVING, source_id=api.id, target_id=process.id
    )
    return api, process, loner, link


async def test_elements_come_back_by_name_and_absent_ids_are_missing(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, _, _ = await a_small_graph(graph_service)

    found = await graph_repository.elements_by_ids([process.id, api.id, uuid4()])

    assert [element.name for element in found] == ["Invoice API", "Order to cash"]
    assert await graph_repository.elements_by_ids([]) == ()


async def test_links_between_keeps_the_direction(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, _, link = await a_small_graph(graph_service)

    forward = await graph_repository.links_between([api.id], [process.id])
    backward = await graph_repository.links_between([process.id], [api.id])

    assert [found.id for found in forward] == [link.id]
    assert backward == ()
    assert await graph_repository.links_between([], [process.id]) == ()


async def test_links_touching_finds_a_link_by_either_end(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, loner, link = await a_small_graph(graph_service)

    assert [l.id for l in await graph_repository.links_touching([api.id])] == [link.id]
    assert [l.id for l in await graph_repository.links_touching([process.id])] == [link.id]
    assert await graph_repository.links_touching([loner.id]) == ()
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/integration/test_analysis_store.py -x`
Expected: SKIPPED sans conteneur jetable (aucun échec visible) ; pour voir l'échec : `make test-integration` → `AttributeError: 'PostgresArchitectureRepository' object has no attribute 'elements_by_ids'`.

- [ ] **Step 3: Déclarer le port**

Dans `backend/src/ea/domain/ports.py`, après la classe `IpamRepository` :

```python
class AnalysisRepository(Protocol):
    """The reads the matrix, the quality checks and the dashboards need.

    A fifth port, declared apart from `ArchitectureRepository` for the reason
    `IpamRepository` is: it is a different use case, satisfied by the same
    PostgreSQL class. Nothing here writes — an analysis computes, it does not
    store (docs/adr/<ADR lot 1>).
    """

    async def elements_by_ids(self, element_ids: Sequence[UUID]) -> tuple[Element, ...]:
        """The elements among these ids that exist, by name. Absent ids are missing, not errors."""
        ...

    async def links_between(
        self, source_ids: Sequence[UUID], target_ids: Sequence[UUID]
    ) -> tuple[Relationship, ...]:
        """Every link running from one of `source_ids` to one of `target_ids`."""
        ...

    async def links_touching(self, element_ids: Sequence[UUID]) -> tuple[Relationship, ...]:
        """Every link with at least one end among these ids."""
        ...
```

- [ ] **Step 4: Implémenter côté PostgreSQL**

Dans `PostgresArchitectureRepository`, après `view_of` :

```python
    # --- Analysis: the reads behind the matrix and the checks ---------------

    async def elements_by_ids(self, element_ids: Sequence[UUID]) -> tuple[Element, ...]:
        if not element_ids:
            return ()
        async with self._sessions() as session:
            found = await session.scalars(
                select(ElementRecord)
                .where(ElementRecord.id.in_(list(element_ids)))
                .order_by(ElementRecord.name, ElementRecord.id)
            )
            return tuple(element_from_row(row) for row in found)

    async def links_between(
        self, source_ids: Sequence[UUID], target_ids: Sequence[UUID]
    ) -> tuple[Relationship, ...]:
        if not source_ids or not target_ids:
            return ()
        statement = (
            select(RelationshipRecord)
            .where(
                RelationshipRecord.source_id.in_(list(source_ids)),
                RelationshipRecord.target_id.in_(list(target_ids)),
            )
            .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
        )
        async with self._sessions() as session:
            return tuple(
                relationship_from_row(row) for row in await session.scalars(statement)
            )

    async def links_touching(self, element_ids: Sequence[UUID]) -> tuple[Relationship, ...]:
        if not element_ids:
            return ()
        ids = list(element_ids)
        statement = (
            select(RelationshipRecord)
            .where(or_(RelationshipRecord.source_id.in_(ids), RelationshipRecord.target_id.in_(ids)))
            .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
        )
        async with self._sessions() as session:
            return tuple(
                relationship_from_row(row) for row in await session.scalars(statement)
            )
```

Mettre à jour la docstring du module (première ligne) en « … `ArchitectureRepository`, `IpamRepository` and `AnalysisRepository` … ».

- [ ] **Step 5: Implémenter le double en mémoire**

Dans `backend/tests/conftest.py`, dans `InMemoryRepository`, juste avant le commentaire `# --- IPAM` :

```python
    # --- Analysis: the reads behind the matrix and the checks ---

    async def elements_by_ids(self, element_ids: Sequence[UUID]) -> tuple[Element, ...]:
        found = [self.elements[i] for i in dict.fromkeys(element_ids) if i in self.elements]
        return tuple(sorted(found, key=lambda element: (element.name, str(element.id))))

    async def links_between(
        self, source_ids: Sequence[UUID], target_ids: Sequence[UUID]
    ) -> tuple[Relationship, ...]:
        sources, targets = set(source_ids), set(target_ids)
        return tuple(
            link
            for link in self.relationships.values()
            if link.source_id in sources and link.target_id in targets
        )

    async def links_touching(self, element_ids: Sequence[UUID]) -> tuple[Relationship, ...]:
        ids = set(element_ids)
        return tuple(
            link
            for link in self.relationships.values()
            if link.source_id in ids or link.target_id in ids
        )
```

- [ ] **Step 6: Relancer (intégration), PASS**

Run: `make test-integration` (ou `cd backend && uv run pytest tests/integration/test_analysis_store.py -x` avec le conteneur jetable démarré par `make pg-up`)
Expected: 3 passed.

- [ ] **Step 7: Typage**

Run: `cd backend && uv run mypy src migrations`
Expected: Success.

- [ ] **Step 8: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/architecture_store.py backend/tests/conftest.py backend/tests/integration/test_analysis_store.py
git commit -m "feat(analysis): add the AnalysisRepository port and its PostgreSQL reads" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `AnalysisService` — `matrix` et `cell`

**Files:**
- Create: `backend/src/ea/services/analysis.py`
- Create: `backend/tests/unit/test_analysis_service.py`
- Modify: `backend/tests/conftest.py` (fixture `analysis`, après la fixture `ipam`, vers la ligne 807 ; import `AnalysisService`)
- Modify: `backend/tests/unit/test_service_guards.py` (import + `WRITES`)

**Interfaces:**
- Consumes: `ArchitectureService.{list_elements, count_elements, get_element}`, `AnalysisRepository`, `domain.matrix.*`
- Produces:
```python
class AnalysisService:
    def __init__(self, architecture: ArchitectureService, repository: AnalysisRepository) -> None: ...
    async def matrix(self, rows: AxisSelection, cols: AxisSelection) -> Matrix: ...
    async def cell(self, source_id: UUID, target_id: UUID) -> MatrixCell: ...
```
(`__init__` reçoit `params` à la Task 15 ; les lots 2 et 3 ajoutent `check`, `aggregate`, `heatmap`.)

- [ ] **Step 1: Écrire les tests qui échouent**

`backend/tests/unit/test_analysis_service.py` :

```python
"""What the analysis service does with a graph: the matrix first, the rest in later lots."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    ElementNotFoundError,
    MatrixAxisTooLargeError,
    NotAuthenticatedError,
)
from ea.domain.matrix import MAX_MATRIX_AXIS, AxisSelection
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import a_reader


async def two_linked(service: ArchitectureService):  # type: ignore[no-untyped-def]
    api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order to cash")
    link = await service.connect(
        relationship_type=R.SERVING, source_id=api.id, target_id=process.id
    )
    return api, process, link


@pytest.mark.asyncio
class TestTheMatrix:
    async def test_named_elements_make_a_grid_with_their_link(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        api, process, link = await two_linked(service)

        matrix = await analysis.matrix(
            AxisSelection(element_ids=(api.id,)), AxisSelection(element_ids=(process.id,))
        )

        assert [c.links for c in matrix.cells] == [(link,)]

    async def test_an_empty_catalogue_gives_an_empty_grid(
        self, analysis: AnalysisService
    ) -> None:
        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert (matrix.rows.elements, matrix.cols.elements, matrix.cells) == ((), (), ())
        assert not matrix.rows.truncated

    async def test_no_criteria_means_the_whole_catalogue(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.rows.elements) == len(matrix.cols.elements) == 2
        assert len(matrix.cells) == 1

    async def test_an_unknown_id_is_named_not_ignored(self, analysis: AnalysisService) -> None:
        missing = uuid4()

        with pytest.raises(ElementNotFoundError, match=str(missing)):
            await analysis.matrix(AxisSelection(element_ids=(missing,)), AxisSelection())

    async def test_named_elements_over_the_ceiling_are_refused(
        self, analysis: AnalysisService
    ) -> None:
        too_many = tuple(uuid4() for _ in range(MAX_MATRIX_AXIS + 1))

        with pytest.raises(MatrixAxisTooLargeError):
            await analysis.matrix(AxisSelection(element_ids=too_many), AxisSelection())

    async def test_a_catalogue_axis_over_the_ceiling_is_cut_and_counted(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        for index in range(MAX_MATRIX_AXIS + 3):
            await service.create_element(element_type=E.NODE, name=f"node-{index:03}")

        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.rows.elements) == MAX_MATRIX_AXIS
        assert matrix.rows.total == MAX_MATRIX_AXIS + 3
        assert matrix.rows.truncated

    async def test_a_reader_may_read_the_grid(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        with acting_as(a_reader()):
            matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.cells) == 1

    async def test_nobody_reads_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.matrix(AxisSelection(), AxisSelection())


@pytest.mark.asyncio
class TestOneCell:
    async def test_it_lists_the_links_and_what_may_still_be_added(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        api, process, link = await two_linked(service)

        cell = await analysis.cell(api.id, process.id)

        assert cell.links == (link,)
        assert R.SERVING in cell.allowed

    async def test_a_pair_without_links_still_says_what_is_permitted(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        data = await service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
        process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")

        cell = await analysis.cell(data.id, process.id)

        assert cell.links == ()
        assert R.SERVING not in cell.allowed
        assert R.ASSOCIATION in cell.allowed

    async def test_the_diagonal_never_offers_a_containment_of_itself(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        component = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="A")

        cell = await analysis.cell(component.id, component.id)

        assert R.COMPOSITION not in cell.allowed
        assert R.ASSOCIATION in cell.allowed

    async def test_an_unknown_end_is_a_not_found(self, analysis: AnalysisService) -> None:
        with pytest.raises(ElementNotFoundError):
            await analysis.cell(uuid4(), uuid4())
```

- [ ] **Step 2: Fixture et garde**

Dans `backend/tests/conftest.py`, ajouter l'import `from ea.services.analysis import AnalysisService` (ordre alphabétique avec les autres `ea.services.*`) et, après la fixture `ipam` :

```python
@pytest.fixture
def analysis(repository: InMemoryRepository, service: ArchitectureService) -> AnalysisService:
    """The analysis use cases over the same graph double, which answers `AnalysisRepository` too."""
    return AnalysisService(service, repository)
```

Dans `backend/tests/unit/test_service_guards.py` : `from ea.services.analysis import AnalysisService` et, dans `WRITES`, `AnalysisService: set(),`.

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.services.analysis'` (à l'import de `conftest`).

- [ ] **Step 4: Implémenter**

`backend/src/ea/services/analysis.py` :

```python
"""Analysing the model: the matrix, the quality checks, the dashboards.

Nothing here writes and nothing here is stored — an analysis is computed on
demand from `elements` and `relationships`, because a stored result would be a
second truth that every edit made stale (docs/adr/<ADR lot 1>). Writing a link
from a matrix cell is `ArchitectureService.connect`, called by the caller.

Every public method asks who is calling first (`require_caller`): all of them
are reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import ElementNotFoundError, MatrixAxisTooLargeError
from ea.domain.matrix import (
    MAX_MATRIX_AXIS,
    AxisSelection,
    Matrix,
    MatrixAxis,
    MatrixCell,
    allowed_between,
    build_matrix,
)
from ea.domain.ports import ElementFilter
from ea.services.caller import require_caller

if TYPE_CHECKING:
    from ea.domain.ports import AnalysisRepository
    from ea.services.architecture import ArchitectureService


class AnalysisService:
    """The analysis use cases, over the architecture service and three extra reads."""

    def __init__(
        self,
        architecture: ArchitectureService,
        repository: AnalysisRepository,
    ) -> None:
        self._architecture = architecture
        self._repository = repository

    # --- The relationship matrix -------------------------------------------

    async def matrix(self, rows: AxisSelection, cols: AxisSelection) -> Matrix:
        """The grid of two axes with the links between them."""
        require_caller()
        row_axis = await self._axis(rows)
        col_axis = await self._axis(cols)
        links = await self._repository.links_between(
            [element.id for element in row_axis.elements],
            [element.id for element in col_axis.elements],
        )
        return build_matrix(row_axis, col_axis, links)

    async def cell(self, source_id: UUID, target_id: UUID) -> MatrixCell:
        """One ordered pair: its links, and what ArchiMate would still let us add."""
        require_caller()
        source = await self._architecture.get_element(source_id)
        target = await self._architecture.get_element(target_id)
        links = await self._repository.links_between([source_id], [target_id])
        return MatrixCell(
            source_id=source_id,
            target_id=target_id,
            links=tuple(sorted(links, key=lambda link: (link.created_at, link.id))),
            allowed=allowed_between(source, target),
        )

    async def _axis(self, selection: AxisSelection) -> MatrixAxis:
        if selection.element_ids:
            ids = tuple(dict.fromkeys(selection.element_ids))
            if len(ids) > MAX_MATRIX_AXIS:
                msg = (
                    f"an axis names {len(ids)} elements; "
                    f"the grid shows at most {MAX_MATRIX_AXIS}"
                )
                raise MatrixAxisTooLargeError(msg)
            found = await self._repository.elements_by_ids(ids)
            missing = sorted(str(i) for i in set(ids) - {element.id for element in found})
            if missing:
                msg = f"no element with id {missing[0]}"
                raise ElementNotFoundError(msg)
            return MatrixAxis.of(found)
        criteria = ElementFilter(
            element_types=selection.element_types,
            layers=selection.layers,
            search=selection.search,
            limit=MAX_MATRIX_AXIS + 1,
        )
        elements = await self._architecture.list_elements(criteria)
        total = (
            len(elements)
            if len(elements) <= MAX_MATRIX_AXIS
            else await self._architecture.count_elements(criteria)
        )
        return MatrixAxis.of(elements, total)
```

- [ ] **Step 5: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py tests/unit/test_service_guards.py -x`
Expected: tous passent.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/services/analysis.py backend/tests/unit/test_analysis_service.py backend/tests/unit/test_service_guards.py backend/tests/conftest.py
git commit -m "feat(analysis): add AnalysisService with the matrix and one cell" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: API — `POST /matrix` et `GET /matrix/cell`, câblage, client régénéré

**Files:**
- Create: `backend/src/ea/api/analysis.py`
- Create: `backend/tests/e2e/test_analysis_api.py`
- Modify: `backend/src/ea/api/schemas.py` (imports en tête ; modèles à la fin du fichier)
- Modify: `backend/src/ea/api/dependencies.py` (ajouter après `Ipam`)
- Modify: `backend/src/ea/api/errors.py` (import + `_STATUS`)
- Modify: `backend/src/ea/main.py` (imports ; lifespan ligne ~245 ; `create_app` ligne ~437-510 ; `include_router` ligne ~535 ; `_mount_mcp` à la Task 6)
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (par `make openapi`)

**Interfaces:**
- Consumes: `AnalysisService.{matrix, cell}`
- Produces (HTTP) :
```
POST /matrix  body {rows: AxisSelect, cols: AxisSelect} -> MatrixRead   (200 ; 404 not_found ; 422 matrix_axis_too_large)
GET  /matrix/cell?source=<uuid>&target=<uuid> -> MatrixCellDetailRead   (404)
```
```python
class AxisSelect(_Input): element_ids: list[UUID]; element_types: list[ElementType]; layers: list[Layer]; search: Search | None;  def selection(self) -> AxisSelection
class MatrixQuery(_Input): rows: AxisSelect; cols: AxisSelect
class ElementBrief(BaseModel): id; name; element_type; layer
class MatrixCellRead(BaseModel): source_id; target_id; links: list[RelationshipRead]
class BlockedCellRead(BaseModel): source_id; target_id
class MatrixRead(BaseModel): rows; cols; rows_total; cols_total; truncated; cells; blocked   # .of(matrix)
class MatrixCellDetailRead(BaseModel): source_id; target_id; links; allowed: list[RelationshipType]   # .of(cell)
analysis_service_of(app) -> AnalysisService ; Analysis = Annotated[AnalysisService, Depends(...)]
create_app(..., analysis: AnalysisRepository | None = None)
```
```ts
// components['schemas']['MatrixRead'], ['MatrixCellDetailRead'], ['AxisSelect'] — générés
```

- [ ] **Step 1: Écrire les tests e2e qui échouent**

`backend/tests/e2e/test_analysis_api.py` :

```python
"""The matrix endpoints, seen from outside: status codes, envelopes, who may call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.matrix import MAX_MATRIX_AXIS
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository, StaticVerifier, a_reader


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False), architecture_service=service, analysis=repository
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def an_element(client: httpx.AsyncClient, element_type: str, name: str) -> dict[str, Any]:
    response = await client.post("/elements", json={"element_type": element_type, "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestTheMatrix:
    async def test_a_link_shows_in_its_cell(self, client: httpx.AsyncClient) -> None:
        api = await an_element(client, "application_service", "Invoice API")
        process = await an_element(client, "business_process", "Order to cash")
        created = await client.post(
            "/relationships",
            json={"relationship_type": "serving", "source_id": api["id"], "target_id": process["id"]},
        )
        assert created.status_code == 201

        response = await client.post(
            "/matrix",
            json={"rows": {"element_ids": [api["id"]]}, "cols": {"element_ids": [process["id"]]}},
        )

        body = response.json()
        assert response.status_code == 200
        assert [r["name"] for r in body["rows"]] == ["Invoice API"]
        assert [(c["source_id"], c["target_id"]) for c in body["cells"]] == [
            (api["id"], process["id"])
        ]
        assert body["cells"][0]["links"][0]["relationship_type"] == "serving"
        assert body["truncated"] is False

    async def test_an_empty_catalogue_is_an_empty_grid(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/matrix", json={"rows": {}, "cols": {}})

        assert response.status_code == 200
        assert response.json()["cells"] == []

    async def test_named_elements_over_the_ceiling_get_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(MAX_MATRIX_AXIS + 1)]

        response = await client.post("/matrix", json={"rows": {"element_ids": ids}, "cols": {}})

        assert response.status_code == 422
        assert response.json()["error"] == "matrix_axis_too_large"

    async def test_a_catalogue_axis_over_the_ceiling_is_truncated(
        self, client: httpx.AsyncClient
    ) -> None:
        for index in range(MAX_MATRIX_AXIS + 2):
            await an_element(client, "node", f"node-{index:03}")

        body = (await client.post("/matrix", json={"rows": {}, "cols": {}})).json()

        assert body["truncated"] is True
        assert len(body["rows"]) == MAX_MATRIX_AXIS
        assert body["rows_total"] == MAX_MATRIX_AXIS + 2

    async def test_an_unknown_element_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/matrix",
            json={"rows": {"element_ids": ["00000000-0000-4000-8000-000000000001"]}, "cols": {}},
        )

        assert response.status_code == 404

    async def test_an_unknown_field_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/matrix", json={"rows": {"package_id": "x"}, "cols": {}})

        assert response.status_code == 422


@pytest.mark.asyncio
class TestOneCell:
    async def test_it_offers_only_what_the_metamodel_permits(
        self, client: httpx.AsyncClient
    ) -> None:
        data = await an_element(client, "data_object", "Invoice")
        process = await an_element(client, "business_process", "Order")

        response = await client.get(
            "/matrix/cell", params={"source": data["id"], "target": process["id"]}
        )

        assert response.status_code == 200
        assert "serving" not in response.json()["allowed"]
        assert "association" in response.json()["allowed"]

    async def test_creating_a_link_the_cell_did_not_offer_is_refused_by_the_metamodel(
        self, client: httpx.AsyncClient
    ) -> None:
        """No new write path: the cell creates through POST /relationships, rules intact."""
        data = await an_element(client, "data_object", "Invoice")
        process = await an_element(client, "business_process", "Order")

        refused = await client.post(
            "/relationships",
            json={"relationship_type": "serving", "source_id": data["id"], "target_id": process["id"]},
        )

        assert refused.status_code == 422
        assert refused.json()["error"] == "illegal_relationship"


@pytest.mark.asyncio
class TestWhoMayRead:
    async def test_no_token_is_a_401_and_a_reader_reads(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        verifier = StaticVerifier({"reader-token": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=verifier,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            anonymous = await client.post("/matrix", json={"rows": {}, "cols": {}})
            reader = await client.post(
                "/matrix",
                json={"rows": {}, "cols": {}},
                headers={"Authorization": "Bearer reader-token"},
            )

        assert anonymous.status_code == 401
        assert reader.status_code == 200
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py -x`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'analysis'`.

- [ ] **Step 3: Erreur typée**

Dans `backend/src/ea/api/errors.py`, importer `MatrixAxisTooLargeError` (liste triée existante) et ajouter dans `_STATUS`, avant `# --- Authentication` :

```python
    # --- Model analysis (docs/adr/<ADR lot 1>) ---
    MatrixAxisTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "matrix_axis_too_large"),
```

- [ ] **Step 4: Schémas**

En tête de `backend/src/ea/api/schemas.py`, après l'import `from ea.domain.ipam import (...)` ajouter :

```python
from ea.domain.matrix import MAX_MATRIX_AXIS, AxisSelection, Matrix, MatrixCell
```

À la fin du fichier :

```python
# --- Model analysis (docs/adr/<ADR lot 1>) ---------------------------------

#: How many ids one axis may *submit*. The service refuses more than
#: `MAX_MATRIX_AXIS` with its own typed error; this only stops a runaway body.
MAX_SUBMITTED_AXIS_IDS: Final = 10 * MAX_MATRIX_AXIS


class AxisSelect(_Input):
    """What one axis of the matrix is made of. Nothing set means the whole catalogue."""

    element_ids: list[UUID] = Field(
        default_factory=list,
        max_length=MAX_SUBMITTED_AXIS_IDS,
        description=f"Named elements; more than {MAX_MATRIX_AXIS} is refused.",
    )
    element_types: list[ElementType] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    search: Search | None = None

    def selection(self) -> AxisSelection:
        return AxisSelection(
            element_ids=tuple(self.element_ids),
            element_types=tuple(self.element_types),
            layers=tuple(self.layers),
            search=self.search,
        )


class MatrixQuery(_Input):
    """Both axes. A POST only because they travel in a body: nothing is written."""

    rows: AxisSelect
    cols: AxisSelect


class ElementBrief(BaseModel):
    """An element abridged to what a header needs."""

    id: UUID
    name: str
    element_type: ElementType
    layer: Layer

    @classmethod
    def of(cls, element: Element) -> ElementBrief:
        return cls(
            id=element.id,
            name=element.name,
            element_type=element.element_type,
            layer=element.element_type.layer,
        )


class MatrixCellRead(BaseModel):
    """A cell holding at least one link: row = source, column = target."""

    source_id: UUID
    target_id: UUID
    links: list[RelationshipRead]


class BlockedCellRead(BaseModel):
    source_id: UUID
    target_id: UUID


class MatrixRead(BaseModel):
    """The grid: two axes and the cells that hold links."""

    rows: list[ElementBrief]
    cols: list[ElementBrief]
    rows_total: int
    cols_total: int
    truncated: bool
    cells: list[MatrixCellRead]
    blocked: list[BlockedCellRead]

    @classmethod
    def of(cls, matrix: Matrix) -> MatrixRead:
        return cls(
            rows=[ElementBrief.of(element) for element in matrix.rows.elements],
            cols=[ElementBrief.of(element) for element in matrix.cols.elements],
            rows_total=matrix.rows.total,
            cols_total=matrix.cols.total,
            truncated=matrix.rows.truncated or matrix.cols.truncated,
            cells=[
                MatrixCellRead(
                    source_id=cell.source_id,
                    target_id=cell.target_id,
                    links=[RelationshipRead.of(link) for link in cell.links],
                )
                for cell in matrix.cells
            ],
            blocked=[
                BlockedCellRead(source_id=source, target_id=target)
                for source, target in matrix.blocked
            ],
        )


class MatrixCellDetailRead(BaseModel):
    """One ordered pair: its links, and the relationships ArchiMate would still allow."""

    source_id: UUID
    target_id: UUID
    links: list[RelationshipRead]
    allowed: list[RelationshipType]

    @classmethod
    def of(cls, cell: MatrixCell) -> MatrixCellDetailRead:
        return cls(
            source_id=cell.source_id,
            target_id=cell.target_id,
            links=[RelationshipRead.of(link) for link in cell.links],
            allowed=list(cell.allowed),
        )
```

- [ ] **Step 5: Dépendance et routeur**

Dans `backend/src/ea/api/dependencies.py`, ajouter `from ea.services.analysis import AnalysisService` (ordre alphabétique) et, après `Ipam = ...` :

```python
def analysis_service_of(app: FastAPI) -> AnalysisService:
    """The analysis use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled with a doubled architecture service and
    no `AnalysisRepository` behind it: a wiring fault and a 500, never an empty
    answer a client could not tell from a model with nothing to report.
    """
    service: AnalysisService | None = getattr(app.state, "analysis_service", None)
    if service is None:
        msg = "no analysis service on the application — was it assembled without a graph?"
        raise RuntimeError(msg)
    return service


def get_analysis_service(request: Request) -> AnalysisService:
    return analysis_service_of(request.app)


Analysis = Annotated[AnalysisService, Depends(get_analysis_service)]
```

`backend/src/ea/api/analysis.py` :

```python
"""The model analysis endpoints: the relationship matrix, the quality checks, the dashboards.

Everything here reads. `POST /matrix` is a POST only because two axes do not
fit a query string; creating or removing a link from a cell goes through
`POST /relationships` and `DELETE /relationships/{id}`, so no rule of the
metamodel is bypassed by a second write path (docs/adr/<ADR lot 1>).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ea.api.dependencies import Analysis
from ea.api.schemas import (
    ErrorResponse,
    MatrixCellDetailRead,
    MatrixQuery,
    MatrixRead,
)

router = APIRouter(tags=["analysis"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}


# --- The relationship matrix ----------------------------------------------


@router.post("/matrix", response_model=MatrixRead, responses=REFUSED)
async def read_matrix(payload: MatrixQuery, service: Analysis) -> MatrixRead:
    """The grid of two axes, with the links between them.

    Row = source, column = target: a link lives in exactly one cell. An axis
    given as criteria is cut at the first 50 by name and `truncated` says so;
    an axis given as more than 50 element ids is refused.
    """
    return MatrixRead.of(await service.matrix(payload.rows.selection(), payload.cols.selection()))


@router.get("/matrix/cell", response_model=MatrixCellDetailRead, responses=NOT_FOUND)
async def read_matrix_cell(
    service: Analysis,
    source: Annotated[UUID, Query(description="The element of the row.")],
    target: Annotated[UUID, Query(description="The element of the column.")],
) -> MatrixCellDetailRead:
    """One pair: the links that exist and the relationships still permitted."""
    return MatrixCellDetailRead.of(await service.cell(source, target))
```

- [ ] **Step 6: Câblage dans `main.py`**

- Imports : `from ea.api.analysis import router as analysis_router`, `analysis_service_of` dans la liste importée de `ea.api.dependencies`, `AnalysisRepository` dans la liste de `ea.domain.ports`, `from ea.services.analysis import AnalysisService`.
- Dans le lifespan, après `app.state.ipam_service = IpamService(app.state.architecture_service, repository)` :

```python
                    app.state.analysis_service = AnalysisService(
                        app.state.architecture_service, repository
                    )
```

- Dans `create_app` : ajouter le paramètre `analysis: AnalysisRepository | None = None` après `ipam`, et sous `if ipam is not None:` :

```python
        if analysis is not None:
            app.state.analysis_service = AnalysisService(architecture_service, analysis)
```

- Après `app.include_router(ipam_router, ...)` :

```python
    app.include_router(analysis_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
```

- [ ] **Step 7: Relancer, PASS**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py tests/e2e/test_auth_api.py tests/unit/test_openapi.py -x`
Expected: tous passent.

- [ ] **Step 8: Régénérer le client**

Run: `make openapi`
Expected: « Schéma et client régénérés. » ; `frontend/src/api/schema.d.ts` contient `MatrixRead`, `MatrixCellDetailRead`, `AxisSelect`.

Run: `make openapi-check`
Expected: « Client généré à jour. »

- [ ] **Step 9: Commit**

```bash
git add backend/src/ea/api backend/src/ea/main.py backend/tests/e2e/test_analysis_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(analysis): serve the relationship matrix over HTTP" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: MCP — l'outil `get_relationship_matrix`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports ; alias `AnalysisProvider` ligne ~175 ; signature de `build_mcp_server` ligne ~294 ; nouveau outil après `relationship_matrix_row` ; `INSTRUCTIONS` ligne ~109-151)
- Modify: `backend/src/ea/main.py` (`_mount_mcp`, ligne ~402)
- Modify: `backend/tests/unit/test_mcp_server.py` (fixtures lignes ~40-66, liste complète ligne ~86, nouvelle classe)

**Interfaces:**
- Consumes: `AnalysisService.matrix`, `AxisSelect`, `MatrixRead`
- Produces:
```python
AnalysisProvider = Callable[[], AnalysisService]
def build_mcp_server(get_service, get_documents, get_ipam, get_files, get_analysis: AnalysisProvider, *, version=..., token_verifier=..., auth=...) -> MCPServer[Any]
# tool: get_relationship_matrix(rows: AxisSelect, cols: AxisSelect) -> MatrixRead   (READS)
```

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `backend/tests/unit/test_mcp_server.py` :

1. Importer `AnalysisService` (`from ea.services.analysis import AnalysisService`) et, depuis `ea.api.schemas`, `AxisSelect`.
2. Ajouter `analysis: AnalysisService` aux fixtures `server` et `server_without_an_index` et passer `lambda: analysis` en cinquième argument de `build_mcp_server`.
3. Dans l'ensemble asserté de `test_it_exposes_the_whole_service_and_nothing_else`, ajouter `"get_relationship_matrix",` après `"relationship_matrix_row",`.
4. Ajouter à la fin du fichier :

```python
@pytest.mark.asyncio
class TestTheRelationshipMatrixTool:
    async def test_it_answers_with_the_grid_and_the_links(self, server: MCPServer[Any]) -> None:
        api = await an_element(server, "application_service", "Invoice API")
        process = await an_element(server, "business_process", "Order to cash")
        await call(
            server,
            "connect_elements",
            relationship_type="serving",
            source_id=api["id"],
            target_id=process["id"],
        )

        grid = await call(
            server,
            "get_relationship_matrix",
            rows={"element_ids": [api["id"]]},
            cols={"element_ids": [process["id"]]},
        )

        assert [c["links"][0]["relationship_type"] for c in grid["cells"]] == ["serving"]

    async def test_it_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "get_relationship_matrix")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True

    async def test_an_unknown_element_is_a_message_the_model_can_act_on(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="no element with id"):
            await call(
                server,
                "get_relationship_matrix",
                rows={"element_ids": ["00000000-0000-4000-8000-000000000001"]},
                cols={},
            )

    async def test_too_many_named_elements_are_refused_with_the_ceiling(
        self, server: MCPServer[Any]
    ) -> None:
        ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(51)]

        with pytest.raises(ToolError, match="at most 50"):
            await call(server, "get_relationship_matrix", rows={"element_ids": ids}, cols={})

    async def test_its_axis_bounds_are_the_http_ones(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "get_relationship_matrix")
        sent = tool.input_schema["$defs"]["AxisSelect"]["properties"]["element_ids"]
        served = AxisSelect.model_json_schema()["properties"]["element_ids"]

        assert bounds_of(sent) == bounds_of(served)
        assert bounds_of(sent)["maxItems"] == 500
```

Si `ToolError` n'est pas encore importé dans ce fichier, il l'est déjà (utilisé plus haut par `describe_file`).

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — `TypeError: build_mcp_server() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Implémenter**

Dans `backend/src/ea/mcp/server.py` :

- imports `from ea.api.schemas import (...)` : ajouter `AxisSelect`, `MatrixRead` (ordre alphabétique) ; `from ea.services.analysis import AnalysisService`.
- après `FileProvider = ...` : `AnalysisProvider = Callable[[], AnalysisService]`.
- signature : ajouter `get_analysis: AnalysisProvider,` après `get_files: FileProvider,` ; docstring : « `get_analysis` is required for the same reason ».
- après l'outil `relationship_matrix_row` (chercher `async def relationship_matrix_row`) :

```python
    @server.tool(annotations=READS)
    @speaking_plainly
    async def get_relationship_matrix(rows: AxisSelect, cols: AxisSelect) -> MatrixRead:
        """The grid of links between two sets of elements: rows are sources, columns targets.

        Each axis is either named elements (`element_ids`, at most 50) or the
        catalogue narrowed by `element_types`, `layers` or `search` (then cut
        at the first 50 by name, `truncated` says so); an axis with nothing set
        is the whole catalogue. A cell appears only if it holds a link, and a
        link lives in exactly one cell — its reverse is the mirrored cell.
        This reads: to add a link use `connect_elements` after
        `permitted_relationships`.
        """
        return MatrixRead.of(await get_analysis().matrix(rows.selection(), cols.selection()))
```

- `INSTRUCTIONS` : avant la dernière ligne `describing it is what gives it one.\` ajouter rien ; **après** elle, avant `"""`, ajouter un paragraphe : remplacer

```
record at all; describing it is what gives it one.\
"""
```
par
```
record at all; describing it is what gives it one.

`get_relationship_matrix` answers "which of these elements are linked to which
of those": a grid, rows as sources and columns as targets, listing only the
cells that hold a link. Use it to see the links between two groups at once
instead of calling `read_element_relations` for each element.\
"""
```

- Dans `backend/src/ea/main.py`, `_mount_mcp` : ajouter `lambda: analysis_service_of(app),` après `lambda: file_service_of(app),`.

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_auth.py tests/e2e/test_mcp_endpoint.py -x`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/src/ea/main.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): add the read-only get_relationship_matrix tool" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: SPA — axes et navigation clavier, purs

**Files:**
- Create: `frontend/src/features/matrix/axis.ts`
- Create: `frontend/src/features/matrix/gridNavigation.ts`
- Create: `frontend/tests/matrixAxis.spec.ts`
- Create: `frontend/tests/gridNavigation.spec.ts`

**Interfaces:**
- Consumes: `components['schemas']['{AxisSelect,Layer,ElementType}']`
- Produces:
```ts
export type AxisChoice =
  | { kind: 'all' }
  | { kind: 'layer'; layer: Layer }
  | { kind: 'type'; type: ElementType }
  | { kind: 'ids'; ids: string[] }
export function parseAxis(text: string | undefined): AxisChoice      // unknown or empty → { kind: 'all' }
export function axisToParam(choice: AxisChoice): string               // 'all' | 'layer:business' | 'type:node' | 'ids:a,b'
export function axisToBody(choice: AxisChoice): AxisSelect
export type Position = { row: number; col: number }
export function moveFocus(from: Position, key: string, size: { rows: number; cols: number }): Position
export function cellToParam(source: string, target: string): string   // `${source},${target}`
export function parseCell(text: string | undefined): { source: string; target: string } | null
```

- [ ] **Step 1: Écrire les tests qui échouent**

`frontend/tests/matrixAxis.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import { axisToBody, axisToParam, parseAxis } from '../src/features/matrix/axis'
import { cellToParam, parseCell } from '../src/features/matrix/gridNavigation'

describe('the axis in the URL', () => {
  it('reads nothing, or something unknown, as the whole catalogue', () => {
    expect(parseAxis(undefined)).toEqual({ kind: 'all' })
    expect(parseAxis('')).toEqual({ kind: 'all' })
    expect(parseAxis('banane:3')).toEqual({ kind: 'all' })
  })

  it('round-trips a layer, a type and a list of ids', () => {
    for (const text of ['all', 'layer:business', 'type:node', 'ids:aaa,bbb']) {
      expect(axisToParam(parseAxis(text))).toBe(text)
    }
  })

  it('drops empty ids rather than sending them', () => {
    expect(parseAxis('ids:aaa,,bbb,')).toEqual({ kind: 'ids', ids: ['aaa', 'bbb'] })
    expect(parseAxis('ids:')).toEqual({ kind: 'all' })
  })

  it('builds the request body each choice means', () => {
    expect(axisToBody({ kind: 'all' })).toEqual({})
    expect(axisToBody({ kind: 'layer', layer: 'business' })).toEqual({ layers: ['business'] })
    expect(axisToBody({ kind: 'type', type: 'node' })).toEqual({ element_types: ['node'] })
    expect(axisToBody({ kind: 'ids', ids: ['a'] })).toEqual({ element_ids: ['a'] })
  })
})

describe('the open cell in the URL', () => {
  it('round-trips a source and a target', () => {
    expect(parseCell(cellToParam('s-1', 't-2'))).toEqual({ source: 's-1', target: 't-2' })
  })

  it('reads anything else as no cell', () => {
    expect(parseCell(undefined)).toBeNull()
    expect(parseCell('seul')).toBeNull()
    expect(parseCell(',t')).toBeNull()
  })
})
```

`frontend/tests/gridNavigation.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import { moveFocus } from '../src/features/matrix/gridNavigation'

const SIZE = { rows: 3, cols: 4 }

describe('moveFocus', () => {
  it('moves one cell with the arrows', () => {
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowRight', SIZE)).toEqual({ row: 1, col: 2 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowLeft', SIZE)).toEqual({ row: 1, col: 0 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowDown', SIZE)).toEqual({ row: 2, col: 1 })
    expect(moveFocus({ row: 1, col: 1 }, 'ArrowUp', SIZE)).toEqual({ row: 0, col: 1 })
  })

  it('stops at the edges instead of wrapping', () => {
    expect(moveFocus({ row: 0, col: 0 }, 'ArrowUp', SIZE)).toEqual({ row: 0, col: 0 })
    expect(moveFocus({ row: 2, col: 3 }, 'ArrowRight', SIZE)).toEqual({ row: 2, col: 3 })
  })

  it('jumps to the ends of the row with Home and End', () => {
    expect(moveFocus({ row: 1, col: 2 }, 'Home', SIZE)).toEqual({ row: 1, col: 0 })
    expect(moveFocus({ row: 1, col: 2 }, 'End', SIZE)).toEqual({ row: 1, col: 3 })
  })

  it('leaves the position alone for any other key', () => {
    expect(moveFocus({ row: 1, col: 1 }, 'a', SIZE)).toEqual({ row: 1, col: 1 })
  })
})
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/matrixAxis.spec.ts tests/gridNavigation.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/matrix/axis"`.

- [ ] **Step 3: Implémenter**

`frontend/src/features/matrix/axis.ts` :

```ts
// What an axis of the matrix is, as the URL spells it and as the API wants it.
//
// A choice is one of: the whole catalogue, one layer, one element type, or a
// list of named elements (which other screens will hand over by link). The
// values are passed through as typed — whether `layer:business` names a real
// layer is the API's to say, never a list kept here (docs/adr/0012).
import type { components } from '../../api/schema'

export type AxisSelect = components['schemas']['AxisSelect']
type Layer = components['schemas']['Layer']
type ElementType = components['schemas']['ElementType']

export type AxisChoice =
  | { kind: 'all' }
  | { kind: 'layer'; layer: Layer }
  | { kind: 'type'; type: ElementType }
  | { kind: 'ids'; ids: string[] }

export function parseAxis(text: string | undefined): AxisChoice {
  if (!text) {
    return { kind: 'all' }
  }
  const split = text.indexOf(':')
  const head = split < 0 ? text : text.slice(0, split)
  const rest = split < 0 ? '' : text.slice(split + 1)
  if (head === 'layer' && rest) {
    return { kind: 'layer', layer: rest as Layer }
  }
  if (head === 'type' && rest) {
    return { kind: 'type', type: rest as ElementType }
  }
  if (head === 'ids') {
    const ids = rest.split(',').filter(Boolean)
    return ids.length > 0 ? { kind: 'ids', ids } : { kind: 'all' }
  }
  return { kind: 'all' }
}

export function axisToParam(choice: AxisChoice): string {
  switch (choice.kind) {
    case 'all':
      return 'all'
    case 'layer':
      return `layer:${choice.layer}`
    case 'type':
      return `type:${choice.type}`
    case 'ids':
      return `ids:${choice.ids.join(',')}`
  }
}

export function axisToBody(choice: AxisChoice): AxisSelect {
  switch (choice.kind) {
    case 'all':
      return {}
    case 'layer':
      return { layers: [choice.layer] }
    case 'type':
      return { element_types: [choice.type] }
    case 'ids':
      return { element_ids: choice.ids }
  }
}
```

`frontend/src/features/matrix/gridNavigation.ts` :

```ts
// Keyboard movement over the matrix grid, and the open cell's URL spelling.
// Pure, so it is tested without mounting anything.

export type Position = { row: number; col: number }

/** Where a key takes the focus. Stops at the edges: a grid does not wrap. */
export function moveFocus(
  from: Position,
  key: string,
  size: { rows: number; cols: number },
): Position {
  const lastRow = Math.max(0, size.rows - 1)
  const lastCol = Math.max(0, size.cols - 1)
  switch (key) {
    case 'ArrowRight':
      return { row: from.row, col: Math.min(lastCol, from.col + 1) }
    case 'ArrowLeft':
      return { row: from.row, col: Math.max(0, from.col - 1) }
    case 'ArrowDown':
      return { row: Math.min(lastRow, from.row + 1), col: from.col }
    case 'ArrowUp':
      return { row: Math.max(0, from.row - 1), col: from.col }
    case 'Home':
      return { row: from.row, col: 0 }
    case 'End':
      return { row: from.row, col: lastCol }
    default:
      return from
  }
}

export function cellToParam(source: string, target: string): string {
  return `${source},${target}`
}

export function parseCell(text: string | undefined): { source: string; target: string } | null {
  if (!text) {
    return null
  }
  const [source, target, ...extra] = text.split(',')
  return source && target && extra.length === 0 ? { source, target } : null
}
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/matrixAxis.spec.ts tests/gridNavigation.spec.ts`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/matrix/axis.ts frontend/src/features/matrix/gridNavigation.ts frontend/tests/matrixAxis.spec.ts frontend/tests/gridNavigation.spec.ts
git commit -m "feat(matrix): add the axis and grid navigation helpers" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: SPA — `useMatrix`, le composable de la matrice

**Files:**
- Create: `frontend/src/features/matrix/useMatrix.ts`
- Create: `frontend/tests/useMatrix.spec.ts`
- Modify: `frontend/tests/support/api.ts` (fabriques `aMatrix`-like : `aGrid`, `aCellDetail` — nommer `aGrid` car `aMatrix` existe déjà pour le métamodèle)

**Interfaces:**
- Consumes: `api`, `unwrap`, `useLatestRequest`, `axisToBody`, schémas `MatrixRead`, `MatrixCellDetailRead`, `RelationshipType`
- Produces:
```ts
export type MatrixRead = components['schemas']['MatrixRead']
export type MatrixCellDetailRead = components['schemas']['MatrixCellDetailRead']
export function useMatrix(): {
  matrix: Ref<MatrixRead | null>
  detail: Ref<MatrixCellDetailRead | null>
  status: Ref<Status>; error: Ref<string>
  cellStatus: Ref<Status>; cellError: Ref<string>
  load(rows: AxisChoice, cols: AxisChoice): Promise<void>
  openCell(source: string, target: string): Promise<void>
  closeCell(): void
  connect(type: RelationshipType): Promise<void>      // POST /relationships for the open cell, then reload both
  disconnect(relationshipId: string): Promise<void>   // DELETE /relationships/{id}, then reload both
}
```
```ts
// tests/support/api.ts
export function aGrid(overrides?: Partial<MatrixRead>): MatrixRead
export function aCellDetail(overrides?: Partial<MatrixCellDetailRead>): MatrixCellDetailRead
```

- [ ] **Step 1: Fabriques**

Dans `frontend/tests/support/api.ts`, ajouter en haut `type MatrixRead = components['schemas']['MatrixRead']` et `type MatrixCellDetailRead = components['schemas']['MatrixCellDetailRead']`, puis à la fin :

```ts
/** A grid as `POST /matrix` returns it — two rows, two columns, one link. */
export function aGrid(overrides: Partial<MatrixRead> = {}): MatrixRead {
  return {
    rows: [
      { id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Invoice API', element_type: 'application_service', layer: 'application' },
      { id: 'cccccccc-3333-4333-8333-333333333333', name: 'Billing', element_type: 'application_component', layer: 'application' },
    ],
    cols: [
      { id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Order to cash', element_type: 'business_process', layer: 'business' },
    ],
    rows_total: 2,
    cols_total: 1,
    truncated: false,
    cells: [
      {
        source_id: 'aaaaaaaa-1111-4111-8111-111111111111',
        target_id: 'bbbbbbbb-2222-4222-8222-222222222222',
        links: [aRelationship()],
      },
    ],
    blocked: [],
    ...overrides,
  }
}

/** One cell as `GET /matrix/cell` returns it. */
export function aCellDetail(overrides: Partial<MatrixCellDetailRead> = {}): MatrixCellDetailRead {
  return {
    source_id: 'aaaaaaaa-1111-4111-8111-111111111111',
    target_id: 'bbbbbbbb-2222-4222-8222-222222222222',
    links: [aRelationship()],
    allowed: ['serving', 'association'],
    ...overrides,
  }
}
```

- [ ] **Step 2: Écrire les tests qui échouent**

`frontend/tests/useMatrix.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMatrix } from '../src/features/matrix/useMatrix'
import { aCellDetail, aGrid, aRelationship, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SOURCE = 'aaaaaaaa-1111-4111-8111-111111111111'
const TARGET = 'bbbbbbbb-2222-4222-8222-222222222222'

describe('useMatrix', () => {
  it('posts both axes as the API wants them', async () => {
    const calls = stubApi([{ method: 'POST', path: '/matrix', body: aGrid() }])
    const matrix = useMatrix()

    await matrix.load({ kind: 'layer', layer: 'business' }, { kind: 'all' })

    expect(calls[0].body).toEqual({ rows: { layers: ['business'] }, cols: {} })
    expect(matrix.matrix.value?.rows).toHaveLength(2)
    expect(matrix.status.value).toBe('ready')
  })

  it('reports a refusal in words and shows no stale grid', async () => {
    stubApi([
      { method: 'POST', path: '/matrix', body: aGrid() },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })

    stubApi([
      {
        method: 'POST',
        path: '/matrix',
        status: 422,
        body: { error: 'matrix_axis_too_large', detail: 'an axis names 60 elements' },
      },
    ])
    await matrix.load({ kind: 'ids', ids: ['x'] }, { kind: 'all' })

    expect(matrix.matrix.value).toBeNull()
    expect(matrix.error.value).toBe('an axis names 60 elements')
  })

  it('keeps only the answer to the grid asked last', async () => {
    const held = deferApi()
    const matrix = useMatrix()

    const first = matrix.load({ kind: 'layer', layer: 'business' }, { kind: 'all' })
    const second = matrix.load({ kind: 'layer', layer: 'application' }, { kind: 'all' })
    // The client awaits the token before it fetches: wait for both requests to be in flight.
    await vi.waitFor(() => expect(held).toHaveLength(2))
    held[1].answer(aGrid({ rows_total: 7 }))
    held[0].answer(aGrid({ rows_total: 1 }))
    await Promise.all([first, second])

    expect(held[0].aborted()).toBe(true)
    expect(matrix.matrix.value?.rows_total).toBe(7)
  })

  it('opens a cell with the pair in the query', async () => {
    const calls = stubApi([{ path: '/matrix/cell', body: aCellDetail() }])
    const matrix = useMatrix()

    await matrix.openCell(SOURCE, TARGET)

    expect(calls[0].url.searchParams.get('source')).toBe(SOURCE)
    expect(calls[0].url.searchParams.get('target')).toBe(TARGET)
    expect(matrix.detail.value?.allowed).toEqual(['serving', 'association'])
  })

  it('creates through POST /relationships, then reloads the cell and the grid', async () => {
    const calls = stubApi([
      { path: '/matrix/cell', body: aCellDetail() },
      { method: 'POST', path: '/matrix', body: aGrid() },
      { method: 'POST', path: '/relationships', status: 201, body: aRelationship() },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })
    await matrix.openCell(SOURCE, TARGET)

    await matrix.connect('association')

    const post = calls.find((call) => call.method === 'POST' && call.url.pathname === '/relationships')
    expect(post?.body).toEqual({
      relationship_type: 'association',
      source_id: SOURCE,
      target_id: TARGET,
    })
    expect(calls.filter((call) => call.url.pathname === '/matrix/cell')).toHaveLength(2)
    expect(calls.filter((call) => call.url.pathname === '/matrix')).toHaveLength(2)
  })

  it('removes through DELETE /relationships/{id}', async () => {
    const link = aRelationship()
    const calls = stubApi([
      { path: '/matrix/cell', body: aCellDetail({ links: [link] }) },
      { method: 'POST', path: '/matrix', body: aGrid() },
      { method: 'DELETE', path: `/relationships/${link.id}`, status: 204 },
    ])
    const matrix = useMatrix()
    await matrix.load({ kind: 'all' }, { kind: 'all' })
    await matrix.openCell(SOURCE, TARGET)

    await matrix.disconnect(link.id)

    expect(calls.some((call) => call.method === 'DELETE')).toBe(true)
  })

  it('forgets the open cell when it is closed', async () => {
    stubApi([{ path: '/matrix/cell', body: aCellDetail() }])
    const matrix = useMatrix()
    await matrix.openCell(SOURCE, TARGET)

    matrix.closeCell()

    expect(matrix.detail.value).toBeNull()
  })
})
```

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/useMatrix.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/matrix/useMatrix"`.

- [ ] **Step 4: Implémenter**

`frontend/src/features/matrix/useMatrix.ts` :

```ts
// The matrix screen's state: one grid, one open cell, and the two writes.
//
// The grid and the cell are two independent questions, so each has its own
// `useLatestRequest` (lib/latest.ts): opening a cell must not cancel the grid
// being reloaded beside it. Writes are the *existing* endpoints — a link is
// created by `POST /relationships` and removed by `DELETE /relationships/{id}`
// — so the metamodel stays the only judge of what a cell accepts.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { axisToBody, type AxisChoice } from './axis'

export type MatrixRead = components['schemas']['MatrixRead']
export type MatrixCellDetailRead = components['schemas']['MatrixCellDetailRead']
export type RelationshipType = components['schemas']['RelationshipType']

export function useMatrix() {
  const matrix = ref<MatrixRead | null>(null)
  const detail = ref<MatrixCellDetailRead | null>(null)
  const grid = useLatestRequest()
  const cell = useLatestRequest()
  let lastAxes: { rows: AxisChoice; cols: AxisChoice } | null = null

  async function load(rows: AxisChoice, cols: AxisChoice): Promise<void> {
    lastAxes = { rows, cols }
    await grid.run(
      async (signal) =>
        unwrap(
          await api.POST('/matrix', {
            body: { rows: axisToBody(rows), cols: axisToBody(cols) },
            signal,
          }),
        ),
      (answer) => {
        matrix.value = answer
      },
      () => {
        // A refused question must not leave the previous answer on screen.
        matrix.value = null
      },
    )
  }

  async function openCell(source: string, target: string): Promise<void> {
    await cell.run(
      async (signal) =>
        unwrap(await api.GET('/matrix/cell', { params: { query: { source, target } }, signal })),
      (answer) => {
        detail.value = answer
      },
      () => {
        detail.value = null
      },
    )
  }

  function closeCell(): void {
    cell.cancel()
    detail.value = null
  }

  async function refresh(): Promise<void> {
    const open = detail.value
    await Promise.all([
      open ? openCell(open.source_id, open.target_id) : Promise.resolve(),
      lastAxes ? load(lastAxes.rows, lastAxes.cols) : Promise.resolve(),
    ])
  }

  async function connect(type: RelationshipType): Promise<void> {
    const open = detail.value
    if (!open) {
      return
    }
    unwrap(
      await api.POST('/relationships', {
        body: { relationship_type: type, source_id: open.source_id, target_id: open.target_id },
      }),
    )
    await refresh()
  }

  async function disconnect(relationshipId: string): Promise<void> {
    unwrap(
      await api.DELETE('/relationships/{relationship_id}', {
        params: { path: { relationship_id: relationshipId } },
      }),
    )
    await refresh()
  }

  return {
    matrix,
    detail,
    status: grid.status,
    error: grid.error,
    cellStatus: cell.status,
    cellError: cell.error,
    load,
    openCell,
    closeCell,
    connect,
    disconnect,
  }
}
```

- [ ] **Step 5: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/useMatrix.spec.ts`
Expected: 7 passed.

- [ ] **Step 6: Typage et lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: aucune erreur.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/matrix/useMatrix.ts frontend/tests/useMatrix.spec.ts frontend/tests/support/api.ts
git commit -m "feat(matrix): add the useMatrix composable" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: SPA — `MatrixGrid.vue`, la grille au clavier

**Files:**
- Create: `frontend/src/features/matrix/MatrixGrid.vue`
- Create: `frontend/tests/MatrixGrid.spec.ts`

**Interfaces:**
- Consumes: `MatrixRead`, `moveFocus`, `RELATIONSHIP_LABELS`
- Produces:
```ts
// props: { matrix: MatrixRead; selected: { source: string; target: string } | null }
// emits: open(source: string, target: string) ; close()
```

- [ ] **Step 1: Écrire les tests qui échouent**

`frontend/tests/MatrixGrid.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import MatrixGrid from '../src/features/matrix/MatrixGrid.vue'
import { aGrid, aRelationship } from './support/api'

const API = {
  id: 'aaaaaaaa-1111-4111-8111-111111111111',
  name: 'Invoice API',
  element_type: 'application_service',
  layer: 'application',
} as const
const PROCESS = {
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Order to cash',
  element_type: 'business_process',
  layer: 'business',
} as const

/** Both elements on both axes, one link running API → PROCESS only. */
const SQUARE = aGrid({
  rows: [API, PROCESS],
  cols: [API, PROCESS],
  rows_total: 2,
  cols_total: 2,
  cells: [{ source_id: API.id, target_id: PROCESS.id, links: [aRelationship()] }],
})

function renderGrid(matrix = SQUARE, selected = null) {
  return render(MatrixGrid, { props: { matrix, selected } })
}

describe('MatrixGrid', () => {
  it('is a grid with the names as headers', () => {
    renderGrid()

    expect(screen.getByRole('grid', { name: /matrice de relations/i })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'Order to cash' })).toHaveLength(1)
    expect(screen.getAllByRole('rowheader', { name: 'Invoice API' })).toHaveLength(1)
  })

  it('draws a link in its own direction only', () => {
    renderGrid()

    const forward = screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })
    const backward = screen.getByRole('gridcell', { name: /Order to cash vers Invoice API/ })

    expect(within(forward).getByText(/sert/)).toBeInTheDocument()
    expect(within(backward).queryByText(/sert/)).toBeNull()
  })

  it('keeps exactly one cell in the tab order', () => {
    renderGrid()

    const reachable = screen.getAllByRole('gridcell').filter((cell) => cell.tabIndex === 0)
    expect(reachable).toHaveLength(1)
  })

  it('moves the focus with the arrows', async () => {
    renderGrid()
    const cells = screen.getAllByRole('gridcell')
    cells[0].focus()

    await fireEvent.keyDown(cells[0], { key: 'ArrowRight' })

    await waitFor(() => expect(document.activeElement).toBe(cells[1]))
    expect(cells[1].tabIndex).toBe(0)
    expect(cells[0].tabIndex).toBe(-1)
  })

  it('opens the cell on Enter and closes on Escape', async () => {
    const { emitted } = renderGrid()
    const cell = screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })

    await fireEvent.keyDown(cell, { key: 'Enter' })
    await fireEvent.keyDown(cell, { key: 'Escape' })

    expect(emitted().open).toEqual([[API.id, PROCESS.id]])
    expect(emitted().close).toHaveLength(1)
  })

  it('does not open a pair the metamodel permits nothing for', async () => {
    const { emitted } = renderGrid(aGrid({ ...SQUARE, blocked: [{ source_id: PROCESS.id, target_id: API.id }] }))
    const cell = screen.getByRole('gridcell', { name: /Order to cash vers Invoice API/ })

    await fireEvent.keyDown(cell, { key: 'Enter' })

    expect(cell).toHaveAttribute('aria-disabled', 'true')
    expect(emitted().open).toBeUndefined()
  })

  it('says so, and draws no grid, when an axis is empty', () => {
    renderGrid(aGrid({ rows: [], rows_total: 0, cells: [] }))

    expect(screen.getByText(/aucun élément/i)).toBeInTheDocument()
    expect(screen.queryByRole('grid')).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/MatrixGrid.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/matrix/MatrixGrid.vue"`.

- [ ] **Step 3: Implémenter**

`frontend/src/features/matrix/MatrixGrid.vue` :

```vue
<script setup lang="ts">
// The grid itself: names on both axes, one cell per ordered pair.
//
// Rows are sources, columns are targets; a link is drawn in the cell of its own
// direction and never mirrored. It fetches nothing and decides nothing: which
// pairs are blocked, and what a cell holds, comes in `matrix` from the API.
//
// One cell is in the tab order at a time (roving tabindex): the arrows move,
// Enter or Space opens the cell, Escape closes the panel. A cell is a real
// focusable element, not a painted pixel.
import { computed, nextTick, ref, useTemplateRef } from 'vue'

import { RELATIONSHIP_LABELS } from '../relationships/labels'
import { moveFocus, type Position } from './gridNavigation'
import type { MatrixRead } from './useMatrix'

const props = defineProps<{
  matrix: MatrixRead
  selected: { source: string; target: string } | null
}>()
const emit = defineEmits<{ open: [source: string, target: string]; close: [] }>()

const grid = useTemplateRef<HTMLElement>('grid')
const focus = ref<Position>({ row: 0, col: 0 })

const linked = computed(
  () => new Map(props.matrix.cells.map((cell) => [`${cell.source_id}|${cell.target_id}`, cell])),
)
const blocked = computed(
  () => new Set(props.matrix.blocked.map((pair) => `${pair.source_id}|${pair.target_id}`)),
)

const isEmpty = computed(() => props.matrix.rows.length === 0 || props.matrix.cols.length === 0)

function keyOf(row: number, col: number): string {
  return `${props.matrix.rows[row]?.id ?? ''}|${props.matrix.cols[col]?.id ?? ''}`
}

function linksIn(row: number, col: number) {
  return linked.value.get(keyOf(row, col))?.links ?? []
}

function isBlocked(row: number, col: number): boolean {
  return blocked.value.has(keyOf(row, col))
}

function isSelected(row: number, col: number): boolean {
  return props.selected !== null && keyOf(row, col) === `${props.selected.source}|${props.selected.target}`
}

function label(row: number, col: number): string {
  const count = linksIn(row, col).length
  const from = props.matrix.rows[row]?.name ?? ''
  const to = props.matrix.cols[col]?.name ?? ''
  return `${from} vers ${to} : ${count} lien${count > 1 ? 's' : ''}`
}

function open(row: number, col: number): void {
  const source = props.matrix.rows[row]
  const target = props.matrix.cols[col]
  focus.value = { row, col }
  if (!source || !target || isBlocked(row, col)) {
    return
  }
  emit('open', source.id, target.id)
}

async function onKeydown(event: KeyboardEvent, row: number, col: number): Promise<void> {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    open(row, col)
    return
  }
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  const next = moveFocus({ row, col }, event.key, {
    rows: props.matrix.rows.length,
    cols: props.matrix.cols.length,
  })
  if (next.row === row && next.col === col) {
    return
  }
  event.preventDefault()
  focus.value = next
  await nextTick()
  grid.value
    ?.querySelector<HTMLElement>(`[data-r="${next.row}"][data-c="${next.col}"]`)
    ?.focus()
}
</script>

<template>
  <p v-if="isEmpty" class="hint">Aucun élément sur au moins un des deux axes : rien à croiser.</p>

  <div v-else class="matrix-scroll">
    <table ref="grid" class="matrix" role="grid" aria-label="Matrice de relations">
      <thead>
        <tr>
          <td></td>
          <th v-for="col in matrix.cols" :key="col.id" scope="col" role="columnheader">
            {{ col.name }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, r) in matrix.rows" :key="row.id" role="row">
          <th scope="row" role="rowheader">{{ row.name }}</th>
          <td
            v-for="(col, c) in matrix.cols"
            :key="col.id"
            role="gridcell"
            class="matrix__cell"
            :class="{ 'matrix__cell--blocked': isBlocked(r, c), 'matrix__cell--open': isSelected(r, c) }"
            :data-r="r"
            :data-c="c"
            :tabindex="focus.row === r && focus.col === c ? 0 : -1"
            :aria-label="label(r, c)"
            :aria-disabled="isBlocked(r, c) ? 'true' : undefined"
            :aria-selected="isSelected(r, c)"
            @click="open(r, c)"
            @keydown="onKeydown($event, r, c)"
          >
            <span v-for="link in linksIn(r, c)" :key="link.id" class="matrix__pill">
              → {{ RELATIONSHIP_LABELS[link.relationship_type] }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.matrix-scroll {
  overflow: auto;
}
.matrix {
  border-collapse: collapse;
}
.matrix th,
.matrix td {
  border: 1px solid var(--border, #c9c4d1);
  padding: 0.25rem 0.5rem;
  text-align: left;
}
.matrix__cell {
  min-width: 7rem;
  cursor: pointer;
}
.matrix__cell:focus-visible {
  outline: 2px solid var(--accent, #5b3fd1);
  outline-offset: -2px;
}
.matrix__cell--open {
  background: color-mix(in srgb, var(--accent, #5b3fd1) 15%, transparent);
}
.matrix__cell--blocked {
  background: repeating-linear-gradient(45deg, transparent 0 6px, rgb(128 128 128 / 0.25) 6px 8px);
  cursor: not-allowed;
}
.matrix__pill {
  display: block;
  font-size: 0.85em;
  white-space: nowrap;
}
</style>
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/MatrixGrid.spec.ts`
Expected: 7 passed.

- [ ] **Step 5: Typage et lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/matrix/MatrixGrid.vue frontend/tests/MatrixGrid.spec.ts
git commit -m "feat(matrix): add the keyboard-driven grid" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: SPA — le panneau de cellule, la section, l'entrée de menu

**Files:**
- Create: `frontend/src/features/matrix/MatrixCellPanel.vue`
- Create: `frontend/src/features/matrix/MatrixSection.vue`
- Create: `frontend/tests/MatrixSection.spec.ts`
- Modify: `frontend/src/router/sections.ts` (après l'entrée `impact`, ligne ~89)

**Interfaces:**
- Consumes: `useMatrix`, `MatrixGrid`, `useMetamodel` (`layers`, `byLayer`, `LAYER_LABELS`), `useMe().canWrite`, `RELATIONSHIP_LABELS`, `verbOf`, `messageOf`
- Produces:
```ts
// MatrixCellPanel props: { detail: MatrixCellDetailRead; sourceName: string; targetName: string; canWrite: boolean; busy: boolean; failure: string }
// emits: connect(type: RelationshipType) ; disconnect(id: string) ; close()
// route: { path: '/analyse/matrice', name: 'matrix', label: 'Matrice', group: 'analysis' }, query ?rows= ?cols= ?cell=
```

- [ ] **Step 1: Écrire les tests qui échouent**

`frontend/tests/MatrixSection.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'

import MatrixSection from '../src/features/matrix/MatrixSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aCellDetail, aGrid, aRelationship, METAMODEL, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SOURCE = 'aaaaaaaa-1111-4111-8111-111111111111'
const TARGET = 'bbbbbbbb-2222-4222-8222-222222222222'

const PALETTE: Route = { path: '/metamodel', body: METAMODEL }
const GRID: Route = { method: 'POST', path: '/matrix', body: aGrid() }
const CELL: Route = { path: '/matrix/cell', body: aCellDetail() }
const CREATE: Route = { method: 'POST', path: '/relationships', status: 201, body: aRelationship() }

async function open(routes: Route[], query = ''): Promise<{ router: Router; calls: ReturnType<typeof stubApi> }> {
  const calls = stubApi([PALETTE, GRID, ...routes])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/matrice${query}`)
  await router.isReady()
  render(MatrixSection, { global: { plugins: [router] } })
  return { router, calls }
}

function asReader(): void {
  vi.mocked(useMe).mockReturnValueOnce({
    me: ref({ username: 'reader', can_write: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  })
}

describe('MatrixSection', () => {
  it('draws the grid the API returns', async () => {
    await open([])

    expect(await screen.findByRole('grid', { name: /matrice de relations/i })).toBeInTheDocument()
    expect(screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })).toBeInTheDocument()
  })

  it('reads the axes from the URL and sends them as the API wants', async () => {
    const { calls } = await open([], '?rows=layer:business&cols=type:node')

    await screen.findByRole('grid')
    const post = calls.find((call) => call.method === 'POST' && call.url.pathname === '/matrix')
    expect(post?.body).toEqual({ rows: { layers: ['business'] }, cols: { element_types: ['node'] } })
  })

  it('warns when an axis was cut, with the total', async () => {
    stubApi([PALETTE, { method: 'POST', path: '/matrix', body: aGrid({ truncated: true, rows_total: 130 }) }])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/analyse/matrice')
    await router.isReady()
    render(MatrixSection, { global: { plugins: [router] } })

    expect(await screen.findByText(/130/)).toBeInTheDocument()
    expect(screen.getByText(/affinez/i)).toBeInTheDocument()
  })

  it('opens a cell by replacing the URL, and offers only what the API permits', async () => {
    const { router } = await open([CELL])
    const cell = await screen.findByRole('gridcell', { name: /Invoice API vers Order to cash/ })

    await fireEvent.click(cell)

    await waitFor(() => expect(router.currentRoute.value.query.cell).toBe(`${SOURCE},${TARGET}`))
    const select = await screen.findByLabelText(/nouveau lien/i)
    const options = within(select).getAllByRole('option').map((option) => option.textContent?.trim())
    expect(options).toEqual(['Choisis une relation…', 'sert', 'est associé à'])
    // Replaced, not pushed: going back leaves the screen instead of closing the cell.
    await router.back()
    expect(router.currentRoute.value.path).not.toBe('/analyse/matrice')
  })

  it('pushes a history entry when an axis changes', async () => {
    const { router } = await open([], '?rows=all')
    await screen.findByRole('grid')

    await fireEvent.update(screen.getByLabelText(/lignes/i), 'layer:business')
    await waitFor(() => expect(router.currentRoute.value.query.rows).toBe('layer:business'))
    await router.back()

    expect(router.currentRoute.value.query.rows).toBe('all')
  })

  it('creates a link from the open cell through POST /relationships', async () => {
    const { calls } = await open([CELL, CREATE], `?cell=${SOURCE},${TARGET}`)
    const select = await screen.findByLabelText(/nouveau lien/i)

    await fireEvent.update(select, 'association')
    await fireEvent.click(screen.getByRole('button', { name: /^relier$/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.pathname === '/relationships')
      expect(post?.body).toEqual({
        relationship_type: 'association',
        source_id: SOURCE,
        target_id: TARGET,
      })
    })
  })

  it('removes a link after a confirmation', async () => {
    const link = aRelationship()
    const { calls } = await open(
      [CELL, { method: 'DELETE', path: `/relationships/${link.id}`, status: 204 }],
      `?cell=${SOURCE},${TARGET}`,
    )

    await fireEvent.click(await screen.findByRole('button', { name: /^supprimer$/i }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: /^confirmer$/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('offers a reader the list of links and no write control', async () => {
    asReader()
    await open([CELL], `?cell=${SOURCE},${TARGET}`)

    expect(await screen.findByText(/sert/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /supprimer/i })).toBeNull()
    expect(screen.queryByLabelText(/nouveau lien/i)).toBeNull()
  })

  it('shows the API refusal in words', async () => {
    await open(
      [
        CELL,
        {
          method: 'POST',
          path: '/relationships',
          status: 422,
          body: {
            error: 'illegal_relationship',
            detail: 'serving: data_object -> business_process is not permitted',
          },
        },
      ],
      `?cell=${SOURCE},${TARGET}`,
    )

    await fireEvent.update(await screen.findByLabelText(/nouveau lien/i), 'serving')
    await fireEvent.click(screen.getByRole('button', { name: /^relier$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not permitted/)
  })
})
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/MatrixSection.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/matrix/MatrixSection.vue"`.

- [ ] **Step 3: Le panneau**

`frontend/src/features/matrix/MatrixCellPanel.vue` :

```vue
<script setup lang="ts">
// One cell, opened: the links it holds and, for an editor, the form that adds
// one. The form offers `detail.allowed` and nothing else — the list comes from
// `GET /matrix/cell`, which asks the metamodel; no type is computed here.
import { ref, watch } from 'vue'

import { RELATIONSHIP_LABELS, verbOf } from '../relationships/labels'
import type { MatrixCellDetailRead, RelationshipType } from './useMatrix'

const props = defineProps<{
  detail: MatrixCellDetailRead
  sourceName: string
  targetName: string
  canWrite: boolean
  busy: boolean
  failure: string
}>()
const emit = defineEmits<{
  connect: [type: RelationshipType]
  disconnect: [id: string]
  close: []
}>()

const chosen = ref<RelationshipType | ''>('')
/** The link whose removal is waiting for a second click. */
const confirming = ref<string | null>(null)

// A different cell, or the same one after a write, starts the form afresh.
watch(
  () => props.detail,
  () => {
    chosen.value = ''
    confirming.value = null
  },
)

function submit(): void {
  if (chosen.value) {
    emit('connect', chosen.value)
  }
}

function remove(id: string): void {
  if (confirming.value !== id) {
    confirming.value = id
    return
  }
  confirming.value = null
  emit('disconnect', id)
}
</script>

<template>
  <aside class="cell-panel" aria-label="Liens de la cellule" @keydown.esc="emit('close')">
    <header>
      <h3>{{ sourceName }} → {{ targetName }}</h3>
      <button type="button" @click="emit('close')">Fermer</button>
    </header>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

    <p v-if="detail.links.length === 0" class="hint">
      Aucun lien de « {{ sourceName }} » vers « {{ targetName }} ».
    </p>
    <ul v-else>
      <li v-for="link in detail.links" :key="link.id">
        {{ sourceName }} {{ verbOf(link.relationship_type, link.access_type) }} {{ targetName }}
        <button v-if="canWrite" type="button" :disabled="busy" @click="remove(link.id)">
          {{ confirming === link.id ? 'Confirmer' : 'Supprimer' }}
        </button>
      </li>
    </ul>

    <form v-if="canWrite" @submit.prevent="submit">
      <label for="cell-relation">Nouveau lien</label>
      <select id="cell-relation" v-model="chosen">
        <option value="">Choisis une relation…</option>
        <option v-for="type in detail.allowed" :key="type" :value="type">
          {{ RELATIONSHIP_LABELS[type] }}
        </option>
      </select>
      <button type="submit" :disabled="busy || !chosen">Relier</button>
    </form>
  </aside>
</template>

<style scoped>
.cell-panel {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border, #c9c4d1);
  border-radius: 6px;
}
.cell-panel header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
```

- [ ] **Step 4: La section**

`frontend/src/features/matrix/MatrixSection.vue` :

```vue
<script setup lang="ts">
// The `/analyse/matrice` section: two axes, a grid, and the cell being worked on.
//
// The question lives in the URL (docs/adr/0010): `?rows=` and `?cols=` say what
// each axis is, `?cell=` which pair is open. Changing an axis is a step of an
// investigation and is pushed; opening a cell only replaces, or a dozen cells
// would bury the previous screen in the history.
//
// A reader sees the grid and the links; the write controls are hidden for them
// but it is the API that refuses (docs/adr/0032).
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import { axisToParam, parseAxis } from './axis'
import { cellToParam, parseCell } from './gridNavigation'
import MatrixCellPanel from './MatrixCellPanel.vue'
import MatrixGrid from './MatrixGrid.vue'
import { useMatrix, type RelationshipType } from './useMatrix'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const metamodel = useMetamodel()
const matrix = useMatrix()
const failure = ref('')
const busy = ref(false)

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const rowsParam = computed(() => one(route.query.rows))
const colsParam = computed(() => one(route.query.cols))
const opened = computed(() => parseCell(one(route.query.cell)))

const rowsValue = computed(() => axisToParam(parseAxis(rowsParam.value)))
const colsValue = computed(() => axisToParam(parseAxis(colsParam.value)))

const axes = computed(() => [
  { id: 'rows' as const, label: 'Lignes', value: rowsValue.value, param: rowsParam.value },
  { id: 'cols' as const, label: 'Colonnes', value: colsValue.value, param: colsParam.value },
])

onMounted(async () => {
  await metamodel.load()
})

watch(
  [rowsParam, colsParam],
  async () => {
    await matrix.load(parseAxis(rowsParam.value), parseAxis(colsParam.value))
  },
  { immediate: true },
)

watch(
  opened,
  async (cell) => {
    failure.value = ''
    if (!cell) {
      matrix.closeCell()
      return
    }
    await matrix.openCell(cell.source, cell.target)
  },
  { immediate: true },
)

const names = computed(() => {
  const found = new Map<string, string>()
  for (const element of [...(matrix.matrix.value?.rows ?? []), ...(matrix.matrix.value?.cols ?? [])]) {
    found.set(element.id, element.name)
  }
  return found
})

function nameOf(id: string): string {
  return names.value.get(id) ?? 'élément inconnu'
}

/** Count of what a cut axis shows, for the banner. */
const cut = computed(() => {
  const grid = matrix.matrix.value
  if (!grid?.truncated) {
    return ''
  }
  const parts: string[] = []
  if (grid.rows_total > grid.rows.length) {
    parts.push(`lignes : ${grid.rows.length} premiers sur ${grid.rows_total}`)
  }
  if (grid.cols_total > grid.cols.length) {
    parts.push(`colonnes : ${grid.cols.length} premières sur ${grid.cols_total}`)
  }
  return `${parts.join(' ; ')} — affinez le choix de l'axe.`
})

function chooseAxis(which: 'rows' | 'cols', value: string): void {
  const { cell: _closed, ...rest } = route.query
  void router.push({ query: { ...rest, [which]: value } })
}

function openCell(source: string, target: string): void {
  void router.replace({ query: { ...route.query, cell: cellToParam(source, target) } })
}

function closeCell(): void {
  const { cell: _closed, ...rest } = route.query
  void router.replace({ query: rest })
}

async function act(task: () => Promise<void>): Promise<void> {
  failure.value = ''
  busy.value = true
  try {
    await task()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

const connect = (type: RelationshipType) => act(() => matrix.connect(type))
const disconnect = (id: string) => act(() => matrix.disconnect(id))
</script>

<template>
  <section class="section">
    <header>
      <h2>Matrice de relations</h2>
      <p class="hint">Qui est relié à quoi : les sources en lignes, les cibles en colonnes.</p>
    </header>

    <form class="controls" aria-label="Axes de la matrice" @submit.prevent>
      <div v-for="axis in axes" :key="axis.id" class="field">
        <label :for="`matrix-${axis.id}`">{{ axis.label }}</label>
        <select
          :id="`matrix-${axis.id}`"
          :value="axis.value"
          @change="chooseAxis(axis.id, ($event.target as HTMLSelectElement).value)"
        >
          <option value="all">Tout le catalogue</option>
          <option v-if="axis.param.startsWith('ids:')" :value="axis.param">
            Une sélection d'éléments
          </option>
          <optgroup label="Une couche">
            <option v-for="layer in metamodel.layers.value" :key="layer" :value="`layer:${layer}`">
              {{ LAYER_LABELS[layer] }}
            </option>
          </optgroup>
          <optgroup v-for="group in metamodel.byLayer.value" :key="group.layer" :label="`Un type — ${LAYER_LABELS[group.layer]}`">
            <option v-for="type in group.types" :key="type.value" :value="`type:${type.value}`">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </div>
    </form>

    <p v-if="matrix.status.value === 'error'" class="banner banner--error" role="alert">
      {{ matrix.error.value }}
    </p>
    <p v-else-if="matrix.status.value === 'loading' && !matrix.matrix.value" class="hint">
      Chargement de la matrice…
    </p>

    <template v-if="matrix.matrix.value">
      <p v-if="cut" class="banner" role="status">{{ cut }}</p>
      <MatrixGrid
        :matrix="matrix.matrix.value"
        :selected="opened"
        @open="openCell"
        @close="closeCell"
      />
    </template>

    <MatrixCellPanel
      v-if="matrix.detail.value"
      :detail="matrix.detail.value"
      :source-name="nameOf(matrix.detail.value.source_id)"
      :target-name="nameOf(matrix.detail.value.target_id)"
      :can-write="canWrite"
      :busy="busy"
      :failure="failure || matrix.cellError.value"
      @connect="connect"
      @disconnect="disconnect"
      @close="closeCell"
    />
  </section>
</template>
```

- [ ] **Step 5: L'entrée de menu**

Dans `frontend/src/router/sections.ts`, après l'entrée `impact` :

```ts
  {
    path: '/analyse/matrice',
    name: 'matrix',
    label: 'Matrice',
    summary: 'Croiser deux ensembles d’éléments : qui est relié à quoi, et créer le lien.',
    group: 'analysis',
    view: () => import('../features/matrix/MatrixSection.vue'),
  },
```

- [ ] **Step 6: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/MatrixSection.spec.ts tests/router.spec.ts tests/AppNav.spec.ts`
Expected: tous passent.

- [ ] **Step 7: Typage et lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: aucune erreur.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/matrix frontend/src/router/sections.ts frontend/tests/MatrixSection.spec.ts
git commit -m "feat(matrix): add the matrix section with the cell panel" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Vérification du lot 1 et ADR de la matrice

**Files:**
- Create: `docs/adr/<N1>-matrice-de-relations.md` (N1 = prochain numéro libre)

**Interfaces:**
- Consumes: —
- Produces: l'ADR du lot 1.

- [ ] **Step 1: Calculer le numéro de l'ADR**

```bash
ls docs/adr | grep -E '^[0-9]{4}-' | tail -1
```
N1 = ce numéro + 1 (quatre chiffres, sans trou). Créer le fichier `docs/adr/<N1>-matrice-de-relations.md`.

- [ ] **Step 2: Écrire l'ADR**

Remplacer `<N1>` par le numéro dans le titre.

```markdown
---
titre: La matrice de relations, calculée à la demande
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/matrix.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/api/schemas.py, backend/src/ea/domain/ports.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/mcp/server.py, frontend/src/features/matrix, frontend/src/router/sections.ts, CLAUDE.md
---

# <N1>. La matrice de relations, calculée à la demande

Date : 2026-10-10
Statut : Proposition

## Contexte

Sparx EA offre une *Relationship Matrix* : des éléments en lignes, des éléments
en colonnes, et dans chaque case les liens qui les unissent — avec la
possibilité d'en créer un sans quitter la grille. Le catalogue sait déjà tout
ce qu'il faut : `relationships` porte les liens, `domain/archimate/rules.py`
dit ce qui est permis entre deux types (`adr/0005`, `adr/0012`). Ce qui manque
est une *lecture croisée*, et la question de savoir où elle écrit.

Trois tensions se sont présentées. D'abord, une grille 61 × 61 n'est ni
lisible ni transportable : il faut des axes bornés. Ensuite, la tentation d'un
chemin d'écriture propre à la matrice (« créer dans la case ») recréerait les
règles que `ArchitectureService.connect` applique déjà — existence des deux
extrémités, boucle de containment. Enfin, `permitted_relationships` répond pour
deux *types* et ignore que les deux extrémités peuvent être le même élément.

## Décision

1. **La matrice est une vue calculée.** Aucune table, aucune migration : un
   résultat stocké serait une seconde vérité que chaque modification périmerait.
   `POST /matrix` (un POST parce que deux axes ne tiennent pas dans une URL, pas
   parce qu'il écrit) et `GET /matrix/cell` lisent `elements` et `relationships`.
2. **Un lien vit dans une seule case** : ligne = source, colonne = cible. Son
   inverse est la case symétrique, si les deux éléments sont sur les axes. Pas de
   symétrisation : une case a un sens d'une phrase.
3. **Écriture exclusivement par `/relationships`.** Créer un lien depuis une case
   est `POST /relationships`, le supprimer `DELETE /relationships/{id}` ; le
   métamodèle et les gardes de `services/` restent les seuls juges.
4. **Les types permis ne sont jamais calculés dans le SPA.** `GET /matrix/cell`
   répond `allowed` ; `allowed_between(source, target)` filtre
   `permitted_relationships` avec `permits(..., same_element=...)`, ce qui exclut
   la composition de soi sur soi sans restater une règle.
5. **Les axes sont des sélections** : éléments nommés (`element_ids`), ou le
   catalogue restreint par type, couche, nom. Plafond `MAX_MATRIX_AXIS = 50`,
   déclaré dans le domaine et réimporté par `api/schemas.py` et `mcp/server.py`.
   Des `element_ids` au-delà du plafond sont refusés (422 `matrix_axis_too_large`) ;
   un axe par critères est coupé aux 50 premiers par nom, avec le total.
6. **La réponse est creuse** : seules les cases portant un lien. `association`
   étant permise entre tous les types, « les cases où quelque chose est permis »
   serait toute la grille ; `blocked` ne liste que les paires sans lien où rien
   n'est permis (vide aujourd'hui, traité si le métamodèle évolue).
7. **Un outil MCP en lecture seule**, `get_relationship_matrix`, qui prend les
   mêmes `AxisSelect` que l'API.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker la matrice (table `matrix_cells`) | lecture O(1) | seconde vérité, périmée à chaque lien | écartée |
| Écrire un lien par `PUT /matrix/cell` | un seul appel côté SPA | contourne `connect` : règles à restater | écartée |
| Matrice complète 61 × 61 côté serveur | simple | 3 721 cases que personne ne lit | écartée |
| Marquer comme interdites les cases sans type permis | fidèle à Sparx | aucune case n'est interdite en ArchiMate 3.2 (`association`) | remplacée par `blocked` |
| Axes par paquet / requête enregistrée | usage Sparx | dépend du sous-projet 1 | repoussée, ajoutée sans changer l'API |

## Conséquences

- La grille ne coûte rien à stocker et ne peut pas dériver ; elle coûte une
  requête `links_between` bornée par 50 × 50.
- Un axe coupé à 50 oblige à affiner ; la pagination d'axes est hors périmètre.
- Les liens sont ordonnés par `created_at` dans une case : deux appels donnent la
  même grille.
- Quand les paquets et requêtes enregistrées existeront, `AxisSelection` gagnera
  deux champs ; le contrat des axes actuel ne change pas.
- La suppression d'un lien depuis une case demande une confirmation côté SPA,
  pas côté API (`DELETE` existant).

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0009](0009-association-des-elements-dans-le-spa.md), [0012](0012-ecran-du-metamodele.md), [0014](0014-serveur-mcp-pour-les-agents.md), [0032](0032-authentification-par-keycloak.md), [0033](0033-postgresql-seul-pour-le-graphe.md)
- Code concerné : `backend/src/ea/domain/matrix.py`, `backend/src/ea/services/analysis.py`, `frontend/src/features/matrix/`
```

- [ ] **Step 3: Régénérer et vérifier**

Run: `make openapi && make check`
Expected: « Client généré à jour. » puis `make check` vert (lint, types, openapi-check, tests ≥ 90 %, Vitest, pipelines).

- [ ] **Step 4: Commit**

```bash
git add docs/adr/<N1>-matrice-de-relations.md
git commit -m "docs(adr): record the computed relationship matrix" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 2 — Les contrôles de qualité

### Task 12: `domain/quality.py` — types, paramètres et quatre règles simples

**Files:**
- Create: `backend/src/ea/domain/quality.py`
- Create: `backend/tests/unit/test_quality.py`
- Modify: `backend/src/ea/domain/model.py` (lignes 1-45 : constante publique de motif de clé)

**Interfaces:**
- Consumes: `Element`, `Relationship`, `ElementType`
- Produces:
```python
MAX_FINDINGS: Final = 1000
MAX_QUALITY_ELEMENTS: Final = 5000
DEFAULT_SIMILARITY: Final = 0.9
class Severity(StrEnum): ERROR="error"; WARNING="warning"; INFO="info"
class RuleCode(StrEnum): ORPHAN="orphan"; DUPLICATE_PROBABLE="duplicate_probable"; NAMING="naming"; MISSING_PROPERTY="missing_property"; NO_DESCRIPTION="no_description"; INCONSISTENT_RELATIONSHIP="inconsistent_relationship"
SEVERITY_OF: Mapping[RuleCode, Severity]
@dataclass(frozen=True, slots=True) class Finding: rule; severity; element_id: UUID; element_name: str; message: str; related_id: UUID | None = None; related_name: str | None = None
@dataclass(frozen=True, slots=True) class QualityParams: similarity: float = 0.9; required_properties: Mapping[ElementType, frozenset[str]] = {}; max_elements: int = 5000;  @classmethod from_settings(similarity: float, required: Mapping[str, Sequence[str]]) -> QualityParams
def check_orphans(elements: Sequence[Element], relationships: Sequence[Relationship]) -> list[Finding]
def check_descriptions(elements: Sequence[Element]) -> list[Finding]
def check_naming(elements: Sequence[Element]) -> list[Finding]
def check_required_properties(elements: Sequence[Element], required: Mapping[ElementType, frozenset[str]]) -> list[Finding]
PROPERTY_KEY_PATTERN: Final = r"^[A-Za-z][A-Za-z0-9_]{0,62}$"   # in domain/model.py
```

- [ ] **Step 1: Écrire les tests qui échouent**

`backend/tests/unit/test_quality.py` :

```python
"""The quality rules, one series per rule: positive, negative, edge."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.quality import (
    QualityParams,
    RuleCode,
    Severity,
    check_descriptions,
    check_naming,
    check_orphans,
    check_required_properties,
)
from tests.support_analysis import a_link, an_element


class TestOrphans:
    def test_an_element_with_no_link_is_flagged(self) -> None:
        loner = an_element(E.NODE, "Loner")

        findings = check_orphans([loner], [])

        assert [(f.rule, f.severity, f.element_id) for f in findings] == [
            (RuleCode.ORPHAN, Severity.WARNING, loner.id)
        ]

    def test_a_linked_element_is_not_flagged_at_either_end(self) -> None:
        api = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        assert check_orphans([api, process], [a_link(R.SERVING, api, process)]) == []

    @pytest.mark.parametrize("exempt", [E.GROUPING, E.LOCATION, E.PLATEAU])
    def test_containers_are_not_expected_to_be_linked(self, exempt: E) -> None:
        assert check_orphans([an_element(exempt, "Paris")], []) == []

    def test_being_alone_of_its_type_changes_nothing(self) -> None:
        only_one = an_element(E.DATA_OBJECT, "Invoice")

        assert len(check_orphans([only_one], [])) == 1


class TestDescriptions:
    def test_an_empty_description_is_flagged_as_information(self) -> None:
        bare = an_element(E.NODE, "srv-01")

        findings = check_descriptions([bare, an_element(E.NODE, "srv-02", description="Linux")])

        assert [(f.element_id, f.rule, f.severity) for f in findings] == [
            (bare.id, RuleCode.NO_DESCRIPTION, Severity.INFO)
        ]


class TestNaming:
    def test_a_clean_name_passes(self) -> None:
        assert check_naming([an_element(E.APPLICATION_COMPONENT, "Facturation client")]) == []

    def test_a_blank_name_is_flagged(self) -> None:
        blank = replace(an_element(E.NODE, "x"), name="   ")

        assert [f.rule for f in check_naming([blank])] == [RuleCode.NAMING]

    def test_a_double_space_is_flagged(self) -> None:
        assert len(check_naming([an_element(E.NODE, "srv  01")])) == 1

    def test_a_shouting_name_is_flagged_but_a_short_acronym_is_not(self) -> None:
        assert len(check_naming([an_element(E.APPLICATION_COMPONENT, "FACTURATION")])) == 1
        assert check_naming([an_element(E.APPLICATION_COMPONENT, "CRM")]) == []

    def test_a_name_repeating_the_type_is_flagged(self) -> None:
        assert len(check_naming([an_element(E.APPLICATION_COMPONENT, "Application Component")])) == 1

    def test_several_problems_make_one_finding(self) -> None:
        findings = check_naming([an_element(E.NODE, "NODE  ONE")])

        assert len(findings) == 1
        assert findings[0].severity is Severity.INFO


class TestRequiredProperties:
    REQUIRED = {E.APPLICATION_COMPONENT: frozenset({"owner"})}

    def test_a_missing_key_is_flagged_as_a_warning(self) -> None:
        app = an_element(E.APPLICATION_COMPONENT, "Billing")

        findings = check_required_properties([app], self.REQUIRED)

        assert [(f.rule, f.severity) for f in findings] == [
            (RuleCode.MISSING_PROPERTY, Severity.WARNING)
        ]
        assert "owner" in findings[0].message

    def test_an_empty_value_counts_as_missing(self) -> None:
        app = an_element(E.APPLICATION_COMPONENT, "Billing", properties={"owner": "  "})

        assert len(check_required_properties([app], self.REQUIRED)) == 1

    def test_a_filled_key_and_another_type_pass(self) -> None:
        filled = an_element(E.APPLICATION_COMPONENT, "Billing", properties={"owner": "finance"})
        other = an_element(E.NODE, "srv-01")

        assert check_required_properties([filled, other], self.REQUIRED) == []


class TestTheParameters:
    def test_they_are_read_from_settings_shaped_values(self) -> None:
        params = QualityParams.from_settings(0.8, {"application_component": ["owner", "tier"]})

        assert params.similarity == 0.8
        assert params.required_properties == {E.APPLICATION_COMPONENT: frozenset({"owner", "tier"})}

    def test_an_unknown_element_type_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="bogus"):
            QualityParams.from_settings(0.9, {"bogus": ["owner"]})

    def test_a_property_key_that_is_not_an_identifier_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1bad"):
            QualityParams.from_settings(0.9, {"node": ["1bad"]})

    def test_a_similarity_outside_zero_to_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="similarity"):
            QualityParams.from_settings(1.5, {})
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.quality'`.

- [ ] **Step 3: Rendre le motif de clé public**

Dans `backend/src/ea/domain/model.py`, ajouter `from typing import Final` aux imports et remplacer

```python
_PROPERTY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
```
par
```python
PROPERTY_KEY_PATTERN: Final = r"^[A-Za-z][A-Za-z0-9_]{0,62}$"
_PROPERTY_KEY = re.compile(PROPERTY_KEY_PATTERN)
```

- [ ] **Step 4: Implémenter**

`backend/src/ea/domain/quality.py` :

```python
"""Quality rules over the model: orphans, duplicates, naming, missing data.

Pure functions of the elements and links they are given, each identified by a
stable `RuleCode` — a client, a pipeline and the SPA all branch on the code, so
a code is never renamed (docs/adr/<ADR lot 2>). Nothing is stored: a stored
result would be a second truth that every edit of an element made stale.

A rule is a judgement about *this* model, not a fact of ArchiMate: what the
metamodel forbids is refused at write time, what it merely makes suspicious is
reported here. Messages name elements in French, since a person reads them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.archimate import ElementType
from ea.domain.model import PROPERTY_KEY_PATTERN, Element, Relationship

#: Declared here and imported by `api/schemas.py` (a service cannot import the API).
MAX_FINDINGS: Final = 1000
#: How many elements one run takes; beyond it the scope is "too large" (422).
MAX_QUALITY_ELEMENTS: Final = 5000
DEFAULT_SIMILARITY: Final = 0.9
#: Fewer letters than this is an acronym ("CRM", "SAP"), not shouting.
_MIN_SHOUTING_LETTERS: Final = 4
_KEY = re.compile(PROPERTY_KEY_PATTERN)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RuleCode(StrEnum):
    ORPHAN = "orphan"
    DUPLICATE_PROBABLE = "duplicate_probable"
    NAMING = "naming"
    MISSING_PROPERTY = "missing_property"
    NO_DESCRIPTION = "no_description"
    INCONSISTENT_RELATIONSHIP = "inconsistent_relationship"


SEVERITY_OF: Final[Mapping[RuleCode, Severity]] = {
    RuleCode.ORPHAN: Severity.WARNING,
    RuleCode.DUPLICATE_PROBABLE: Severity.WARNING,
    RuleCode.NAMING: Severity.INFO,
    RuleCode.MISSING_PROPERTY: Severity.WARNING,
    RuleCode.NO_DESCRIPTION: Severity.INFO,
    RuleCode.INCONSISTENT_RELATIONSHIP: Severity.ERROR,
}

#: Containers are not expected to be linked to anything.
_EXEMPT_FROM_ORPHAN: Final = frozenset({ElementType.GROUPING, ElementType.LOCATION, ElementType.PLATEAU})


@dataclass(frozen=True, slots=True)
class Finding:
    rule: RuleCode
    severity: Severity
    element_id: UUID
    element_name: str
    message: str
    related_id: UUID | None = None
    related_name: str | None = None


@dataclass(frozen=True, slots=True)
class QualityParams:
    """The two knobs: how alike two names must be, and which properties a type must carry."""

    similarity: float = DEFAULT_SIMILARITY
    required_properties: Mapping[ElementType, frozenset[str]] = field(default_factory=dict)
    max_elements: int = MAX_QUALITY_ELEMENTS

    @classmethod
    def from_settings(
        cls, similarity: float, required: Mapping[str, Sequence[str]]
    ) -> QualityParams:
        """Validate the environment's values; raises `ValueError` so boot fails loudly."""
        if not 0.0 <= similarity <= 1.0:
            msg = f"EA_QUALITY_SIMILARITY: similarity must be between 0 and 1, not {similarity}"
            raise ValueError(msg)
        parsed: dict[ElementType, frozenset[str]] = {}
        for type_name, keys in required.items():
            try:
                element_type = ElementType(type_name)
            except ValueError:
                msg = f"EA_QUALITY_REQUIRED names an unknown element type {type_name!r}"
                raise ValueError(msg) from None
            for key in keys:
                if not _KEY.fullmatch(key):
                    msg = f"EA_QUALITY_REQUIRED: {key!r} is not a plain property name"
                    raise ValueError(msg)
            parsed[element_type] = frozenset(keys)
        return cls(similarity=similarity, required_properties=parsed)


def _finding(
    rule: RuleCode, element: Element, message: str, related: Element | None = None
) -> Finding:
    return Finding(
        rule=rule,
        severity=SEVERITY_OF[rule],
        element_id=element.id,
        element_name=element.name,
        message=message,
        related_id=related.id if related else None,
        related_name=related.name if related else None,
    )


def _normalise(text: str) -> str:
    """Case, accents and punctuation folded away: `Café-API` and `cafe api` agree."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", bare.casefold()).split())


def check_orphans(elements: Sequence[Element], relationships: Sequence[Relationship]) -> list[Finding]:
    """Elements with no link at all, in or out. `relationships` must touch the scope."""
    linked = {end for link in relationships for end in (link.source_id, link.target_id)}
    return [
        _finding(RuleCode.ORPHAN, element, f"« {element.name} » n'est relié à aucun élément.")
        for element in elements
        if element.element_type not in _EXEMPT_FROM_ORPHAN and element.id not in linked
    ]


def check_descriptions(elements: Sequence[Element]) -> list[Finding]:
    return [
        _finding(RuleCode.NO_DESCRIPTION, element, f"« {element.name} » n'a pas de description.")
        for element in elements
        if not element.description.strip()
    ]


def check_naming(elements: Sequence[Element]) -> list[Finding]:
    """One finding per element, listing every problem its name has."""
    findings: list[Finding] = []
    for element in elements:
        name = element.name
        problems: list[str] = []
        if not name.strip():
            problems.append("le nom est vide")
        if "  " in name:
            problems.append("le nom contient des espaces doubles")
        letters = [c for c in name if c.isalpha()]
        if len(letters) >= _MIN_SHOUTING_LETTERS and name == name.upper():
            problems.append("le nom est tout en capitales")
        type_names = {_normalise(element.element_type.label), _normalise(element.element_type.value)}
        if name.strip() and _normalise(name) in type_names:
            problems.append("le nom répète le type de l'élément")
        if problems:
            findings.append(
                _finding(RuleCode.NAMING, element, f"« {name.strip()} » : {' ; '.join(problems)}.")
            )
    return findings


def check_required_properties(
    elements: Sequence[Element], required: Mapping[ElementType, frozenset[str]]
) -> list[Finding]:
    findings: list[Finding] = []
    for element in elements:
        for key in sorted(required.get(element.element_type, ())):
            if not element.properties.get(key, "").strip():
                findings.append(
                    _finding(
                        RuleCode.MISSING_PROPERTY,
                        element,
                        f"« {element.name} » n'a pas de valeur pour « {key} ».",
                    )
                )
    return findings
```

- [ ] **Step 5: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_quality.py tests/unit/archimate -x`
Expected: tous passent.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/quality.py backend/src/ea/domain/model.py backend/tests/unit/test_quality.py
git commit -m "feat(quality): add the quality types and the four simple rules" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Règle `duplicate_probable`

**Files:**
- Modify: `backend/src/ea/domain/quality.py` (ajouter l'import `from difflib import SequenceMatcher` et la fonction à la fin)
- Modify: `backend/tests/unit/test_quality.py` (ajouter `check_duplicates` à l'import et une classe)

**Interfaces:**
- Consumes: `_normalise`, `_finding`
- Produces: `def check_duplicates(elements: Sequence[Element], similarity: float) -> list[Finding]` — au plus un constat par élément, porté par le plus tardif (par nom puis id) des deux, `related_id` = le premier.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter `check_duplicates` à l'import de `ea.domain.quality` dans `test_quality.py`, puis :

```python
class TestDuplicates:
    def pair(self, one: str, other: str, *, type_: E = E.APPLICATION_COMPONENT):  # type: ignore[no-untyped-def]
        return an_element(type_, one), an_element(type_, other)

    def flagged(self, a, b, similarity: float = 0.9):  # type: ignore[no-untyped-def]
        findings = check_duplicates([a, b], similarity)
        assert len(findings) == 1
        assert {findings[0].element_id, findings[0].related_id} == {a.id, b.id}
        assert findings[0].rule is RuleCode.DUPLICATE_PROBABLE
        assert findings[0].severity is Severity.WARNING

    def test_names_equal_after_case_are_flagged(self) -> None:
        self.flagged(*self.pair("Facturation", "facturation"))

    def test_names_equal_after_accents_are_flagged(self) -> None:
        self.flagged(*self.pair("Café", "Cafe"))

    def test_names_equal_after_punctuation_are_flagged(self) -> None:
        self.flagged(*self.pair("Billing API", "Billing-API"))

    def test_very_similar_names_are_flagged_over_the_threshold(self) -> None:
        self.flagged(*self.pair("Facturation client", "Facturation clients"))

    def test_the_threshold_is_a_parameter(self) -> None:
        a, b = self.pair("Facturation client", "Facturation clients")

        assert check_duplicates([a, b], 0.999) == []

    def test_different_names_are_not_flagged(self) -> None:
        a, b = self.pair("Billing", "Backup")

        assert check_duplicates([a, b], 0.9) == []

    def test_the_same_name_under_two_types_is_not_a_duplicate(self) -> None:
        a = an_element(E.APPLICATION_COMPONENT, "Billing")
        b = an_element(E.BUSINESS_PROCESS, "Billing")

        assert check_duplicates([a, b], 0.9) == []

    def test_an_element_alone_of_its_type_has_no_duplicate(self) -> None:
        assert check_duplicates([an_element(E.NODE, "srv-01")], 0.9) == []

    def test_three_copies_make_two_findings_and_never_flag_the_first(self) -> None:
        copies = [an_element(E.NODE, name) for name in ("srv-01", "SRV-01", "srv 01")]

        findings = check_duplicates(copies, 0.9)

        assert len(findings) == 2
        first = min(copies, key=lambda e: (e.name, str(e.id)))
        assert first.id not in {f.element_id for f in findings}
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x -k Duplicates`
Expected: FAIL — `ImportError: cannot import name 'check_duplicates'`.

- [ ] **Step 3: Implémenter**

Ajouter `from difflib import SequenceMatcher` et, à la fin de `quality.py` :

```python
def _alike(one: str, other: str, similarity: float) -> bool:
    if one == other:
        return True
    matcher = SequenceMatcher(None, one, other)
    # The two upper bounds are cheap and rule most pairs out before `ratio()`.
    return (
        matcher.real_quick_ratio() >= similarity
        and matcher.quick_ratio() >= similarity
        and matcher.ratio() >= similarity
    )


def check_duplicates(elements: Sequence[Element], similarity: float) -> list[Finding]:
    """Same type, names equal once normalised or at least `similarity` alike.

    Compared per (type, first normalised letter), so a thousand elements are
    not a million pairs. A typo in the *first* letter slips through — accepted:
    this is a warning, never an error, and the threshold is a setting.
    """
    # ponytail: pairwise within a block, bounded by MAX_QUALITY_ELEMENTS;
    # a MinHash/n-gram index if a block ever holds thousands of near names.
    blocks: dict[tuple[ElementType, str], list[tuple[str, Element]]] = {}
    for element in sorted(elements, key=lambda e: (e.name, str(e.id))):
        normalised = _normalise(element.name)
        if normalised:
            blocks.setdefault((element.element_type, normalised[0]), []).append(
                (normalised, element)
            )
    findings: list[Finding] = []
    for block in blocks.values():
        for index, (normalised, later) in enumerate(block):
            for earlier_normalised, earlier in block[:index]:
                if _alike(normalised, earlier_normalised, similarity):
                    findings.append(
                        _finding(
                            RuleCode.DUPLICATE_PROBABLE,
                            later,
                            f"« {later.name} » ressemble à « {earlier.name} ».",
                            earlier,
                        )
                    )
                    break
    return findings
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/quality.py backend/tests/unit/test_quality.py
git commit -m "feat(quality): flag probable duplicate elements" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Règle `inconsistent_relationship`, `run_checks`, rapport et page

**Files:**
- Modify: `backend/src/ea/domain/quality.py` (imports `Counter`, `RelationshipType`, `permits` ; fonctions à la fin)
- Modify: `backend/tests/unit/test_quality.py`

**Interfaces:**
- Consumes: règles des Tasks 12-13
- Produces:
```python
def check_relationships(elements: Sequence[Element], relationships: Sequence[Relationship]) -> list[Finding]
@dataclass(frozen=True, slots=True) class RuleCount: rule: RuleCode; severity: Severity; count: int
@dataclass(frozen=True, slots=True) class QualityReport: findings: tuple[Finding, ...];  @property counts -> tuple[RuleCount, ...]  # one per RuleCode, zeros included
def run_checks(elements: Sequence[Element], relationships: Sequence[Relationship], params: QualityParams) -> QualityReport
@dataclass(frozen=True, slots=True) class QualityPage: report: QualityReport; findings: tuple[Finding, ...]; next_cursor: int | None
def page_of(report: QualityReport, *, severity: Severity | None = None, code: RuleCode | None = None, limit: int = 100, cursor: int = 0) -> QualityPage
```

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à l'import de `test_quality.py` : `QualityReport, check_relationships, page_of, run_checks` ; et `from tests.support_analysis import a_link, a_stored_link, an_element`. Puis :

```python
class TestInconsistentRelationships:
    def test_a_legal_link_is_not_flagged(self) -> None:
        api = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        assert check_relationships([api, process], [a_link(R.SERVING, api, process)]) == []

    def test_a_link_the_metamodel_would_refuse_today_is_an_error(self) -> None:
        data = an_element(E.DATA_OBJECT, "Invoice")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        findings = check_relationships([data, process], [a_stored_link(R.SERVING, data, process)])

        assert [(f.rule, f.severity, f.element_id, f.related_id) for f in findings] == [
            (RuleCode.INCONSISTENT_RELATIONSHIP, Severity.ERROR, data.id, process.id)
        ]

    def test_a_containment_cycle_is_one_error(self) -> None:
        a = an_element(E.APPLICATION_COMPONENT, "A")
        b = an_element(E.APPLICATION_COMPONENT, "B")

        findings = check_relationships(
            [a, b], [a_link(R.COMPOSITION, a, b), a_link(R.COMPOSITION, b, a)]
        )

        assert len(findings) == 1
        assert "boucle" in findings[0].message

    def test_a_self_composition_is_reported_once_not_twice(self) -> None:
        a = an_element(E.APPLICATION_COMPONENT, "A")

        findings = check_relationships([a], [a_stored_link(R.COMPOSITION, a, a)])

        assert len(findings) == 1

    def test_a_chain_is_not_a_cycle(self) -> None:
        a, b, c = (an_element(E.APPLICATION_COMPONENT, name) for name in "ABC")

        links = [a_link(R.COMPOSITION, a, b), a_link(R.COMPOSITION, b, c)]

        assert check_relationships([a, b, c], links) == []

    def test_an_end_outside_the_scope_is_named_by_its_type(self) -> None:
        data = an_element(E.DATA_OBJECT, "Invoice")
        outside = an_element(E.BUSINESS_PROCESS, "Order to cash")

        findings = check_relationships([data], [a_stored_link(R.SERVING, data, outside)])

        assert findings[0].related_name == "business_process"


class TestRunningThemAll:
    def model(self):  # type: ignore[no-untyped-def]
        app = an_element(E.APPLICATION_COMPONENT, "Billing")
        data = an_element(E.DATA_OBJECT, "Invoice", description="Facture")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash", description="Flux")
        loner = an_element(E.NODE, "Loner", description="seul")
        links = [a_stored_link(R.SERVING, data, process)]
        return [app, data, process, loner], links, (app, data, process, loner)

    def test_errors_come_before_warnings_before_information(self) -> None:
        elements, links, _ = self.model()

        report = run_checks(elements, links, QualityParams())

        order = [f.severity for f in report.findings]
        assert order == sorted(order, key=["error", "warning", "info"].index)
        assert order[0] is Severity.ERROR

    def test_within_a_severity_findings_are_sorted_by_element_name(self) -> None:
        elements, links, _ = self.model()

        report = run_checks(elements, links, QualityParams())

        warnings = [f.element_name for f in report.findings if f.severity is Severity.WARNING]
        assert warnings == sorted(warnings, key=str.casefold)

    def test_the_counts_list_every_rule_zeros_included(self) -> None:
        elements, links, _ = self.model()

        report = run_checks(elements, links, QualityParams())

        counts = {c.rule: c.count for c in report.counts}
        assert set(counts) == set(RuleCode)
        assert counts[RuleCode.INCONSISTENT_RELATIONSHIP] == 1
        assert counts[RuleCode.DUPLICATE_PROBABLE] == 0

    def test_it_is_deterministic(self) -> None:
        elements, links, _ = self.model()

        assert run_checks(elements, links, QualityParams()) == run_checks(
            list(reversed(elements)), links, QualityParams()
        )

    def test_an_empty_model_has_no_finding(self) -> None:
        assert run_checks([], [], QualityParams()) == QualityReport(findings=())

    def test_required_properties_and_threshold_come_from_the_parameters(self) -> None:
        a = an_element(E.APPLICATION_COMPONENT, "Facturation client", description="d")
        b = an_element(E.APPLICATION_COMPONENT, "Facturation clients", description="d")
        link = a_link(R.FLOW, a, b)
        strict = QualityParams(similarity=0.99, required_properties={E.APPLICATION_COMPONENT: frozenset({"owner"})})

        report = run_checks([a, b], [link], strict)

        rules = {f.rule for f in report.findings}
        assert RuleCode.MISSING_PROPERTY in rules
        assert RuleCode.DUPLICATE_PROBABLE not in rules


class TestPaging:
    def report(self) -> QualityReport:
        elements = [an_element(E.NODE, f"n{index}") for index in range(5)]
        return run_checks(elements, [], QualityParams())  # 5 orphans + 5 missing descriptions

    def test_a_page_has_a_cursor_to_the_next_one(self) -> None:
        report = self.report()

        first = page_of(report, limit=4)
        second = page_of(report, limit=4, cursor=first.next_cursor or 0)
        last = page_of(report, limit=4, cursor=second.next_cursor or 0)

        assert (len(first.findings), len(second.findings), len(last.findings)) == (4, 4, 2)
        assert first.next_cursor == 4 and last.next_cursor is None

    def test_filters_narrow_the_findings_but_not_the_counts(self) -> None:
        report = self.report()

        page = page_of(report, severity=Severity.INFO)

        assert {f.rule for f in page.findings} == {RuleCode.NO_DESCRIPTION}
        assert page.report.counts == report.counts

    def test_a_code_filter(self) -> None:
        page = page_of(self.report(), code=RuleCode.ORPHAN)

        assert {f.rule for f in page.findings} == {RuleCode.ORPHAN}

    def test_a_cursor_past_the_end_is_an_empty_last_page(self) -> None:
        page = page_of(self.report(), cursor=999)

        assert (page.findings, page.next_cursor) == ((), None)
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x -k "Inconsistent or RunningThemAll or Paging"`
Expected: FAIL — `ImportError: cannot import name 'check_relationships'`.

- [ ] **Step 3: Implémenter**

Compléter les imports de `quality.py` : `from collections import Counter` et `from ea.domain.archimate import ElementType, RelationshipType, permits`. Ajouter à la fin :

```python
#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT: Final = frozenset({RelationshipType.COMPOSITION, RelationshipType.AGGREGATION})


def _containment_loops(relationships: Sequence[Relationship]) -> list[Relationship]:
    """The containment links that close a loop: back edges of a depth-first walk."""
    edges: dict[UUID, list[Relationship]] = {}
    for link in relationships:
        if link.relationship_type in _CONTAINMENT:
            edges.setdefault(link.source_id, []).append(link)
    on_path, done = 1, 2
    state: dict[UUID, int] = {}
    closing: list[Relationship] = []
    for start in sorted(edges, key=str):
        if start in state:
            continue
        state[start] = on_path
        stack = [(start, iter(edges.get(start, ())))]
        while stack:
            node, pending = stack[-1]
            link = next(pending, None)
            if link is None:
                state[node] = done
                stack.pop()
                continue
            seen = state.get(link.target_id)
            if seen == on_path:
                closing.append(link)
            elif seen is None:
                state[link.target_id] = on_path
                stack.append((link.target_id, iter(edges.get(link.target_id, ()))))
    return closing


def check_relationships(
    elements: Sequence[Element], relationships: Sequence[Relationship]
) -> list[Finding]:
    """Links the metamodel would refuse today, and containment loops.

    The metamodel may have evolved since a link was stored, or an import may
    have let it through: either way the link exists, so it is reported — never
    deleted. A link whose end is outside `elements` is still judged, from the
    types it stores, and its far end is named by its type.
    """
    by_id = {element.id: element for element in elements}
    findings: list[Finding] = []
    flagged: set[UUID] = set()

    def name_of(end: UUID, stored_type: ElementType) -> str:
        known = by_id.get(end)
        return known.name if known else stored_type.value

    for link in relationships:
        if permits(
            link.relationship_type,
            link.source_type,
            link.target_type,
            same_element=link.source_id == link.target_id,
        ):
            continue
        flagged.add(link.id)
        source_name = name_of(link.source_id, link.source_type)
        target_name = name_of(link.target_id, link.target_type)
        findings.append(
            Finding(
                rule=RuleCode.INCONSISTENT_RELATIONSHIP,
                severity=Severity.ERROR,
                element_id=link.source_id,
                element_name=source_name,
                message=(
                    f"« {source_name} » {link.relationship_type.value} « {target_name} » : "
                    "le métamodèle ne permet plus ce lien."
                ),
                related_id=link.target_id,
                related_name=target_name,
            )
        )
    for link in _containment_loops(relationships):
        if link.id in flagged:
            continue
        source_name = name_of(link.source_id, link.source_type)
        target_name = name_of(link.target_id, link.target_type)
        findings.append(
            Finding(
                rule=RuleCode.INCONSISTENT_RELATIONSHIP,
                severity=Severity.ERROR,
                element_id=link.source_id,
                element_name=source_name,
                message=(
                    f"« {source_name} » {link.relationship_type.value} « {target_name} » "
                    "boucle : la hiérarchie de contenu se referme sur elle-même."
                ),
                related_id=link.target_id,
                related_name=target_name,
            )
        )
    return findings


@dataclass(frozen=True, slots=True)
class RuleCount:
    rule: RuleCode
    severity: Severity
    count: int


@dataclass(frozen=True, slots=True)
class QualityReport:
    findings: tuple[Finding, ...]

    @property
    def counts(self) -> tuple[RuleCount, ...]:
        """One entry per rule, zeros included, so a screen can show what is clean."""
        tally = Counter(finding.rule for finding in self.findings)
        return tuple(RuleCount(rule, SEVERITY_OF[rule], tally.get(rule, 0)) for rule in RuleCode)


_SEVERITY_ORDER: Final = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def run_checks(
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
    params: QualityParams,
) -> QualityReport:
    """Every rule over the scope. `relationships` must be the links touching it."""
    found = [
        *check_orphans(elements, relationships),
        *check_duplicates(elements, params.similarity),
        *check_naming(elements),
        *check_required_properties(elements, params.required_properties),
        *check_descriptions(elements),
        *check_relationships(elements, relationships),
    ]
    found.sort(
        key=lambda f: (
            _SEVERITY_ORDER[f.severity],
            f.element_name.casefold(),
            f.rule.value,
            f.message,
        )
    )
    return QualityReport(findings=tuple(found))


@dataclass(frozen=True, slots=True)
class QualityPage:
    #: The whole report, so counts describe the scope and not the filter.
    report: QualityReport
    findings: tuple[Finding, ...]
    next_cursor: int | None


def page_of(
    report: QualityReport,
    *,
    severity: Severity | None = None,
    code: RuleCode | None = None,
    limit: int = 100,
    cursor: int = 0,
) -> QualityPage:
    kept = [
        finding
        for finding in report.findings
        if (severity is None or finding.severity is severity)
        and (code is None or finding.rule is code)
    ]
    window = tuple(kept[cursor : cursor + limit])
    end = cursor + limit
    return QualityPage(report, window, end if end < len(kept) else None)
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x && uv run mypy src`
Expected: tous passent ; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/quality.py backend/tests/unit/test_quality.py
git commit -m "feat(quality): flag inconsistent links and run every rule" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Paramètres de configuration et `AnalysisService.check`

**Files:**
- Modify: `backend/src/ea/core/config.py` (import `Field` ; deux champs après `s3_ca_cert`, vers la ligne 205)
- Modify: `backend/src/ea/domain/errors.py` (`ScopeTooLargeError`)
- Modify: `backend/src/ea/services/analysis.py`
- Modify: `backend/src/ea/main.py` (validation au démarrage et passage de `params`)
- Modify: `backend/.env.example`
- Modify: `backend/tests/unit/test_config.py`, `backend/tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `QualityParams`, `run_checks`, `page_of`, `ArchitectureService.list_elements`, `AnalysisRepository.links_touching`
- Produces:
```python
class ScopeTooLargeError(DomainError)
Settings.quality_similarity: float = 0.9   # EA_QUALITY_SIMILARITY, 0..1
Settings.quality_required: dict[str, list[str]] = {}   # EA_QUALITY_REQUIRED, JSON
AnalysisService.__init__(self, architecture, repository, *, params: QualityParams | None = None)
async def check(self, *, severity: Severity | None = None, code: RuleCode | None = None, limit: int = 100, cursor: int = 0) -> QualityPage
```

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `backend/tests/unit/test_config.py`, ajouter (imports `pytest` déjà là) :

```python
def test_quality_defaults_are_a_ninety_percent_threshold_and_nothing_required() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.quality_similarity == 0.9
    assert settings.quality_required == {}


def test_quality_required_is_read_as_json_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EA_QUALITY_REQUIRED", '{"application_component": ["owner"]}')

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.quality_required == {"application_component": ["owner"]}


def test_a_similarity_outside_zero_to_one_does_not_build() -> None:
    with pytest.raises(ValueError, match="quality_similarity"):
        Settings(_env_file=None, quality_similarity=1.5)  # type: ignore[call-arg]
```

Dans `backend/tests/unit/test_analysis_service.py`, ajouter les imports `ScopeTooLargeError`, `RuleCode`, `Severity`, `QualityParams` et :

```python
@pytest.mark.asyncio
class TestTheQualityRun:
    async def test_a_lone_bare_element_is_an_orphan_without_description(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="Loner")

        page = await analysis.check()

        assert {f.rule for f in page.findings} == {RuleCode.ORPHAN, RuleCode.NO_DESCRIPTION}
        assert {c.rule: c.count for c in page.report.counts}[RuleCode.ORPHAN] == 1

    async def test_an_empty_model_is_clean(self, analysis: AnalysisService) -> None:
        page = await analysis.check()

        assert page.findings == ()
        assert all(count.count == 0 for count in page.report.counts)

    async def test_links_count_against_the_orphan_rule(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        page = await analysis.check(code=RuleCode.ORPHAN)

        assert page.findings == ()

    async def test_filters_and_paging_are_applied(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        for index in range(3):
            await service.create_element(element_type=E.NODE, name=f"n{index}")

        page = await analysis.check(severity=Severity.INFO, limit=2)

        assert len(page.findings) == 2
        assert page.next_cursor == 2

    async def test_a_scope_over_the_ceiling_is_refused_not_truncated(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = AnalysisService(service, repository, params=QualityParams(max_elements=2))
        for index in range(3):
            await service.create_element(element_type=E.NODE, name=f"n{index}")

        with pytest.raises(ScopeTooLargeError, match="2"):
            await small.check()

    async def test_a_reader_may_run_it(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="Loner")

        with acting_as(a_reader()):
            page = await analysis.check()

        assert page.findings

    async def test_nobody_runs_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.check()

    async def test_a_run_leaves_a_trace_with_its_duration(
        self, analysis: AnalysisService, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("INFO", logger="ea.services.analysis"):
            await analysis.check()

        record = next(r for r in caplog.records if getattr(r, "action", "") == "quality_run")
        assert record.findings == 0  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]
```

Ajouter `from tests.conftest import InMemoryRepository, a_reader` à l'import existant de `tests.conftest`.

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_config.py tests/unit/test_analysis_service.py -x`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'quality_similarity'`.

- [ ] **Step 3: Implémenter**

`backend/src/ea/domain/errors.py`, à la fin :

```python
class ScopeTooLargeError(DomainError):
    """A calculation was asked of more elements than one run takes."""
```

`backend/src/ea/core/config.py` : importer `Field` (`from pydantic import Field, SecretStr, ValidationInfo, ...`) et, après `s3_ca_cert` :

```python
    # --- Model quality checks — see docs/adr/<ADR lot 2> ---
    #: How alike two names of one type must be to be reported as probable
    #: duplicates (0 = anything, 1 = identical once normalised).
    quality_similarity: float = Field(default=0.9, ge=0.0, le=1.0)
    #: Properties a type must carry, as JSON: {"application_component": ["owner"]}.
    #: Which types and keys are valid is checked at boot by `QualityParams`.
    quality_required: dict[str, list[str]] = {}
```

`backend/.env.example`, à la fin :

```
# Contrôles de qualité du modèle (docs/adr, lot 2)
EA_QUALITY_SIMILARITY=0.9
# EA_QUALITY_REQUIRED={"application_component": ["owner"]}
```

`backend/src/ea/services/analysis.py` : ajouter aux imports

```python
import logging
from time import perf_counter

from ea.domain.errors import ElementNotFoundError, MatrixAxisTooLargeError, ScopeTooLargeError
from ea.domain.quality import (
    QualityPage,
    QualityParams,
    RuleCode,
    Severity,
    page_of,
    run_checks,
)

logger = logging.getLogger(__name__)
```

modifier `__init__` et ajouter `check` après `cell` :

```python
    def __init__(
        self,
        architecture: ArchitectureService,
        repository: AnalysisRepository,
        *,
        params: QualityParams | None = None,
    ) -> None:
        self._architecture = architecture
        self._repository = repository
        self._params = params or QualityParams()
```

```python
    # --- Quality checks -----------------------------------------------------

    async def check(
        self,
        *,
        severity: Severity | None = None,
        code: RuleCode | None = None,
        limit: int = 100,
        cursor: int = 0,
    ) -> QualityPage:
        """Run every rule over the catalogue and return one page of findings.

        The counts describe the whole run; `severity` and `code` narrow only the
        findings listed. A catalogue over the ceiling is refused rather than
        silently checked in part: a partial report reads as a clean one.
        """
        require_caller()
        started = perf_counter()
        ceiling = self._params.max_elements
        elements = await self._architecture.list_elements(ElementFilter(limit=ceiling + 1))
        if len(elements) > ceiling:
            msg = f"the catalogue holds more than {ceiling} elements; one run takes at most {ceiling}"
            raise ScopeTooLargeError(msg)
        links = await self._repository.links_touching([element.id for element in elements])
        report = run_checks(elements, links, self._params)
        logger.info(
            "quality run: %d findings over %d elements",
            len(report.findings),
            len(elements),
            extra={
                "action": "quality_run",
                "duration_ms": round((perf_counter() - started) * 1000, 1),
                "findings": len(report.findings),
            },
        )
        return page_of(report, severity=severity, code=code, limit=limit, cursor=cursor)
```

`backend/src/ea/main.py` : importer `from ea.domain.quality import QualityParams`, définir près de `build_embedder` :

```python
def build_quality_params(settings: Settings) -> QualityParams:
    """The quality knobs, validated: a bad `EA_QUALITY_REQUIRED` stops the boot."""
    return QualityParams.from_settings(settings.quality_similarity, settings.quality_required)
```

Dans le lifespan, remplacer la construction de `AnalysisService` par :

```python
                    app.state.analysis_service = AnalysisService(
                        app.state.architecture_service,
                        repository,
                        params=build_quality_params(settings),
                    )
```
et dans `create_app`, juste après `settings = settings or get_settings()` : `quality = build_quality_params(settings)` (échoue dès la construction), puis `AnalysisService(architecture_service, analysis, params=quality)` dans la branche `if analysis is not None`.

- [ ] **Step 4: Test du démarrage**

Ajouter à `backend/tests/unit/test_analysis_service.py` (ou `test_config.py`) :

```python
def test_a_wrong_required_property_stops_the_application_from_building() -> None:
    from ea.main import create_app

    with pytest.raises(ValueError, match="bogus"):
        create_app(Settings(debug=True, auth_enabled=False, quality_required={"bogus": ["owner"]}))
```
(importer `Settings` : `from ea.core.config import Settings`).

- [ ] **Step 5: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_config.py tests/unit/test_analysis_service.py tests/unit/test_service_guards.py -x`
Expected: tous passent.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests backend/.env.example
git commit -m "feat(quality): add the quality settings and AnalysisService.check" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 16: API — `GET /quality`

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (imports + modèles à la fin)
- Modify: `backend/src/ea/api/analysis.py` (route)
- Modify: `backend/src/ea/api/errors.py` (`ScopeTooLargeError`)
- Modify: `backend/tests/e2e/test_analysis_api.py`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (par `make openapi`)

**Interfaces:**
- Consumes: `AnalysisService.check`
- Produces:
```
GET /quality?severity=&code=&limit=(1..1000, défaut 100)&cursor=(>=0) -> QualityReportRead   (401/403 via le bearer ; 422 scope_too_large)
```
```python
FindingsLimit = Annotated[int, Field(ge=1, le=MAX_FINDINGS)]
Cursor = Annotated[int, Field(ge=0)]
class FindingRead(BaseModel): rule: RuleCode; severity: Severity; element_id: UUID; element_name: str; related_id: UUID | None; related_name: str | None; message: str; fix_hint: str
class RuleCountRead(BaseModel): rule: RuleCode; severity: Severity; count: int
class QualityReportRead(BaseModel): total: int; counts: list[RuleCountRead]; findings: list[FindingRead]; next_cursor: int | None   # .of(page)
```
```ts
// components['schemas']['QualityReportRead' | 'FindingRead' | 'RuleCountRead' | 'RuleCode' | 'Severity']
```

- [ ] **Step 1: Écrire les tests e2e qui échouent**

Ajouter à `backend/tests/e2e/test_analysis_api.py` :

```python
@pytest.mark.asyncio
class TestTheQualityReport:
    async def test_a_lone_element_is_reported_with_where_to_fix_it(
        self, client: httpx.AsyncClient
    ) -> None:
        node = await an_element(client, "node", "Loner")

        body = (await client.get("/quality")).json()

        orphan = next(f for f in body["findings"] if f["rule"] == "orphan")
        assert orphan["element_id"] == node["id"]
        assert orphan["severity"] == "warning"
        assert orphan["fix_hint"] == f"/elements?element={node['id']}"
        assert {c["rule"] for c in body["counts"]} >= {"orphan", "naming", "inconsistent_relationship"}
        assert body["total"] == len(body["findings"])

    async def test_filters_are_applied(self, client: httpx.AsyncClient) -> None:
        await an_element(client, "node", "Loner")

        body = (await client.get("/quality", params={"severity": "info"})).json()

        assert {f["rule"] for f in body["findings"]} == {"no_description"}
        assert body["total"] >= 2  # the counts' total is not narrowed by the filter

    async def test_paging_walks_the_findings(self, client: httpx.AsyncClient) -> None:
        for index in range(3):
            await an_element(client, "node", f"n{index}")

        first = (await client.get("/quality", params={"limit": 4})).json()
        second = (await client.get("/quality", params={"limit": 4, "cursor": first["next_cursor"]})).json()

        assert len(first["findings"]) == 4 and first["next_cursor"] == 4
        assert len(second["findings"]) == 2 and second["next_cursor"] is None

    async def test_a_message_naming_an_element_is_data_not_markup(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element(client, "node", "<script>alert(1)</script>")

        body = (await client.get("/quality")).json()

        assert any("<script>" in f["message"] for f in body["findings"])  # carried verbatim; Vue escapes it

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"cursor": -1}, {"code": "nope"}])
    async def test_out_of_bounds_arguments_are_a_422(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> None:
        assert (await client.get("/quality", params=params)).status_code == 422

    async def test_no_token_is_a_401(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=StaticVerifier({}),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
            assert (await anonymous.get("/quality")).status_code == 401
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py -x -k Quality`
Expected: FAIL — 404 sur `/quality`.

- [ ] **Step 3: Implémenter**

`backend/src/ea/api/errors.py` : importer `ScopeTooLargeError` et ajouter dans `_STATUS` (section « Model analysis ») :

```python
    ScopeTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "scope_too_large"),
```

`backend/src/ea/api/schemas.py` : importer `from ea.domain.quality import MAX_FINDINGS, Finding, QualityPage, RuleCode, Severity`, puis à la fin :

```python
# --- Quality checks (docs/adr/<ADR lot 2>) ---------------------------------

FindingsLimit = Annotated[int, Field(ge=1, le=MAX_FINDINGS)]
Cursor = Annotated[int, Field(ge=0, description="Where the previous page stopped.")]


def fix_hint_of(finding: Finding) -> str:
    """An application link to where the finding is fixed — a route, never code."""
    if finding.rule is RuleCode.INCONSISTENT_RELATIONSHIP:
        return f"/analyse/voisinage?element={finding.element_id}"
    return f"/elements?element={finding.element_id}"


class FindingRead(BaseModel):
    rule: RuleCode
    severity: Severity
    element_id: UUID
    element_name: str
    related_id: UUID | None
    related_name: str | None
    message: str
    fix_hint: str

    @classmethod
    def of(cls, finding: Finding) -> FindingRead:
        return cls(
            rule=finding.rule,
            severity=finding.severity,
            element_id=finding.element_id,
            element_name=finding.element_name,
            related_id=finding.related_id,
            related_name=finding.related_name,
            message=finding.message,
            fix_hint=fix_hint_of(finding),
        )


class RuleCountRead(BaseModel):
    rule: RuleCode
    severity: Severity
    count: int


class QualityReportRead(BaseModel):
    """One page of findings, with the counts of the whole run."""

    total: int = Field(description="Findings of the whole run, before any filter.")
    counts: list[RuleCountRead]
    findings: list[FindingRead]
    next_cursor: int | None

    @classmethod
    def of(cls, page: QualityPage) -> QualityReportRead:
        return cls(
            total=len(page.report.findings),
            counts=[
                RuleCountRead(rule=c.rule, severity=c.severity, count=c.count)
                for c in page.report.counts
            ],
            findings=[FindingRead.of(finding) for finding in page.findings],
            next_cursor=page.next_cursor,
        )
```

`backend/src/ea/api/analysis.py` : imports `Cursor`, `FindingsLimit`, `QualityReportRead`, `from ea.domain.quality import RuleCode, Severity`, puis :

```python
FindingsQuery = Annotated[FindingsLimit, Query()]
CursorQuery = Annotated[Cursor, Query()]


# --- Quality checks -----------------------------------------------------------


@router.get(
    "/quality",
    response_model=QualityReportRead,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}},
)
async def read_quality(
    service: Analysis,
    severity: Annotated[Severity | None, Query()] = None,
    code: Annotated[RuleCode | None, Query()] = None,
    limit: FindingsQuery = 100,
    cursor: CursorQuery = 0,
) -> QualityReportRead:
    """Run the quality rules over the catalogue and page through the findings.

    Computed on every call — nothing is stored. The counts describe the whole
    run; `severity` and `code` narrow the findings only.
    """
    return QualityReportRead.of(
        await service.check(severity=severity, code=code, limit=limit, cursor=cursor)
    )
```

- [ ] **Step 4: Relancer, PASS puis régénérer**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py tests/e2e/test_auth_api.py tests/unit/test_openapi.py -x`
Expected: tous passent.

Run: `make openapi && make openapi-check`
Expected: « Client généré à jour. »

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/api backend/tests/e2e/test_analysis_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(quality): serve the quality report over HTTP" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 17: MCP — l'outil `check_model`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports, outil, `INSTRUCTIONS`)
- Modify: `backend/tests/unit/test_mcp_server.py`

**Interfaces:**
- Consumes: `AnalysisService.check`, `QualityReportRead`, `FindingsLimit`, `Cursor`, `Severity`, `RuleCode`
- Produces: `check_model(severity: Severity | None = None, code: RuleCode | None = None, limit: FindingsLimit = 100, cursor: Cursor = 0) -> QualityReportRead` (READS)

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `test_mcp_server.py` : ajouter `"check_model",` à la liste complète (après `"get_relationship_matrix",`), ajouter `("check_model", "limit", "/quality", "limit")` et `("check_model", "cursor", "/quality", "cursor")` à la paramétrisation de `test_a_query_parameter_and_its_tool_argument_agree`, et :

```python
@pytest.mark.asyncio
class TestCheckModel:
    async def test_it_reports_what_a_quality_run_finds(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "Loner")

        report = await call(server, "check_model")

        assert "orphan" in {f["rule"] for f in report["findings"]}
        assert report["total"] == len(report["findings"])

    async def test_it_filters_by_code(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "Loner")

        report = await call(server, "check_model", code="orphan")

        assert {f["rule"] for f in report["findings"]} == {"orphan"}

    async def test_it_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "check_model")

        assert tool.annotations is not None and tool.annotations.read_only_hint is True

    async def test_its_description_says_when_to_call_it(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "check_model")

        assert "after an import" in (tool.description or "")

    async def test_an_unknown_rule_code_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "check_model", code="nope")

    async def test_a_limit_over_the_ceiling_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "check_model", limit=1001)
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — la liste d'outils ne contient pas `check_model`.

- [ ] **Step 3: Implémenter**

Dans `server.py` : importer `Cursor`, `FindingsLimit`, `QualityReportRead` depuis `ea.api.schemas`, `from ea.domain.quality import RuleCode, Severity`. Après `get_relationship_matrix` :

```python
    @server.tool(annotations=READS)
    @speaking_plainly
    async def check_model(
        severity: Severity | None = None,
        code: RuleCode | None = None,
        limit: FindingsLimit = 100,
        cursor: Cursor = 0,
    ) -> QualityReportRead:
        """Check the model for problems: orphans, probable duplicates, naming, gaps.

        Call it before you write — to avoid creating what already exists under
        another spelling — and after an import, to see what the import left
        behind. Each finding names an element, says why, and carries a `fix_hint`
        route. `counts` describe the whole catalogue; `severity` (`error`,
        `warning`, `info`) and `code` (`orphan`, `duplicate_probable`, `naming`,
        `missing_property`, `no_description`, `inconsistent_relationship`) narrow
        only the findings listed, and `next_cursor` pages through them. It reads
        and stores nothing: every call recomputes.
        """
        return QualityReportRead.of(
            await get_analysis().check(severity=severity, code=code, limit=limit, cursor=cursor)
        )
```

`INSTRUCTIONS`, après le paragraphe de `get_relationship_matrix` (terminé par `...for each element.\`) :

```
`check_model` reads the quality of the model itself — elements linked to
nothing, names that look like duplicates, missing descriptions or required
properties, links the metamodel would refuse today. Run it before adding to the
catalogue and after an import, and fix what it reports at the element it names.\
```
(remplacer la fin `for each element.\` + `"""` pour que le nouveau paragraphe soit le dernier avant `"""`, séparé par une ligne vide.)

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): add the read-only check_model tool" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 18: SPA — la section Qualité

**Files:**
- Create: `frontend/src/features/quality/labels.ts`
- Create: `frontend/src/features/quality/useQuality.ts`
- Create: `frontend/src/features/quality/QualitySection.vue`
- Create: `frontend/tests/useQuality.spec.ts`, `frontend/tests/QualitySection.spec.ts`
- Modify: `frontend/tests/support/api.ts` (`aFinding`, `aQualityReport`)
- Modify: `frontend/src/router/sections.ts`

**Interfaces:**
- Consumes: `QualityReportRead`, `FindingRead`, `RuleCode`, `Severity`
- Produces:
```ts
export const RULE_LABELS: Record<RuleCode, string>
export const SEVERITY_LABELS: Record<Severity, string>
export type QualityFilters = { severity: Severity | ''; code: RuleCode | '' }
export function useQuality(): { report: Ref<QualityReportRead | null>; findings: Ref<FindingRead[]>; status; error; load(filters: QualityFilters): Promise<void>; more(filters: QualityFilters): Promise<void> }
// route: { path: '/analyse/qualite', name: 'quality', label: 'Qualité', group: 'analysis' }, query ?severity= ?code=
```

- [ ] **Step 1: Fabriques**

Dans `frontend/tests/support/api.ts` (types `FindingRead`, `QualityReportRead` en tête) :

```ts
/** One finding, as `GET /quality` returns it. */
export function aFinding(overrides: Partial<FindingRead> = {}): FindingRead {
  return {
    rule: 'orphan',
    severity: 'warning',
    element_id: '11111111-1111-4111-8111-111111111111',
    element_name: 'Facturation',
    related_id: null,
    related_name: null,
    message: "« Facturation » n'est relié à aucun élément.",
    fix_hint: '/elements?element=11111111-1111-4111-8111-111111111111',
    ...overrides,
  }
}

/** A report with a count per rule — zeros included, like the API. */
export function aQualityReport(
  findings: FindingRead[] = [aFinding()],
  overrides: Partial<QualityReportRead> = {},
): QualityReportRead {
  const rules = ['orphan', 'duplicate_probable', 'naming', 'missing_property', 'no_description', 'inconsistent_relationship'] as const
  const severity = { orphan: 'warning', duplicate_probable: 'warning', naming: 'info', missing_property: 'warning', no_description: 'info', inconsistent_relationship: 'error' } as const
  return {
    total: findings.length,
    counts: rules.map((rule) => ({
      rule,
      severity: severity[rule],
      count: findings.filter((finding) => finding.rule === rule).length,
    })),
    findings,
    next_cursor: null,
    ...overrides,
  }
}
```

- [ ] **Step 2: Écrire les tests qui échouent**

`frontend/tests/useQuality.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useQuality } from '../src/features/quality/useQuality'
import { aFinding, aQualityReport, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const NO_FILTER = { severity: '', code: '' } as const

describe('useQuality', () => {
  it('leaves an unset filter out of the query', async () => {
    const calls = stubApi([{ path: '/quality', body: aQualityReport() }])
    const quality = useQuality()

    await quality.load(NO_FILTER)

    expect(calls[0].url.searchParams.has('severity')).toBe(false)
    expect(calls[0].url.searchParams.has('code')).toBe(false)
    expect(quality.findings.value).toHaveLength(1)
  })

  it('sends the filters it was given', async () => {
    const calls = stubApi([{ path: '/quality', body: aQualityReport([]) }])
    const quality = useQuality()

    await quality.load({ severity: 'error', code: 'orphan' })

    expect(calls[0].url.searchParams.get('severity')).toBe('error')
    expect(calls[0].url.searchParams.get('code')).toBe('orphan')
  })

  it('appends the next page from the cursor', async () => {
    const first = aFinding({ element_name: 'A' })
    const second = aFinding({ element_name: 'B' })
    const calls = stubApi([{ path: '/quality', body: aQualityReport([first], { next_cursor: 1 }) }])
    const quality = useQuality()
    await quality.load(NO_FILTER)

    stubApi([{ path: '/quality', body: aQualityReport([second]) }])
    await quality.more(NO_FILTER)

    expect(calls).toHaveLength(1)
    expect(quality.findings.value.map((finding) => finding.element_name)).toEqual(['A', 'B'])
    expect(quality.report.value?.next_cursor).toBeNull()
  })

  it('reports a refusal in words', async () => {
    stubApi([{ path: '/quality', status: 422, body: { error: 'scope_too_large', detail: 'trop large' } }])
    const quality = useQuality()

    await quality.load(NO_FILTER)

    expect(quality.error.value).toBe('trop large')
    expect(quality.report.value).toBeNull()
  })
})
```

`frontend/tests/QualitySection.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import QualitySection from '../src/features/quality/QualitySection.vue'
import { createAppRouter } from '../src/router'
import { aFinding, aQualityReport, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function open(routes: Route[], query = '') {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/qualite${query}`)
  await router.isReady()
  render(QualitySection, { global: { plugins: [router] } })
  return { calls, router }
}

const ORPHAN = aFinding()
const DUPLICATE = aFinding({
  rule: 'duplicate_probable',
  element_name: 'Facturation 2',
  message: '« Facturation 2 » ressemble à « Facturation ».',
})

describe('QualitySection', () => {
  it('counts each rule at the top, zeros included', async () => {
    await open([{ path: '/quality', body: aQualityReport([ORPHAN, DUPLICATE]) }])

    expect(await screen.findByRole('button', { name: /orphelin.*1/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /relation incohérente.*0/i })).toBeInTheDocument()
  })

  it('groups the findings by rule with a fix link each', async () => {
    await open([{ path: '/quality', body: aQualityReport([ORPHAN, DUPLICATE]) }])

    const table = await screen.findByRole('table')
    expect(within(table).getByText(/n'est relié à aucun élément/)).toBeInTheDocument()
    const links = within(table).getAllByRole('link', { name: /corriger/i })
    expect(links[0]).toHaveAttribute('href', ORPHAN.fix_hint)
  })

  it('shows an element name as text, never as markup', async () => {
    const hostile = aFinding({ element_name: '<img src=x onerror=alert(1)>', message: '<b>x</b>' })
    await open([{ path: '/quality', body: aQualityReport([hostile]) }])

    expect(await screen.findByText('<b>x</b>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('reads the filters from the URL and sends them', async () => {
    const { calls } = await open(
      [{ path: '/quality', body: aQualityReport([]) }],
      '?severity=error&code=orphan',
    )

    await screen.findByText(/aucun constat/i)
    expect(calls[0].url.searchParams.get('severity')).toBe('error')
    expect(calls[0].url.searchParams.get('code')).toBe('orphan')
  })

  it('writes a clicked rule into the URL, replacing the history entry', async () => {
    const { router } = await open([{ path: '/quality', body: aQualityReport([ORPHAN]) }])

    await fireEvent.click(await screen.findByRole('button', { name: /orphelin/i }))

    await waitFor(() => expect(router.currentRoute.value.query.code).toBe('orphan'))
  })

  it('says a clean model is clean', async () => {
    await open([{ path: '/quality', body: aQualityReport([]) }])

    expect(await screen.findByText(/aucun constat/i)).toBeInTheDocument()
  })

  it('offers the next page when the API gives a cursor', async () => {
    const { calls } = await open([
      { path: '/quality', body: aQualityReport([ORPHAN], { next_cursor: 1 }) },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: /afficher la suite/i }))

    await waitFor(() => expect(calls[calls.length - 1].url.searchParams.get('cursor')).toBe('1'))
  })

  it('shows the refusal in words', async () => {
    await open([{ path: '/quality', status: 422, body: { error: 'scope_too_large', detail: 'catalogue trop large' } }])

    expect(await screen.findByRole('alert')).toHaveTextContent('catalogue trop large')
  })
})
```

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/useQuality.spec.ts tests/QualitySection.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/quality/useQuality"`.

- [ ] **Step 4: Implémenter**

`frontend/src/features/quality/labels.ts` :

```ts
// How a rule and a severity read. Translation only: whether an element is an
// orphan, or a name a duplicate, is decided by the API — these tables restate
// no rule (docs/adr/0012).
import type { components } from '../../api/schema'

export type RuleCode = components['schemas']['RuleCode']
export type Severity = components['schemas']['Severity']

export const RULE_LABELS: Record<RuleCode, string> = {
  orphan: 'Orphelin',
  duplicate_probable: 'Doublon probable',
  naming: 'Nommage',
  missing_property: 'Propriété manquante',
  no_description: 'Sans description',
  inconsistent_relationship: 'Relation incohérente',
}

export const SEVERITY_LABELS: Record<Severity, string> = {
  error: 'Erreur',
  warning: 'Avertissement',
  info: 'Information',
}
```

`frontend/src/features/quality/useQuality.ts` :

```ts
// The quality screen's state: one page of findings, the counts of the run, and
// the next page on request. Every filter change is a new question, so a late
// answer to the previous one is dropped (lib/latest.ts).
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { RuleCode, Severity } from './labels'

export type QualityReportRead = components['schemas']['QualityReportRead']
export type FindingRead = components['schemas']['FindingRead']

export type QualityFilters = { severity: Severity | ''; code: RuleCode | '' }

export function useQuality() {
  const report = ref<QualityReportRead | null>(null)
  const findings = ref<FindingRead[]>([])
  const listing = useLatestRequest()

  function query(filters: QualityFilters, cursor: number) {
    // An empty filter is left out: `severity=` would be a filter matching nothing.
    return {
      ...(filters.severity ? { severity: filters.severity } : {}),
      ...(filters.code ? { code: filters.code } : {}),
      ...(cursor > 0 ? { cursor } : {}),
    }
  }

  async function load(filters: QualityFilters): Promise<void> {
    await listing.run(
      async (signal) =>
        unwrap(await api.GET('/quality', { params: { query: query(filters, 0) }, signal })),
      (answer) => {
        report.value = answer
        findings.value = answer.findings
      },
      () => {
        report.value = null
        findings.value = []
      },
    )
  }

  async function more(filters: QualityFilters): Promise<void> {
    const cursor = report.value?.next_cursor
    if (cursor === null || cursor === undefined) {
      return
    }
    await listing.run(
      async (signal) =>
        unwrap(await api.GET('/quality', { params: { query: query(filters, cursor) }, signal })),
      (answer) => {
        report.value = answer
        findings.value = [...findings.value, ...answer.findings]
      },
    )
  }

  return { report, findings, status: listing.status, error: listing.error, load, more }
}
```

`frontend/src/features/quality/QualitySection.vue` :

```vue
<script setup lang="ts">
// The `/analyse/qualite` section: what is wrong with the model, by rule.
//
// Every finding is computed by the API on request; this screen only reads and
// translates them. The filters live in the URL (`?severity=`, `?code=`) so a
// view of "all orphans" is a link one can send. A finding's message carries
// element names — user data — and is rendered as text by Vue, never as HTML.
import { computed, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { RULE_LABELS, SEVERITY_LABELS, type RuleCode, type Severity } from './labels'
import { useQuality, type FindingRead, type QualityFilters } from './useQuality'

const route = useRoute()
const router = useRouter()
const quality = useQuality()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

// A value typed by hand is passed on as it stands: the API knows the codes and
// refuses anything else, and its refusal is the message the user should read.
const filters = computed<QualityFilters>(() => ({
  severity: one(route.query.severity) as Severity | '',
  code: one(route.query.code) as RuleCode | '',
}))

watch(
  filters,
  async (current) => {
    await quality.load(current)
  },
  { immediate: true },
)

function ask(changes: Record<string, string | undefined>): void {
  void router.replace({ query: { ...route.query, ...changes } })
}

const groups = computed(() => {
  const byRule = new Map<RuleCode, FindingRead[]>()
  for (const finding of quality.findings.value) {
    byRule.set(finding.rule, [...(byRule.get(finding.rule) ?? []), finding])
  }
  return [...byRule.entries()].map(([rule, items]) => ({ rule, items }))
})
</script>

<template>
  <section class="section">
    <header>
      <h2>Qualité du modèle</h2>
      <p class="hint">Orphelins, doublons, nommage, données manquantes, liens devenus incohérents.</p>
    </header>

    <p v-if="quality.status.value === 'error'" class="banner banner--error" role="alert">
      {{ quality.error.value }}
    </p>

    <template v-if="quality.report.value">
      <div class="counts" role="group" aria-label="Constats par règle">
        <button
          v-for="count in quality.report.value.counts"
          :key="count.rule"
          type="button"
          :aria-pressed="filters.code === count.rule"
          @click="ask({ code: filters.code === count.rule ? undefined : count.rule })"
        >
          {{ RULE_LABELS[count.rule] }} ({{ count.count }})
        </button>
      </div>

      <div class="controls">
        <div class="field">
          <label for="quality-severity">Gravité</label>
          <select
            id="quality-severity"
            :value="filters.severity"
            @change="ask({ severity: ($event.target as HTMLSelectElement).value || undefined })"
          >
            <option value="">Toutes</option>
            <option v-for="(label, value) in SEVERITY_LABELS" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </div>
      </div>

      <p v-if="quality.findings.value.length === 0" class="hint">
        Aucun constat pour ce filtre — {{ quality.report.value.total }} sur l'ensemble du modèle.
      </p>

      <table v-else class="table">
        <thead>
          <tr>
            <th>Règle</th>
            <th>Gravité</th>
            <th>Élément</th>
            <th>Constat</th>
            <th><span class="sr-only">Action</span></th>
          </tr>
        </thead>
        <tbody v-for="group in groups" :key="group.rule">
          <tr v-for="finding in group.items" :key="`${finding.rule}-${finding.element_id}-${finding.related_id ?? ''}-${finding.message}`">
            <td>{{ RULE_LABELS[finding.rule] }}</td>
            <td>{{ SEVERITY_LABELS[finding.severity] }}</td>
            <td>{{ finding.element_name }}</td>
            <td>{{ finding.message }}</td>
            <td>
              <RouterLink :to="finding.fix_hint">Corriger</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>

      <button
        v-if="quality.report.value.next_cursor !== null"
        type="button"
        @click="quality.more(filters)"
      >
        Afficher la suite
      </button>
    </template>
    <p v-else-if="quality.status.value === 'loading'" class="hint">Analyse en cours…</p>
  </section>
</template>
```

Dans `sections.ts`, après l'entrée `matrix` :

```ts
  {
    path: '/analyse/qualite',
    name: 'quality',
    label: 'Qualité',
    summary: 'Orphelins, doublons, nommage, données manquantes : ce qui cloche dans le modèle.',
    group: 'analysis',
    view: () => import('../features/quality/QualitySection.vue'),
  },
```

(`quality.more(filters)` est passé tel quel dans le template : `filters` est un `ComputedRef` auto-déballé dans le template ; ESLint `no-floating-promises` demande `void` dans un handler inline — écrire `@click="void quality.more(filters)"` si ESLint le signale.)

- [ ] **Step 5: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/useQuality.spec.ts tests/QualitySection.spec.ts tests/router.spec.ts && npm run typecheck && npm run lint`
Expected: tous passent, aucune erreur de types ni de lint.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/quality frontend/src/router/sections.ts frontend/tests/useQuality.spec.ts frontend/tests/QualitySection.spec.ts frontend/tests/support/api.ts
git commit -m "feat(quality): add the quality section" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Vérification du lot 2, test d'intégration et ADR des contrôles

**Files:**
- Create: `backend/tests/integration/test_quality_store.py`
- Create: `docs/adr/<N2>-controles-de-qualite-du-modele.md`

**Interfaces:**
- Consumes: `AnalysisService.check`, `PostgresArchitectureRepository.add_relationship`
- Produces: l'ADR du lot 2 ; la preuve que la règle pure donne le bon résultat sur de vraies lignes.

- [ ] **Step 1: Test d'intégration**

`backend/tests/integration/test_quality_store.py` :

```python
"""The quality run against a real PostgreSQL: the links it reads are the stored ones."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.quality import RuleCode
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from tests.support_analysis import a_stored_link

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_orphans_are_found_from_the_stored_links(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api = await graph_service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")
    loner = await graph_service.create_element(element_type=E.NODE, name="Loner")
    await graph_service.connect(relationship_type=R.SERVING, source_id=api.id, target_id=process.id)

    page = await AnalysisService(graph_service, graph_repository).check(code=RuleCode.ORPHAN)

    assert [f.element_id for f in page.findings] == [loner.id]


async def test_a_link_stored_before_the_rules_changed_is_reported_not_deleted(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    data = await graph_service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
    process = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")
    illegal = a_stored_link(R.SERVING, data, process)
    await graph_repository.add_relationship(illegal)

    page = await AnalysisService(graph_service, graph_repository).check(
        code=RuleCode.INCONSISTENT_RELATIONSHIP
    )

    assert [(f.element_id, f.related_id) for f in page.findings] == [(data.id, process.id)]
    assert await graph_repository.get_relationship(illegal.id) is not None


async def test_the_run_is_the_same_twice(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    for name in ("Facturation client", "Facturation clients"):
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)
    analysis = AnalysisService(graph_service, graph_repository)

    assert await analysis.check() == await analysis.check()
```

Run: `make test-integration` (ou `cd backend && uv run pytest tests/integration/test_quality_store.py -x` avec `make pg-up`)
Expected: 3 passed.

- [ ] **Step 2: ADR**

Calculer `N2` = prochain numéro libre (`ls docs/adr | grep -E '^[0-9]{4}-' | tail -1`, plus un). Créer `docs/adr/<N2>-controles-de-qualite-du-modele.md` :

```markdown
---
titre: Les contrôles de qualité du modèle, calculés à la demande
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/quality.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/core/config.py, backend/src/ea/mcp/server.py, frontend/src/features/quality, CLAUDE.md
---

# <N2>. Les contrôles de qualité du modèle, calculés à la demande

Date : 2026-10-10
Statut : Proposition

## Contexte

Le métamodèle refuse à l'écriture ce qu'ArchiMate interdit. Il ne dit rien de
ce qui est seulement *suspect* : un élément relié à rien, deux noms qui se
ressemblent à un accent près, une description absente, un lien que le
métamodèle ne permettrait plus aujourd'hui parce qu'il a évolué ou qu'un import
l'a laissé passer. Sparx EA appelle cela les contrôles du modèle ; ici le
pipeline `alimenter-catalogue` (`adr/0028`) en a un besoin immédiat : lire ce
qu'un import a laissé.

Deux tentations ont été écartées : stocker le résultat d'un contrôle (il serait
périmé à la modification suivante d'un élément, donc une seconde vérité), et
laisser chaque client recalculer les règles (trois adaptateurs, trois définitions
d'un « doublon »).

## Décision

1. **Les contrôles sont calculés à chaque appel**, sur les éléments du
   catalogue et les liens qui les touchent (deux lectures), puis passés à des
   fonctions pures de `domain/quality.py`. Aucune table, aucune migration.
2. **Sept règles au plus, chacune identifiée par un code stable**
   (`orphan`, `duplicate_probable`, `naming`, `missing_property`,
   `no_description`, `inconsistent_relationship`, plus `unreadable_property` au
   lot suivant). Un code ne se renomme pas : le SPA, un agent et le pipeline
   s'appuient dessus. Les gravités sont fixées par la règle (`error`, `warning`,
   `info`), pas par l'appelant.
3. **Rien n'est corrigé automatiquement.** Un constat nomme l'élément et un
   `fix_hint`, route de l'application ; un lien devenu incohérent est signalé en
   `error`, jamais supprimé.
4. **Deux paramètres seulement, en configuration** : `EA_QUALITY_SIMILARITY`
   (seuil de ressemblance, 0,9 par défaut) et `EA_QUALITY_REQUIRED` (JSON, les
   propriétés qu'un type doit porter). Validés au démarrage par
   `QualityParams.from_settings` : un type inconnu ou une clé qui n'est pas un
   identifiant arrête le boot. Une table `quality_rules` n'existe que si des
   paramètres par périmètre sont demandés.
5. **Doublons** : même type, noms égaux une fois la casse, les accents et la
   ponctuation retirés, ou ressemblance (`difflib.SequenceMatcher`) au moins égale
   au seuil. Comparaison par blocs (type, première lettre normalisée) ; un
   `duplicate_probable` est un avertissement, jamais une erreur, et il n'y a pas
   de liste d'exceptions (rien n'est stocké).
6. **Plafond** : 5 000 éléments par exécution (`MAX_QUALITY_ELEMENTS`). Au-delà,
   422 `scope_too_large` : un rapport partiel se lirait comme un rapport propre.
7. **Les compteurs décrivent tout l'ensemble**, les filtres `severity` et `code`
   ne restreignent que la liste, paginée par un `cursor` (page de 1 000 au plus,
   `MAX_FINDINGS`).
8. **Les orphelins ne passent pas par un `NOT EXISTS`** : avec le plafond ci-dessus,
   la règle pure sur `links_touching` donne le même résultat sans second chemin.
9. **Trace** : une ligne `action=quality_run` avec `duration_ms` et le nombre de
   constats ; jamais de valeur de propriété.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker les constats | lecture immédiate, historique | périmés à chaque édition, deux vérités | écartée |
| Règles définies par l'utilisateur | souple | hors périmètre, surface d'injection | écartée |
| Détection d'orphelins en SQL (`NOT EXISTS`) | tient un très grand modèle | second chemin à garder identique ; inutile sous 5 000 éléments | écartée, à rouvrir si le plafond monte |
| Auto-correction | gain de temps | peut détruire une donnée voulue | écartée |
| Liste d'exceptions de doublons | zéro faux positif | demande un stockage | écartée |

## Conséquences

- Un contrôle est aussi frais que le modèle ; il coûte deux requêtes et du CPU
  quadratique par bloc de noms (bornés par le plafond).
- Un faux positif de doublon se règle en montant le seuil pour tout le monde,
  pas pour un cas.
- Une erreur de frappe sur la *première lettre* d'un nom échappe au rapprochement ;
  acceptée, c'est un avertissement.
- Quand les paquets existeront (sous-projet 1), `GET /quality` gagnera un
  périmètre sans changer le contrat des constats.
- Les messages contiennent des noms d'éléments : le SPA les rend en texte.

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0028](0028-pipelines-python-dans-le-depot-ea.md), [0032](0032-authentification-par-keycloak.md), [<N1>](<N1>-matrice-de-relations.md)
- Code concerné : `backend/src/ea/domain/quality.py`, `backend/src/ea/services/analysis.py`
```

- [ ] **Step 3: Régénérer et vérifier**

Run: `make openapi && make check`
Expected: vert.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_quality_store.py docs/adr/<N2>-controles-de-qualite-du-modele.md
git commit -m "docs(adr): record the on-demand quality checks" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 3 — Agrégations, carte de chaleur et tableau de bord

### Task 20: `domain/analytics.py` — lecture des propriétés, dimensions, échelles

**Files:**
- Create: `backend/src/ea/domain/analytics.py`
- Create: `backend/tests/unit/test_analytics.py`
- Modify: `backend/src/ea/domain/errors.py` (`InvalidAnalysisError`)

**Interfaces:**
- Consumes: `PROPERTY_KEY_PATTERN`
- Produces:
```python
MAX_BUCKETS: Final = 200; RAW_GROUP_CEILING: Final = 5000; UNSET_KEY = "__unset__"; INVALID_KEY = "__invalid__"; LEVELS = 5
DIMENSION_PATTERN: Final = r"^(element_type|layer|property:[A-Za-z][A-Za-z0-9_]{0,62})$"
class ReadingKind(StrEnum): NUMBER; ORDERED
@dataclass(frozen=True, slots=True) class Reading: kind: ReadingKind; order: tuple[str, ...] = ()
ANALYTIC_PROPERTIES: Mapping[str, Reading]   # lifecycle, risk (ordered), cost (number)
def read_number(raw: str) -> float | None
def read_rank(key: str, raw: str) -> int | None
class Metric(StrEnum): COUNT="count"; SUM="sum"; AVG="avg"; MIN="min"; MAX="max"
class DimensionKind(StrEnum): ELEMENT_TYPE="element_type"; LAYER="layer"; PROPERTY="property"
@dataclass(frozen=True, slots=True) class Dimension: kind: DimensionKind; key: str | None = None;  parse(text) -> Dimension; __str__
def validate_question(metric: Metric, property_key: str | None) -> str | None
def scale_for(values: Sequence[float | None]) -> tuple[int | None, ...]
def rank_level(rank: int, size: int) -> int
class InvalidAnalysisError(DomainError)
```

- [ ] **Step 1: Écrire les tests qui échouent**

`backend/tests/unit/test_analytics.py` :

```python
"""Reading properties as numbers and ranks, dimensions, and the colour scale."""

from __future__ import annotations

import pytest

from ea.domain.analytics import (
    INVALID_KEY,
    UNSET_KEY,
    Dimension,
    DimensionKind,
    Metric,
    rank_level,
    read_number,
    read_rank,
    scale_for,
    validate_question,
)
from ea.domain.errors import InvalidAnalysisError


class TestReadingANumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1200", 1200.0),
            ("1 200,50", 1200.5),
            ("1 200", 1200.0),
            ("1 200,5", 1200.5),
            ("-3.5", -3.5),
            ("12,5", 12.5),
            ("  42  ", 42.0),
        ],
    )
    def test_the_tolerated_spellings(self, raw: str, expected: float) -> None:
        assert read_number(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["1 200 €", "1.200,50", "", "abc", "1e3", "٣", "12,5,5", "--1"]
    )
    def test_anything_else_is_unreadable_not_guessed(self, raw: str) -> None:
        assert read_number(raw) is None


class TestReadingARank:
    def test_the_declared_order_ignores_case(self) -> None:
        assert [read_rank("risk", v) for v in ("faible", "Moyen", "ÉLEVÉ", "critique")] == [0, 1, 2, 3]

    def test_a_value_outside_the_enumeration_has_no_rank(self) -> None:
        assert read_rank("risk", "inconnu") is None

    def test_a_key_the_catalogue_does_not_order_has_no_rank(self) -> None:
        assert read_rank("owner", "faible") is None
        assert read_rank("cost", "faible") is None


class TestDimensions:
    def test_they_parse_and_print_back(self) -> None:
        for text in ("element_type", "layer", "property:risk"):
            assert str(Dimension.parse(text)) == text

    def test_a_property_dimension_carries_its_key(self) -> None:
        assert Dimension.parse("property:cost") == Dimension(DimensionKind.PROPERTY, "cost")

    @pytest.mark.parametrize("text", ["", "bogus", "property:", "property:1x", "property:a b", "Layer"])
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(InvalidAnalysisError):
            Dimension.parse(text)


class TestTheQuestion:
    def test_a_count_needs_no_property(self) -> None:
        assert validate_question(Metric.COUNT, None) is None
        assert validate_question(Metric.COUNT, "cost") is None

    def test_a_sum_needs_one(self) -> None:
        assert validate_question(Metric.SUM, "cost") == "cost"
        with pytest.raises(InvalidAnalysisError, match="sum"):
            validate_question(Metric.SUM, None)

    def test_the_key_must_be_a_plain_identifier(self) -> None:
        with pytest.raises(InvalidAnalysisError):
            validate_question(Metric.AVG, "cost'; --")


class TestTheScale:
    def test_ten_distinct_values_fall_into_five_equal_steps(self) -> None:
        assert scale_for([float(n) for n in range(1, 11)]) == (0, 0, 1, 1, 2, 2, 3, 3, 4, 4)

    def test_equal_values_share_a_level(self) -> None:
        assert scale_for([5.0, 5.0, 5.0]) == (0, 0, 0)

    def test_a_missing_value_has_no_level_and_does_not_shift_the_others(self) -> None:
        assert scale_for([1.0, None, 2.0]) == (0, None, 2)

    def test_nothing_in_nothing_out(self) -> None:
        assert scale_for([]) == ()
        assert scale_for([None, None]) == (None, None)

    def test_the_order_of_the_input_is_kept(self) -> None:
        assert scale_for([10.0, 1.0]) == (2, 0)

    def test_ranks_spread_over_the_five_levels(self) -> None:
        assert [rank_level(rank, 4) for rank in range(4)] == [0, 1, 3, 4]
        assert [rank_level(rank, 5) for rank in range(5)] == [0, 1, 2, 3, 4]
        assert rank_level(0, 1) == 0
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_analytics.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.analytics'`.

- [ ] **Step 3: Implémenter**

`backend/src/ea/domain/errors.py`, à la fin :

```python
class InvalidAnalysisError(DomainError):
    """A question the analysis cannot answer: a dimension that does not exist, a sum of nothing."""
```

`backend/src/ea/domain/analytics.py` :

```python
"""Reading the model as numbers: aggregates, scales, heat.

`properties` stays a map of strings (docs/adr/<ADR lot 3>): no schema, no
migration. What is added is a *catalogue of readings* — how the few keys the
dashboards care about are to be read, as a decimal number or as a position in
an ordered list — and a value that does not read is counted and reported,
never silently dropped. Any other key is a nominal label: it can be counted by,
not summed.

The colour of a heat-map cell is decided here, as a `level` from 0 to 4. The
SPA only maps a level to a swatch, so no threshold lives in TypeScript.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from ea.domain.errors import InvalidAnalysisError
from ea.domain.model import PROPERTY_KEY_PATTERN

#: Declared here and imported by `api/schemas.py` (a service cannot import the API).
MAX_BUCKETS: Final = 200
#: A hard stop on the groups one SQL query may return before they are merged.
RAW_GROUP_CEILING: Final = 5000
#: The bucket of an element that has no value for a property dimension.
UNSET_KEY: Final = "__unset__"
#: The bucket of an element whose value is outside the property's enumeration.
INVALID_KEY: Final = "__invalid__"
LEVELS: Final = 5
DIMENSION_PATTERN: Final = r"^(element_type|layer|property:[A-Za-z][A-Za-z0-9_]{0,62})$"

_BLANKS: Final = "   "
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_KEY = re.compile(PROPERTY_KEY_PATTERN)


class ReadingKind(StrEnum):
    NUMBER = "number"
    ORDERED = "ordered"


@dataclass(frozen=True, slots=True)
class Reading:
    kind: ReadingKind
    #: For `ORDERED`, the values from least to most.
    order: tuple[str, ...] = ()


#: How the keys the dashboards know are read. The values are stored as typed;
#: comparing them ignores case and surrounding blanks.
ANALYTIC_PROPERTIES: Final[Mapping[str, Reading]] = MappingProxyType(
    {
        "lifecycle": Reading(
            ReadingKind.ORDERED,
            ("planifié", "en construction", "en production", "en fin de vie", "retiré"),
        ),
        "risk": Reading(ReadingKind.ORDERED, ("faible", "moyen", "élevé", "critique")),
        "cost": Reading(ReadingKind.NUMBER),
    }
)


def read_number(raw: str) -> float | None:
    """A decimal number, or `None`.

    Tolerated: a `.` or `,` decimal, and spaces, no-break spaces (U+00A0) and
    narrow no-break spaces (U+202F) as thousands separators. Everything else —
    a currency sign, `1.200,50`, an exponent — is unreadable: guessing a
    spelling would turn a typo into a wrong total.
    """
    text = raw.translate(str.maketrans("", "", _BLANKS)).replace(",", ".")
    return float(text) if _NUMBER.fullmatch(text) else None


def _fold(text: str) -> str:
    return text.strip().casefold()


def read_rank(key: str, raw: str) -> int | None:
    """Where `raw` sits in the ordered reading of `key`, or `None`."""
    reading = ANALYTIC_PROPERTIES.get(key)
    if reading is None or reading.kind is not ReadingKind.ORDERED:
        return None
    folded = _fold(raw)
    for rank, value in enumerate(reading.order):
        if _fold(value) == folded:
            return rank
    return None


class Metric(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class DimensionKind(StrEnum):
    ELEMENT_TYPE = "element_type"
    LAYER = "layer"
    PROPERTY = "property"


@dataclass(frozen=True, slots=True)
class Dimension:
    """What elements are grouped by."""

    kind: DimensionKind
    key: str | None = None

    @classmethod
    def parse(cls, text: str) -> Dimension:
        if not re.fullmatch(DIMENSION_PATTERN, text):
            msg = f"{text!r} is not a dimension: element_type, layer or property:<key>"
            raise InvalidAnalysisError(msg)
        if text.startswith("property:"):
            return cls(DimensionKind.PROPERTY, text.split(":", 1)[1])
        return cls(DimensionKind(text))

    def __str__(self) -> str:
        return f"property:{self.key}" if self.kind is DimensionKind.PROPERTY else self.kind.value


def validate_question(metric: Metric, property_key: str | None) -> str | None:
    """The key numbers are read from — `None` for a count, which reads nothing."""
    if metric is Metric.COUNT:
        return None
    if not property_key:
        msg = f"the metric {metric.value} needs a property to read"
        raise InvalidAnalysisError(msg)
    if not _KEY.fullmatch(property_key):
        msg = f"{property_key!r} is not a plain property name"
        raise InvalidAnalysisError(msg)
    return property_key


def scale_for(values: Sequence[float | None]) -> tuple[int | None, ...]:
    """A level 0..4 per value, by quantile; equal values share a level.

    The thresholds are the values at 20, 40, 60 and 80 % of the sorted present
    values; a value's level is how many thresholds it is strictly above. A
    missing value has no level and does not move the others.
    """
    present = sorted(v for v in values if v is not None)
    if not present:
        return tuple(None for _ in values)
    thresholds = [present[max(0, math.ceil(step * len(present) / LEVELS) - 1)] for step in range(1, LEVELS)]
    return tuple(None if v is None else sum(1 for t in thresholds if v > t) for v in values)


def rank_level(rank: int, size: int) -> int:
    """Spread `size` ordered values over the five levels, ends included."""
    if size <= 1:
        return 0
    return int(rank * (LEVELS - 1) / (size - 1) + 0.5)
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_analytics.py -x`
Expected: tous passent. (`scale_for([1.0, None, 2.0])` : valeurs présentes `[1.0, 2.0]`, seuils `[1.0, 1.0, 2.0, 2.0]`, donc `1.0` → 0 et `2.0` → 2.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/analytics.py backend/src/ea/domain/errors.py backend/tests/unit/test_analytics.py
git commit -m "feat(analytics): add property readings, dimensions and the colour scale" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Groupes bruts, fusion et `summarise`

**Files:**
- Modify: `backend/src/ea/domain/analytics.py` (ajouter à la fin)
- Modify: `backend/tests/unit/test_analytics.py`

**Interfaces:**
- Consumes: Task 20, `Element`
- Produces:
```python
@dataclass(frozen=True, slots=True) class RawGroup: keys: tuple[str, ...]; count: int; valid: int = 0; total: float = 0.0; low: float | None = None; high: float | None = None; invalid: int = 0
def group_elements(elements: Iterable[Element], dimensions: Sequence[Dimension], metric_key: str | None = None) -> tuple[RawGroup, ...]
def classify(dimension: Dimension, raw: str) -> str
@dataclass(frozen=True, slots=True) class Bucket: keys: tuple[str, ...]; count: int; value: float | None; level: int | None
@dataclass(frozen=True, slots=True) class Aggregate: dimensions; metric; property_key; buckets; total; unreadable; truncated;  def axis_keys(self, position: int) -> tuple[str, ...]
def summarise(groups: Iterable[RawGroup], dimensions: Sequence[Dimension], metric: Metric, property_key: str | None = None) -> Aggregate
```

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à l'import de `test_analytics.py` : `MAX_BUCKETS, group_elements, summarise` et `from ea.domain.archimate import ElementType as E` et `from tests.support_analysis import an_element` ; puis :

```python
def node(name: str, **properties: str):  # type: ignore[no-untyped-def]
    return an_element(E.NODE, name, properties=properties)


def counts(aggregate) -> dict[tuple[str, ...], int]:  # type: ignore[no-untyped-def]
    return {bucket.keys: bucket.count for bucket in aggregate.buckets}


class TestCounting:
    def test_by_element_type(self) -> None:
        elements = [node("a"), node("b"), an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("element_type"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert counts(result) == {("business_process",): 1, ("node",): 2}
        assert result.total == 3

    def test_by_layer(self) -> None:
        elements = [node("a"), an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("layer"),)

        assert counts(summarise(group_elements(elements, dims), dims, Metric.COUNT)) == {
            ("business",): 1,
            ("technology",): 1,
        }

    def test_nothing_gives_an_empty_aggregate(self) -> None:
        dims = (Dimension.parse("layer"),)

        result = summarise(group_elements([], dims), dims, Metric.COUNT)

        assert (result.buckets, result.total, result.unreadable, result.truncated) == ((), 0, 0, False)

    def test_the_levels_of_a_count_are_quantiles_of_the_counts(self) -> None:
        elements = [node(f"n{i}") for i in range(3)] + [an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("element_type"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        by_key = {bucket.keys: bucket.level for bucket in result.buckets}
        assert by_key[("node",)] > by_key[("business_process",)]


class TestAnOrderedProperty:
    def elements(self):  # type: ignore[no-untyped-def]
        return [
            node("a", risk="Élevé"),
            node("b", risk="élevé"),
            node("c", risk="faible"),
            node("d", risk="inconnu"),
            node("e"),
            node("f", risk="   "),
        ]

    def test_values_are_folded_onto_the_declared_spelling_and_the_rest_goes_last(self) -> None:
        dims = (Dimension.parse("property:risk"),)

        result = summarise(group_elements(self.elements(), dims), dims, Metric.COUNT)

        assert [bucket.keys[0] for bucket in result.buckets] == [
            "faible",
            "élevé",
            INVALID_KEY,
            UNSET_KEY,
        ]
        assert counts(result)[("élevé",)] == 2
        assert counts(result)[(UNSET_KEY,)] == 2

    def test_the_level_is_the_rank_in_the_declared_order(self) -> None:
        dims = (Dimension.parse("property:risk"),)

        result = summarise(group_elements(self.elements(), dims), dims, Metric.COUNT)

        levels = {bucket.keys[0]: bucket.level for bucket in result.buckets}
        assert levels == {"faible": 0, "élevé": 3, INVALID_KEY: None, UNSET_KEY: None}


class TestANumericProperty:
    def elements(self):  # type: ignore[no-untyped-def]
        return [
            node("a", cost="1 200,50"),
            node("b", cost="300"),
            node("c", cost="1 200 €"),
            node("d"),
            node("e", cost="   "),
        ]

    def run(self, metric: Metric):  # type: ignore[no-untyped-def]
        dims = (Dimension.parse("layer"),)
        return summarise(group_elements(self.elements(), dims, "cost"), dims, metric, "cost")

    def test_sum_ignores_what_it_cannot_read_and_says_how_much_that_was(self) -> None:
        result = self.run(Metric.SUM)

        assert result.buckets[0].value == 1500.5
        assert result.unreadable == 1
        assert result.buckets[0].count == 5

    def test_the_other_metrics(self) -> None:
        assert self.run(Metric.AVG).buckets[0].value == 750.25
        assert self.run(Metric.MIN).buckets[0].value == 300.0
        assert self.run(Metric.MAX).buckets[0].value == 1200.5

    def test_a_bucket_with_nothing_readable_has_no_value_and_no_level(self) -> None:
        dims = (Dimension.parse("layer"),)

        result = summarise(
            group_elements([node("a", cost="n/a")], dims, "cost"), dims, Metric.SUM, "cost"
        )

        assert (result.buckets[0].value, result.buckets[0].level) == (None, None)
        assert result.unreadable == 1


class TestTwoDimensions:
    def test_a_heat_map_is_a_count_per_pair(self) -> None:
        elements = [
            node("a", lifecycle="en production"),
            node("b", lifecycle="en production"),
            an_element(E.BUSINESS_PROCESS, "p", properties={"lifecycle": "retiré"}),
        ]
        dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert counts(result) == {("business", "retiré"): 1, ("technology", "en production"): 2}
        assert result.axis_keys(0) == ("business", "technology")
        assert result.axis_keys(1) == ("en production", "retiré")

    def test_the_levels_of_a_pair_are_quantiles_of_the_counts(self) -> None:
        elements = [node(f"n{i}", lifecycle="retiré") for i in range(4)] + [
            an_element(E.BUSINESS_PROCESS, "p", properties={"lifecycle": "retiré"})
        ]
        dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        by_key = {bucket.keys: bucket.level for bucket in result.buckets}
        assert by_key[("technology", "retiré")] > by_key[("business", "retiré")]


class TestTheCeiling:
    def test_more_buckets_than_the_ceiling_are_cut_and_flagged(self) -> None:
        elements = [node(f"n{i}", team=f"team-{i:04d}") for i in range(MAX_BUCKETS + 5)]
        dims = (Dimension.parse("property:team"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert len(result.buckets) == MAX_BUCKETS
        assert result.truncated is True
        assert result.total == MAX_BUCKETS + 5


def test_grouping_is_independent_of_the_order_of_the_elements() -> None:
    elements = [node("a", risk="faible"), node("b", risk="élevé"), node("c", risk="faible")]
    dims = (Dimension.parse("property:risk"),)

    forward = summarise(group_elements(elements, dims), dims, Metric.COUNT)
    backward = summarise(group_elements(list(reversed(elements)), dims), dims, Metric.COUNT)

    assert forward == backward
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_analytics.py -x`
Expected: FAIL — `ImportError: cannot import name 'group_elements'`.

- [ ] **Step 3: Implémenter**

En tête d'`analytics.py`, ajouter `from collections.abc import Iterable, Mapping, Sequence`, `from dataclasses import dataclass, replace` et `from ea.domain.model import PROPERTY_KEY_PATTERN, Element`. À la fin du fichier :

```python
# --- Grouping ------------------------------------------------------------
#
# The database groups by the *raw* spelling of each dimension and returns
# partial sums per group (`RawGroup`); folding spellings together, ordering
# and colouring are done once, here, in `summarise` — so the SQL path and the
# in-memory path (`group_elements`) cannot disagree about what "Élevé" means.


@dataclass(frozen=True, slots=True)
class RawGroup:
    #: The raw value of each dimension, in order; `""` for a property that is absent.
    keys: tuple[str, ...]
    count: int
    #: How many elements of the group carry a readable number, and their sum / extremes.
    valid: int = 0
    total: float = 0.0
    low: float | None = None
    high: float | None = None
    #: Elements whose property is present but not a number.
    invalid: int = 0


def _raw_of(dimension: Dimension, element: Element) -> str:
    match dimension.kind:
        case DimensionKind.ELEMENT_TYPE:
            return element.element_type.value
        case DimensionKind.LAYER:
            return element.layer
        case DimensionKind.PROPERTY:
            return element.properties.get(dimension.key or "", "")


def group_elements(
    elements: Iterable[Element],
    dimensions: Sequence[Dimension],
    metric_key: str | None = None,
) -> tuple[RawGroup, ...]:
    """The reference grouping, in memory: what the SQL query must agree with."""
    counts: dict[tuple[str, ...], int] = {}
    numbers: dict[tuple[str, ...], list[float]] = {}
    invalid: dict[tuple[str, ...], int] = {}
    for element in elements:
        keys = tuple(_raw_of(dimension, element) for dimension in dimensions)
        counts[keys] = counts.get(keys, 0) + 1
        if metric_key is None:
            continue
        raw = element.properties.get(metric_key, "")
        if not raw.strip(" "):
            continue
        number = read_number(raw)
        if number is None:
            invalid[keys] = invalid.get(keys, 0) + 1
        else:
            numbers.setdefault(keys, []).append(number)
    return tuple(
        RawGroup(
            keys=keys,
            count=count,
            valid=len(numbers.get(keys, ())),
            total=sum(numbers.get(keys, ())),
            low=min(numbers.get(keys, ()), default=None),
            high=max(numbers.get(keys, ()), default=None),
            invalid=invalid.get(keys, 0),
        )
        for keys, count in counts.items()
    )


def _ordered(dimension: Dimension) -> Reading | None:
    if dimension.kind is not DimensionKind.PROPERTY:
        return None
    reading = ANALYTIC_PROPERTIES.get(dimension.key or "")
    return reading if reading and reading.kind is ReadingKind.ORDERED else None


def classify(dimension: Dimension, raw: str) -> str:
    """The bucket a raw value belongs to: itself, its declared spelling, or a sentinel."""
    if dimension.kind is not DimensionKind.PROPERTY:
        return raw
    text = raw.strip()
    if not text:
        return UNSET_KEY
    reading = _ordered(dimension)
    if reading is None:
        return text
    rank = read_rank(dimension.key or "", text)
    return reading.order[rank] if rank is not None else INVALID_KEY


def _sort_key(dimension: Dimension, key: str) -> tuple[int, int, str]:
    if key == UNSET_KEY:
        return (2, 0, "")
    if key == INVALID_KEY:
        return (1, 0, "")
    reading = _ordered(dimension)
    if reading is not None and key in reading.order:
        return (0, reading.order.index(key), "")
    return (0, 0, key.casefold())


@dataclass(frozen=True, slots=True)
class Bucket:
    keys: tuple[str, ...]
    count: int
    #: The metric over the bucket: the count itself for `COUNT`, else over readable values.
    value: float | None
    #: 0..4, or `None` for a bucket with nothing to colour.
    level: int | None


@dataclass(frozen=True, slots=True)
class Aggregate:
    dimensions: tuple[Dimension, ...]
    metric: Metric
    property_key: str | None
    buckets: tuple[Bucket, ...]
    #: Elements counted, before any cut.
    total: int
    #: Elements whose metric property is present but not a number.
    unreadable: int
    truncated: bool

    def axis_keys(self, position: int) -> tuple[str, ...]:
        """The distinct keys of one dimension, in the order that dimension reads."""
        dimension = self.dimensions[position]
        distinct = {bucket.keys[position] for bucket in self.buckets}
        return tuple(sorted(distinct, key=lambda key: _sort_key(dimension, key)))


@dataclass(slots=True)
class _Merged:
    count: int = 0
    valid: int = 0
    total: float = 0.0
    low: float | None = None
    high: float | None = None


def _smaller(one: float | None, other: float | None) -> float | None:
    return other if one is None else one if other is None else min(one, other)


def _larger(one: float | None, other: float | None) -> float | None:
    return other if one is None else one if other is None else max(one, other)


def _metric_of(metric: Metric, merged: _Merged) -> float | None:
    match metric:
        case Metric.COUNT:
            return float(merged.count)
        case Metric.SUM:
            return merged.total if merged.valid else None
        case Metric.AVG:
            return merged.total / merged.valid if merged.valid else None
        case Metric.MIN:
            return merged.low
        case Metric.MAX:
            return merged.high


def summarise(
    groups: Iterable[RawGroup],
    dimensions: Sequence[Dimension],
    metric: Metric,
    property_key: str | None = None,
) -> Aggregate:
    """Fold raw groups into buckets: classify, merge, order, cut and colour."""
    merged: dict[tuple[str, ...], _Merged] = {}
    unreadable = 0
    for group in groups:
        keys = tuple(
            classify(dimension, raw) for dimension, raw in zip(dimensions, group.keys, strict=True)
        )
        into = merged.setdefault(keys, _Merged())
        into.count += group.count
        into.valid += group.valid
        into.total += group.total
        into.low = _smaller(into.low, group.low)
        into.high = _larger(into.high, group.high)
        unreadable += group.invalid

    ordered = sorted(
        merged,
        key=lambda keys: tuple(
            _sort_key(dimension, key) for dimension, key in zip(dimensions, keys, strict=True)
        ),
    )
    total = sum(entry.count for entry in merged.values())
    kept = ordered[:MAX_BUCKETS]
    values = [_metric_of(metric, merged[keys]) for keys in kept]

    reading = _ordered(dimensions[0]) if len(dimensions) == 1 else None
    if reading is not None and metric is Metric.COUNT:
        levels: Sequence[int | None] = [
            rank_level(reading.order.index(keys[0]), len(reading.order))
            if keys[0] in reading.order
            else None
            for keys in kept
        ]
    else:
        levels = scale_for(values)

    return Aggregate(
        dimensions=tuple(dimensions),
        metric=metric,
        property_key=property_key,
        buckets=tuple(
            Bucket(keys=keys, count=merged[keys].count, value=value, level=level)
            for keys, value, level in zip(kept, values, levels, strict=True)
        ),
        total=total,
        unreadable=unreadable,
        truncated=len(ordered) > MAX_BUCKETS,
    )
```
(`replace` n'est pas utilisé : ne pas l'importer.)

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_analytics.py -x && uv run mypy src`
Expected: tous passent ; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/analytics.py backend/tests/unit/test_analytics.py
git commit -m "feat(analytics): group, merge, order and colour the aggregates" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 22: Le port `grouped` — SQL `GROUP BY` et double en mémoire

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (méthode sur `AnalysisRepository`)
- Modify: `backend/src/ea/repositories/architecture_store.py` (imports, constantes, helpers, méthode)
- Modify: `backend/tests/conftest.py` (`InMemoryRepository.grouped`)
- Modify: `backend/tests/integration/test_analysis_store.py`

**Interfaces:**
- Consumes: `Dimension`, `RawGroup`, `group_elements`
- Produces:
```python
# AnalysisRepository
async def grouped(self, dimensions: Sequence[Dimension], metric_key: str | None, *, scope: Sequence[UUID] | None = None) -> tuple[RawGroup, ...]
# store constants
ANALYSIS_TIMEOUT_MS: Final = 5000
```

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

Ajouter à `backend/tests/integration/test_analysis_store.py` (imports : `from ea.domain.analytics import Dimension, Metric, group_elements, summarise`) :

```python
DIRTY = [
    ("A", {"cost": "1 200,50", "risk": "Élevé", "lifecycle": "en production"}),
    ("B", {"cost": "300", "risk": "faible", "lifecycle": "en production"}),
    ("C", {"cost": "1 200 €", "risk": "élevé"}),
    ("D", {}),
    ("E", {"cost": "   ", "risk": "inconnu", "lifecycle": "retiré"}),
    ("F", {"cost": "1 000", "risk": "Faible"}),
]


async def dirty_model(service: ArchitectureService):  # type: ignore[no-untyped-def]
    return [
        await service.create_element(element_type=E.APPLICATION_COMPONENT, name=name, properties=props)
        for name, props in DIRTY
    ]


@pytest.mark.parametrize("metric", [Metric.COUNT, Metric.SUM, Metric.AVG, Metric.MIN, Metric.MAX])
async def test_the_sql_aggregate_is_the_pure_one_on_dirty_values(
    graph_service: ArchitectureService,
    graph_repository: PostgresArchitectureRepository,
    metric: Metric,
) -> None:
    elements = await dirty_model(graph_service)
    key = None if metric is Metric.COUNT else "cost"
    for text in ("property:risk", "layer", "element_type"):
        dims = (Dimension.parse(text),)

        from_sql = summarise(await graph_repository.grouped(dims, key), dims, metric, key)
        from_python = summarise(group_elements(elements, dims, key), dims, metric, key)

        assert from_sql == from_python, text


async def test_two_dimensions_agree_too(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

    from_sql = summarise(await graph_repository.grouped(dims, "cost"), dims, Metric.SUM, "cost")
    from_python = summarise(group_elements(elements, dims, "cost"), dims, Metric.SUM, "cost")

    assert from_sql == from_python


async def test_unreadable_costs_are_counted_not_cast(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    """`'1 200 €'::numeric` would abort the whole query: the CASE keeps the cast behind the regex."""
    await dirty_model(graph_service)
    dims = (Dimension.parse("layer"),)

    result = summarise(await graph_repository.grouped(dims, "cost"), dims, Metric.SUM, "cost")

    assert result.unreadable == 1
    assert result.buckets[0].value == 1200.5 + 300 + 1000


async def test_a_scope_narrows_the_groups(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    dims = (Dimension.parse("property:risk"),)

    inside = await graph_repository.grouped(dims, None, scope=[elements[1].id, elements[5].id])

    assert sum(group.count for group in inside) == 2


async def test_a_key_is_a_bound_parameter_never_spliced_into_the_statement(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    hostile = "x'; DROP TABLE elements; --"

    groups = await graph_repository.grouped((Dimension.parse("layer"),), hostile)

    assert sum(group.count for group in groups) == len(DIRTY)
    assert await graph_repository.get_element(elements[0].id) is not None  # the table is still there
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `make test-integration`
Expected: FAIL — `AttributeError: 'PostgresArchitectureRepository' object has no attribute 'grouped'`.

- [ ] **Step 3: Port**

Dans `ports.py` : `from ea.domain.analytics import Dimension, RawGroup` et sur `AnalysisRepository` :

```python
    async def grouped(
        self,
        dimensions: Sequence[Dimension],
        metric_key: str | None,
        *,
        scope: Sequence[UUID] | None = None,
    ) -> tuple[RawGroup, ...]:
        """Elements grouped by the raw value of each dimension, with partial sums of `metric_key`.

        Raw groups and not buckets: folding spellings together is
        `summarise`'s job, once, so this and the in-memory reference cannot
        disagree. `scope` narrows to those element ids; `None` is the whole
        catalogue.
        """
        ...
```

- [ ] **Step 4: SQL**

Dans `architecture_store.py` : ajouter aux imports SQLAlchemy `Numeric` et `from sqlalchemy import cast as sql_cast`; `from ea.domain.analytics import RAW_GROUP_CEILING, Dimension, DimensionKind, RawGroup`. Après `_text_of` :

```python
#: A read-only analysis that runs longer than this is cancelled by PostgreSQL,
#: local to its transaction. Its cancellation surfaces as a 500 `internal_error`
#: with the traceback in the logs (docs/adr/<ADR lot 3>).
ANALYSIS_TIMEOUT_MS: Final = 5000

#: What `read_number` tolerates, spelt for PostgreSQL: blanks (space, U+00A0, U+202F)
#: dropped, `,` read as `.`, then a plain decimal. Both are bound parameters.
_BLANKS: Final = "[   ]"
_NUMBER: Final = "^-?[0-9]+(\\.[0-9]+)?$"


def _raw_key(dimension: Dimension) -> ColumnElement[str]:
    match dimension.kind:
        case DimensionKind.ELEMENT_TYPE:
            return ElementRecord.element_type
        case DimensionKind.LAYER:
            return ElementRecord.layer
        case DimensionKind.PROPERTY:
            return func.coalesce(_text_of(dimension.key or ""), "")


def _number_of(key: str) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[Any]]:
    """(readable, present, the number) for a property read as a decimal.

    The cast sits behind the `CASE`, so an unreadable value is counted as such
    instead of aborting the whole query with `invalid input syntax`.
    """
    raw = func.coalesce(_text_of(key), "")
    normalised = func.replace(func.regexp_replace(raw, _BLANKS, "", "g"), ",", ".")
    readable = normalised.op("~", is_comparison=True)(_NUMBER)
    present = func.btrim(raw) != ""
    number = case((readable, sql_cast(normalised, Numeric)), else_=None)
    return readable, present, number
```

Méthode, après `links_touching` :

```python
    async def grouped(
        self,
        dimensions: Sequence[Dimension],
        metric_key: str | None,
        *,
        scope: Sequence[UUID] | None = None,
    ) -> tuple[RawGroup, ...]:
        columns: list[Any] = [
            *(_raw_key(dimension).label(f"k{index}") for index, dimension in enumerate(dimensions)),
            func.count().label("n"),
        ]
        if metric_key is not None:
            readable, present, number = _number_of(metric_key)
            columns += [
                func.count().filter(readable).label("valid"),
                func.coalesce(func.sum(number), 0).label("total"),
                func.min(number).label("low"),
                func.max(number).label("high"),
                func.count().filter(and_(present, not_(readable))).label("invalid"),
            ]
        statement = select(*columns)
        if scope is not None:
            statement = statement.where(ElementRecord.id.in_(list(scope)))
        # GROUP BY by ordinal: the same bound parameter would otherwise appear
        # twice under two numbers, and PostgreSQL then refuses the select list.
        statement = statement.group_by(
            *(literal_column(str(position + 1)) for position in range(len(dimensions)))
        ).limit(RAW_GROUP_CEILING)
        async with self._sessions.begin() as session:
            await session.execute(
                select(func.set_config("statement_timeout", str(ANALYSIS_TIMEOUT_MS), True))
            )
            rows = (await session.execute(statement)).all()
        if len(rows) == RAW_GROUP_CEILING:
            logger.warning(
                "analysis grouping reached its ceiling; the result is partial",
                extra={"action": "analysis_ceiling"},
            )
        return tuple(
            RawGroup(
                keys=tuple(str(getattr(row, f"k{index}")) for index in range(len(dimensions))),
                count=int(row.n),
                valid=int(row.valid) if metric_key is not None else 0,
                total=float(row.total) if metric_key is not None else 0.0,
                low=float(row.low) if metric_key is not None and row.low is not None else None,
                high=float(row.high) if metric_key is not None and row.high is not None else None,
                invalid=int(row.invalid) if metric_key is not None else 0,
            )
            for row in rows
        )
```

- [ ] **Step 5: Double en mémoire**

Dans `tests/conftest.py`, importer `from ea.domain.analytics import Dimension, RawGroup, group_elements` et, dans `InMemoryRepository`, après `links_touching` :

```python
    async def grouped(
        self,
        dimensions: Sequence[Dimension],
        metric_key: str | None,
        *,
        scope: Sequence[UUID] | None = None,
    ) -> tuple[RawGroup, ...]:
        inside = (
            self.elements.values()
            if scope is None
            else [self.elements[i] for i in scope if i in self.elements]
        )
        return group_elements(inside, dimensions, metric_key)
```

- [ ] **Step 6: Relancer, PASS**

Run: `make test-integration && cd backend && uv run mypy src migrations`
Expected: les tests d'intégration passent (5 paramétrés + 4) ; mypy Success.

- [ ] **Step 7: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(analytics): group elements in SQL, in agreement with the pure reference" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 23: `AnalysisService.aggregate` et `heatmap`

**Files:**
- Modify: `backend/src/ea/services/analysis.py`
- Modify: `backend/tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `AnalysisRepository.grouped`, `summarise`, `validate_question`
- Produces:
```python
async def aggregate(self, by: Dimension, metric: Metric, property_key: str | None = None) -> Aggregate
async def heatmap(self, rows: Dimension, cols: Dimension, metric: Metric, property_key: str | None = None) -> Aggregate
```

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `test_analysis_service.py` (imports : `from ea.domain.analytics import Dimension, Metric` et `InvalidAnalysisError`) :

```python
@pytest.mark.asyncio
class TestTheAggregates:
    async def a_model(self, service: ArchitectureService) -> None:
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing",
            properties={"cost": "1 200,50", "lifecycle": "en production"},
        )
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="CRM",
            properties={"cost": "300", "lifecycle": "retiré"},
        )
        await service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order", properties={"cost": "1 200 €"}
        )

    async def test_counting_by_layer(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert {b.keys: b.count for b in result.buckets} == {("application",): 2, ("business",): 1}

    async def test_summing_a_property_reports_what_it_could_not_read(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.aggregate(Dimension.parse("layer"), Metric.SUM, "cost")

        assert {b.keys: b.value for b in result.buckets} == {
            ("application",): 1500.5,
            ("business",): None,
        }
        assert result.unreadable == 1

    async def test_a_heat_map_crosses_two_dimensions(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.heatmap(
            Dimension.parse("layer"), Dimension.parse("property:lifecycle"), Metric.COUNT
        )

        assert {b.keys: b.count for b in result.buckets} == {
            ("application", "en production"): 1,
            ("application", "retiré"): 1,
            ("business", "__unset__"): 1,
        }

    async def test_a_sum_without_a_property_is_a_refused_question(
        self, analysis: AnalysisService
    ) -> None:
        with pytest.raises(InvalidAnalysisError, match="sum"):
            await analysis.aggregate(Dimension.parse("layer"), Metric.SUM)

    async def test_a_reader_may_ask(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        with acting_as(a_reader()):
            result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert result.total == 3

    async def test_an_empty_catalogue_is_an_empty_answer(self, analysis: AnalysisService) -> None:
        result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert (result.buckets, result.total) == ((), 0)

    async def test_nobody_asks_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py -x -k Aggregates`
Expected: FAIL — `AttributeError: 'AnalysisService' object has no attribute 'aggregate'`.

- [ ] **Step 3: Implémenter**

Dans `services/analysis.py`, imports : `from collections.abc import Sequence`, `from ea.domain.analytics import Aggregate, Dimension, Metric, summarise, validate_question`. Après `check` :

```python
    # --- Aggregates and heat maps -------------------------------------------

    async def aggregate(
        self, by: Dimension, metric: Metric, property_key: str | None = None
    ) -> Aggregate:
        """Elements grouped by one dimension, with a metric and a colour level per bucket."""
        require_caller()
        return await self._summary((by,), metric, property_key)

    async def heatmap(
        self,
        rows: Dimension,
        cols: Dimension,
        metric: Metric,
        property_key: str | None = None,
    ) -> Aggregate:
        """The same, grouped by two dimensions — the cells of a heat map."""
        require_caller()
        return await self._summary((rows, cols), metric, property_key)

    async def _summary(
        self, dimensions: Sequence[Dimension], metric: Metric, property_key: str | None
    ) -> Aggregate:
        key = validate_question(metric, property_key)
        groups = await self._repository.grouped(dimensions, key)
        return summarise(groups, dimensions, metric, key)
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py tests/unit/test_service_guards.py -x`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/analysis.py backend/tests/unit/test_analysis_service.py
git commit -m "feat(analytics): add the aggregate and heat-map use cases" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 24: API — `/analytics/aggregate` et `/analytics/heatmap`

**Files:**
- Modify: `backend/src/ea/api/schemas.py`, `backend/src/ea/api/analysis.py`, `backend/src/ea/api/errors.py`
- Modify: `backend/tests/e2e/test_analysis_api.py`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (par `make openapi`)

**Interfaces:**
- Consumes: `AnalysisService.aggregate/heatmap`
- Produces:
```
GET /analytics/aggregate?by=&metric=count&property=  -> AggregateRead
GET /analytics/heatmap?rows=&cols=&metric=count&property= -> HeatmapRead
```
```python
DimensionParam = Annotated[str, Field(pattern=DIMENSION_PATTERN)]
PropertyKey = Annotated[str, Field(pattern=PROPERTY_KEY_PATTERN)]
class BucketRead(BaseModel): key: str; count: int; value: float | None; level: int | None  # 0..4
class AggregateRead(BaseModel): by: str; metric: Metric; property_key: str | None; total: int; unreadable: int; truncated: bool; buckets: list[BucketRead]
class HeatCellRead(BaseModel): row: str; col: str; count: int; value: float | None; level: int | None
class HeatmapRead(BaseModel): rows_by: str; cols_by: str; rows: list[str]; cols: list[str]; metric: Metric; property_key: str | None; total: int; unreadable: int; truncated: bool; cells: list[HeatCellRead]
```
```ts
// components['schemas']['AggregateRead' | 'HeatmapRead' | 'BucketRead' | 'HeatCellRead' | 'Metric']
```

- [ ] **Step 1: Écrire les tests e2e qui échouent**

Ajouter à `test_analysis_api.py` :

```python
async def an_element_with(client: httpx.AsyncClient, element_type: str, name: str, **properties: str) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestTheAggregates:
    async def test_counting_by_layer_colours_each_bucket_with_a_level(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element(client, "node", "srv-01")
        await an_element(client, "node", "srv-02")
        await an_element(client, "business_process", "Order")

        body = (await client.get("/analytics/aggregate", params={"by": "layer"})).json()

        assert {b["key"]: b["count"] for b in body["buckets"]} == {"business": 1, "technology": 2}
        assert body["total"] == 3 and body["truncated"] is False
        assert all(0 <= b["level"] <= 4 for b in body["buckets"])
        assert next(b for b in body["buckets"] if b["key"] == "technology")["level"] > next(
            b for b in body["buckets"] if b["key"] == "business"
        )["level"]

    async def test_a_dirty_cost_is_summed_around_and_reported(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element_with(client, "node", "a", cost="1 200,50")
        await an_element_with(client, "node", "b", cost="300")
        await an_element_with(client, "node", "c", cost="1 200 €")

        body = (
            await client.get(
                "/analytics/aggregate", params={"by": "layer", "metric": "sum", "property": "cost"}
            )
        ).json()

        assert body["buckets"][0]["value"] == 1500.5
        assert body["unreadable"] == 1
        assert body["property_key"] == "cost"

    async def test_a_heat_map_is_a_grid_of_cells_with_ordered_axes(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element_with(client, "node", "a", lifecycle="retiré")
        await an_element_with(client, "node", "b", lifecycle="planifié")

        body = (
            await client.get(
                "/analytics/heatmap", params={"rows": "layer", "cols": "property:lifecycle"}
            )
        ).json()

        assert body["rows"] == ["technology"]
        assert body["cols"] == ["planifié", "retiré"]
        assert {(c["row"], c["col"], c["count"]) for c in body["cells"]} == {
            ("technology", "planifié", 1),
            ("technology", "retiré", 1),
        }

    async def test_an_empty_model_is_an_empty_answer(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/analytics/aggregate", params={"by": "layer"})).json()

        assert body["buckets"] == [] and body["total"] == 0

    @pytest.mark.parametrize(
        "params",
        [
            {"by": "bogus"},
            {"by": "property:"},
            {"by": "layer", "metric": "median"},
            {"by": "layer", "metric": "sum", "property": "cost'; --"},
        ],
    )
    async def test_a_malformed_question_is_a_422(
        self, client: httpx.AsyncClient, params: dict[str, str]
    ) -> None:
        assert (await client.get("/analytics/aggregate", params=params)).status_code == 422

    async def test_a_sum_without_a_property_is_a_typed_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/analytics/aggregate", params={"by": "layer", "metric": "sum"})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_analysis"

    async def test_no_token_is_a_401(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=StaticVerifier({}),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
            assert (await anonymous.get("/analytics/aggregate", params={"by": "layer"})).status_code == 401
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py -x -k Aggregates`
Expected: FAIL — 404 sur `/analytics/aggregate`.

- [ ] **Step 3: Implémenter**

`api/errors.py` : importer `InvalidAnalysisError` et ajouter `InvalidAnalysisError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_analysis"),`.

`api/schemas.py` : imports `from ea.domain.analytics import DIMENSION_PATTERN, MAX_BUCKETS, Aggregate, Metric` et `from ea.domain.model import PROPERTY_KEY_PATTERN, Element, Relationship` (compléter l'import existant). À la fin :

```python
# --- Aggregates and heat maps (docs/adr/<ADR lot 3>) -------------------------

DimensionParam = Annotated[
    str,
    Field(
        pattern=DIMENSION_PATTERN,
        description="`element_type`, `layer`, or `property:<key>` for an attribute of the element.",
    ),
]
PropertyKey = Annotated[
    str,
    Field(pattern=PROPERTY_KEY_PATTERN, description="The property the metric reads numbers from."),
]


class BucketRead(BaseModel):
    """One group. `key` is a value, or `__unset__` / `__invalid__` for a property dimension."""

    key: str
    count: int
    value: float | None = Field(description="The metric over the bucket; null if nothing was readable.")
    level: int | None = Field(description="0 (lowest) to 4, decided by the server; null if none.")


class AggregateRead(BaseModel):
    by: str
    metric: Metric
    property_key: str | None
    total: int
    unreadable: int = Field(description="Elements whose property is present but not a number.")
    truncated: bool = Field(description=f"True when more than {MAX_BUCKETS} buckets were cut.")
    buckets: list[BucketRead]

    @classmethod
    def of(cls, aggregate: Aggregate) -> AggregateRead:
        return cls(
            by=str(aggregate.dimensions[0]),
            metric=aggregate.metric,
            property_key=aggregate.property_key,
            total=aggregate.total,
            unreadable=aggregate.unreadable,
            truncated=aggregate.truncated,
            buckets=[
                BucketRead(key=b.keys[0], count=b.count, value=b.value, level=b.level)
                for b in aggregate.buckets
            ],
        )


class HeatCellRead(BaseModel):
    row: str
    col: str
    count: int
    value: float | None
    level: int | None


class HeatmapRead(BaseModel):
    rows_by: str
    cols_by: str
    rows: list[str] = Field(description="Row keys, in the order the dimension reads.")
    cols: list[str]
    metric: Metric
    property_key: str | None
    total: int
    unreadable: int
    truncated: bool
    cells: list[HeatCellRead]

    @classmethod
    def of(cls, aggregate: Aggregate) -> HeatmapRead:
        return cls(
            rows_by=str(aggregate.dimensions[0]),
            cols_by=str(aggregate.dimensions[1]),
            rows=list(aggregate.axis_keys(0)),
            cols=list(aggregate.axis_keys(1)),
            metric=aggregate.metric,
            property_key=aggregate.property_key,
            total=aggregate.total,
            unreadable=aggregate.unreadable,
            truncated=aggregate.truncated,
            cells=[
                HeatCellRead(
                    row=b.keys[0], col=b.keys[1], count=b.count, value=b.value, level=b.level
                )
                for b in aggregate.buckets
            ],
        )
```

`api/analysis.py` : importer `AggregateRead, DimensionParam, HeatmapRead, PropertyKey`, `from ea.domain.analytics import Dimension, Metric` ; puis :

```python
PropertyQuery = Annotated[PropertyKey | None, Query(alias="property")]
INVALID: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}
}


# --- Aggregates and heat maps ---------------------------------------------------


@router.get("/analytics/aggregate", response_model=AggregateRead, responses=INVALID)
async def read_aggregate(
    service: Analysis,
    by: Annotated[DimensionParam, Query()],
    metric: Annotated[Metric, Query()] = Metric.COUNT,
    property_key: PropertyQuery = None,
) -> AggregateRead:
    """Elements grouped by one dimension, each bucket with a count, a metric and a level.

    `sum`, `avg`, `min` and `max` read `property` as a number (`.` or `,`
    decimal, spaces as thousands separators); a value that does not read is
    counted in `unreadable`, never silently ignored. `level` (0 to 4) is the
    server's colour decision.
    """
    dimension = Dimension.parse(by)
    return AggregateRead.of(await service.aggregate(dimension, metric, property_key))


@router.get("/analytics/heatmap", response_model=HeatmapRead, responses=INVALID)
async def read_heatmap(
    service: Analysis,
    rows: Annotated[DimensionParam, Query()],
    cols: Annotated[DimensionParam, Query()],
    metric: Annotated[Metric, Query()] = Metric.COUNT,
    property_key: PropertyQuery = None,
) -> HeatmapRead:
    """The same, grouped by two dimensions: a grid of cells with a level each."""
    result = await service.heatmap(Dimension.parse(rows), Dimension.parse(cols), metric, property_key)
    return HeatmapRead.of(result)
```

- [ ] **Step 4: Relancer, PASS puis régénérer**

Run: `cd backend && uv run pytest tests/e2e/test_analysis_api.py tests/e2e/test_auth_api.py tests/unit/test_openapi.py -x`
Expected: tous passent.

Run: `make openapi && make openapi-check`
Expected: « Client généré à jour. »

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/api backend/tests/e2e/test_analysis_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(analytics): serve aggregates and heat maps over HTTP" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 25: MCP — l'outil `aggregate_model`

**Files:**
- Modify: `backend/src/ea/mcp/server.py`
- Modify: `backend/tests/unit/test_mcp_server.py`

**Interfaces:**
- Consumes: `AnalysisService.aggregate`, `AggregateRead`, `DimensionParam`, `PropertyKey`
- Produces: `aggregate_model(by: DimensionParam, metric: Metric = Metric.COUNT, property_key: PropertyKey | None = None) -> AggregateRead` (READS)

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `test_mcp_server.py` : ajouter `"aggregate_model",` à la liste complète ; ajouter à la paramétrisation des paramètres de requête `("aggregate_model", "by", "/analytics/aggregate", "by")` et `("aggregate_model", "property_key", "/analytics/aggregate", "property")` ; puis :

```python
@pytest.mark.asyncio
class TestAggregateModel:
    async def test_it_counts_by_layer(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "srv-01")
        await an_element(server, "business_process", "Order")

        result = await call(server, "aggregate_model", by="layer")

        assert {b["key"]: b["count"] for b in result["buckets"]} == {"business": 1, "technology": 1}

    async def test_it_sums_a_property_and_reports_the_unreadable(
        self, server: MCPServer[Any]
    ) -> None:
        for name, cost in (("a", "100"), ("b", "n/a")):
            await call(
                server, "create_element", element_type="node", name=name, properties={"cost": cost}
            )

        result = await call(server, "aggregate_model", by="layer", metric="sum", property_key="cost")

        assert result["buckets"][0]["value"] == 100.0
        assert result["unreadable"] == 1

    async def test_it_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "aggregate_model")

        assert tool.annotations is not None and tool.annotations.read_only_hint is True

    async def test_a_sum_without_a_property_says_what_is_missing(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="needs a property"):
            await call(server, "aggregate_model", by="layer", metric="sum")

    async def test_an_unknown_dimension_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "aggregate_model", by="bogus")
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — la liste d'outils ne contient pas `aggregate_model`.

- [ ] **Step 3: Implémenter**

Dans `server.py` : importer `AggregateRead`, `DimensionParam`, `PropertyKey` depuis `ea.api.schemas`, `from ea.domain.analytics import Dimension, Metric`. Après `check_model` :

```python
    @server.tool(annotations=READS)
    @speaking_plainly
    async def aggregate_model(
        by: DimensionParam,
        metric: Metric = Metric.COUNT,
        property_key: PropertyKey | None = None,
    ) -> AggregateRead:
        """Count or total the catalogue by a dimension: how many, how much, how risky.

        `by` is `element_type`, `layer`, or `property:<key>` (for instance
        `property:lifecycle` or `property:risk`). `metric` is `count`, or
        `sum` / `avg` / `min` / `max` of the numeric property `property_key`
        (for instance `cost`). A value that is not a readable number — "1 200 €"
        — is not ignored in silence: it is counted in `unreadable`. Each bucket
        carries a `level` from 0 to 4 for how high it ranks. This reads only.
        """
        return AggregateRead.of(
            await get_analysis().aggregate(Dimension.parse(by), metric, property_key)
        )
```

`INSTRUCTIONS`, après le paragraphe de `check_model` :

```
`aggregate_model` totals the catalogue by layer, element type or an attribute
such as `property:lifecycle`, optionally summing a numeric property like `cost`.
Prefer it to listing elements and adding them up yourself.\
```
(même règle de placement que pour `check_model` : le dernier paragraphe se termine par `\` avant `"""`.)

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/e2e/test_mcp_endpoint.py -x`
Expected: tous passent.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): add the read-only aggregate_model tool" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 26: La règle `unreadable_property`

**Files:**
- Modify: `backend/src/ea/domain/quality.py`
- Modify: `backend/tests/unit/test_quality.py`
- Modify: `backend/src/ea/mcp/server.py` (docstring de `check_model`)
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (par `make openapi`)
- Modify: `frontend/src/features/quality/labels.ts`, `frontend/tests/support/api.ts`

**Interfaces:**
- Consumes: `ANALYTIC_PROPERTIES`, `read_number`, `read_rank`
- Produces: `RuleCode.UNREADABLE_PROPERTY = "unreadable_property"` (info) ; `def check_readable_properties(elements: Sequence[Element]) -> list[Finding]` ; `run_checks` l'inclut.

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `test_quality.py`, ajouter `check_readable_properties` à l'import, puis :

```python
class TestReadableProperties:
    def test_a_cost_that_is_not_a_number_is_flagged_as_information(self) -> None:
        pricey = an_element(E.NODE, "srv", properties={"cost": "1 200 €"})

        findings = check_readable_properties([pricey])

        assert [(f.rule, f.severity, f.element_id) for f in findings] == [
            (RuleCode.UNREADABLE_PROPERTY, Severity.INFO, pricey.id)
        ]
        assert "cost" in findings[0].message and "1 200 €" in findings[0].message

    def test_a_tolerated_spelling_is_not_flagged(self) -> None:
        ok = an_element(E.NODE, "srv", properties={"cost": "1 200,50"})

        assert check_readable_properties([ok]) == []

    def test_a_risk_outside_the_enumeration_is_flagged(self) -> None:
        odd = an_element(E.NODE, "srv", properties={"risk": "inconnu"})

        assert len(check_readable_properties([odd])) == 1

    def test_an_absent_or_blank_value_is_not_unreadable(self) -> None:
        elements = [an_element(E.NODE, "a"), an_element(E.NODE, "b", properties={"cost": "  "})]

        assert check_readable_properties(elements) == []

    def test_a_key_the_catalogue_does_not_read_is_left_alone(self) -> None:
        assert check_readable_properties([an_element(E.NODE, "a", properties={"owner": "???"})]) == []

    def test_the_run_includes_it(self) -> None:
        pricey = an_element(E.NODE, "srv", properties={"cost": "n/a"}, description="d")

        report = run_checks([pricey], [], QualityParams())

        assert RuleCode.UNREADABLE_PROPERTY in {f.rule for f in report.findings}
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_quality.py -x -k Readable`
Expected: FAIL — `ImportError: cannot import name 'check_readable_properties'`.

- [ ] **Step 3: Implémenter**

Dans `quality.py` : `from ea.domain.analytics import ANALYTIC_PROPERTIES, ReadingKind, read_number, read_rank` ; ajouter `UNREADABLE_PROPERTY = "unreadable_property"` à `RuleCode` ; `RuleCode.UNREADABLE_PROPERTY: Severity.INFO,` à `SEVERITY_OF` ; la fonction :

```python
def check_readable_properties(elements: Sequence[Element]) -> list[Finding]:
    """Values of the keys the dashboards read that they cannot read.

    The dashboards count such a value as `unreadable` instead of using it; this
    rule says *which* element carries it, so it can be fixed at the source.
    Writing the value is never refused: validating on write is a later project.
    """
    findings: list[Finding] = []
    for element in elements:
        for key, reading in ANALYTIC_PROPERTIES.items():
            raw = element.properties.get(key, "")
            if not raw.strip(" "):
                continue
            if reading.kind is ReadingKind.NUMBER:
                readable = read_number(raw) is not None
                expected = "un nombre (ex. 1200 ou 1 200,50)"
            else:
                readable = read_rank(key, raw) is not None
                expected = f"l'une de : {', '.join(reading.order)}"
            if not readable:
                findings.append(
                    _finding(
                        RuleCode.UNREADABLE_PROPERTY,
                        element,
                        f"« {element.name} » : la valeur « {raw.strip()} » de « {key} » "
                        f"n'est pas lisible ; attendu : {expected}.",
                    )
                )
    return findings
```
et, dans `run_checks`, ajouter `*check_readable_properties(elements),` après `check_descriptions`.

Dans `server.py`, docstring de `check_model` : ajouter `unreadable_property` à la liste de codes (`... `inconsistent_relationship`, `unreadable_property`)`).

- [ ] **Step 4: Côté SPA**

`frontend/src/features/quality/labels.ts` : ajouter `unreadable_property: 'Valeur illisible',` à `RULE_LABELS`. `frontend/tests/support/api.ts`, dans `aQualityReport` : ajouter `'unreadable_property'` à `rules` et `unreadable_property: 'info'` à `severity`.

- [ ] **Step 5: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_quality.py tests/unit/test_mcp_server.py -x && cd .. && make openapi && cd frontend && npm test -- --run tests/QualitySection.spec.ts tests/useQuality.spec.ts && npm run typecheck`
Expected: tous passent ; `schema.d.ts` contient `unreadable_property` dans `RuleCode`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests backend/openapi.json frontend/src frontend/tests
git commit -m "feat(quality): flag property values the dashboards cannot read" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 27: `lib/heatmapLayout.ts` — la géométrie, pure

**Files:**
- Create: `frontend/src/lib/heatmapLayout.ts`
- Create: `frontend/tests/heatmapLayout.spec.ts`

**Interfaces:**
- Consumes: —
- Produces:
```ts
export const CELL_WIDTH = 96, CELL_HEIGHT = 44, HEADER_WIDTH = 150, HEADER_HEIGHT = 56, GAP = 2, PADDING = 8
export type HeatCell = { row: string; col: string; count: number; value: number | null; level: number | null }
export type HeatRect = { row: string; col: string; x: number; y: number; width: number; height: number; count: number; value: number | null; level: number | null }
export type HeatLayout = { width: number; height: number; viewBox: string; rects: HeatRect[]; rowHeaders: { key: string; y: number }[]; colHeaders: { key: string; x: number }[] }
export function layoutHeatmap(rows: string[], cols: string[], cells: HeatCell[]): HeatLayout
export function legendLevels(cells: HeatCell[]): number[]
```

- [ ] **Step 1: Écrire les tests qui échouent**

`frontend/tests/heatmapLayout.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import {
  CELL_HEIGHT,
  CELL_WIDTH,
  GAP,
  HEADER_HEIGHT,
  HEADER_WIDTH,
  PADDING,
  layoutHeatmap,
  legendLevels,
  type HeatCell,
} from '../src/lib/heatmapLayout'

const cell = (row: string, col: string, count: number, level: number | null): HeatCell => ({
  row,
  col,
  count,
  value: count,
  level,
})

describe('layoutHeatmap', () => {
  it('puts one rect per row and column, row by row', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y', 'z'], [cell('a', 'x', 1, 0)])

    expect(layout.rects).toHaveLength(6)
    expect(layout.rects.map((rect) => `${rect.row}/${rect.col}`)).toEqual([
      'a/x', 'a/y', 'a/z', 'b/x', 'b/y', 'b/z',
    ])
  })

  it('places a rect after the headers, one cell and one gap further each step', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y'], [])
    const [first, second, third] = layout.rects

    expect(first).toMatchObject({ x: PADDING + HEADER_WIDTH, y: PADDING + HEADER_HEIGHT })
    expect(second.x - first.x).toBe(CELL_WIDTH + GAP)
    expect(third.y - first.y).toBe(CELL_HEIGHT + GAP)
  })

  it('carries the count, value and level of a known cell and none for a missing one', () => {
    const layout = layoutHeatmap(['a'], ['x', 'y'], [cell('a', 'x', 7, 3)])

    expect(layout.rects[0]).toMatchObject({ count: 7, value: 7, level: 3 })
    expect(layout.rects[1]).toMatchObject({ count: 0, value: null, level: null })
  })

  it('sizes the canvas to its content and says so in the viewBox', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y', 'z'], [])

    expect(layout.width).toBe(PADDING * 2 + HEADER_WIDTH + 3 * CELL_WIDTH + 2 * GAP)
    expect(layout.height).toBe(PADDING * 2 + HEADER_HEIGHT + 2 * CELL_HEIGHT + GAP)
    expect(layout.viewBox).toBe(`0 0 ${layout.width} ${layout.height}`)
  })

  it('lines the headers up with their rects', () => {
    const layout = layoutHeatmap(['a', 'b'], ['x', 'y'], [])

    expect(layout.colHeaders.map((header) => header.x)).toEqual(
      layout.rects.slice(0, 2).map((rect) => rect.x),
    )
    expect(layout.rowHeaders.map((header) => header.y)).toEqual([
      layout.rects[0].y,
      layout.rects[2].y,
    ])
  })

  it('lays out nothing, but a valid canvas, for an empty grid', () => {
    const layout = layoutHeatmap([], [], [])

    expect(layout.rects).toEqual([])
    expect(layout.width).toBeGreaterThan(0)
    expect(layout.height).toBeGreaterThan(0)
  })

  it('always draws the same picture for the same grid', () => {
    const cells = [cell('a', 'x', 2, 1)]

    expect(layoutHeatmap(['a'], ['x'], cells)).toEqual(layoutHeatmap(['a'], ['x'], cells))
  })
})

describe('legendLevels', () => {
  it('lists the distinct levels actually received, lowest first', () => {
    expect(legendLevels([cell('a', 'x', 1, 3), cell('a', 'y', 1, 0), cell('b', 'x', 1, 3)])).toEqual([0, 3])
  })

  it('leaves out cells with no level', () => {
    expect(legendLevels([cell('a', 'x', 0, null)])).toEqual([])
  })
})
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/heatmapLayout.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/lib/heatmapLayout"`.

- [ ] **Step 3: Implémenter**

`frontend/src/lib/heatmapLayout.ts` :

```ts
// The geometry of the heat map: a grid of rectangles with headers.
//
// Pure and deterministic, like `graphLayout.ts`: the same grid always draws the
// same picture, and it is tested without mounting anything. There is no chart
// library behind it — a grid of rectangles does not justify one (docs/adr/<ADR
// lot 3>). Colours are not here either: the server decides a `level`, and
// `features/dashboard/presentation.ts` maps it to a swatch.

export const CELL_WIDTH = 96
export const CELL_HEIGHT = 44
export const HEADER_WIDTH = 150
export const HEADER_HEIGHT = 56
export const GAP = 2
export const PADDING = 8

export type HeatCell = {
  row: string
  col: string
  count: number
  value: number | null
  level: number | null
}

export type HeatRect = {
  row: string
  col: string
  x: number
  y: number
  width: number
  height: number
  count: number
  value: number | null
  level: number | null
}

export type HeatLayout = {
  width: number
  height: number
  viewBox: string
  rects: HeatRect[]
  rowHeaders: { key: string; y: number }[]
  colHeaders: { key: string; x: number }[]
}

export function layoutHeatmap(rows: string[], cols: string[], cells: HeatCell[]): HeatLayout {
  const known = new Map(cells.map((entry) => [`${entry.row}\u0000${entry.col}`, entry]))
  const left = PADDING + HEADER_WIDTH
  const top = PADDING + HEADER_HEIGHT
  const columnX = (index: number) => left + index * (CELL_WIDTH + GAP)
  const rowY = (index: number) => top + index * (CELL_HEIGHT + GAP)

  const rects: HeatRect[] = rows.flatMap((row, r) =>
    cols.map((col, c) => {
      const found = known.get(`${row}\u0000${col}`)
      return {
        row,
        col,
        x: columnX(c),
        y: rowY(r),
        width: CELL_WIDTH,
        height: CELL_HEIGHT,
        count: found?.count ?? 0,
        value: found?.value ?? null,
        level: found?.level ?? null,
      }
    }),
  )

  const width = PADDING * 2 + HEADER_WIDTH + cols.length * CELL_WIDTH + Math.max(0, cols.length - 1) * GAP
  const height = PADDING * 2 + HEADER_HEIGHT + rows.length * CELL_HEIGHT + Math.max(0, rows.length - 1) * GAP

  return {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    rects,
    rowHeaders: rows.map((key, index) => ({ key, y: rowY(index) })),
    colHeaders: cols.map((key, index) => ({ key, x: columnX(index) })),
  }
}

/** The levels actually present, lowest first: the legend shows what the picture uses. */
export function legendLevels(cells: HeatCell[]): number[] {
  const levels = new Set<number>()
  for (const entry of cells) {
    if (entry.level !== null) {
      levels.add(entry.level)
    }
  }
  return [...levels].sort((one, other) => one - other)
}
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/heatmapLayout.spec.ts && npm run typecheck && npm run lint`
Expected: 9 passed, aucune erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/heatmapLayout.ts frontend/tests/heatmapLayout.spec.ts
git commit -m "feat(dashboard): add the pure heat-map layout" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 28: SPA — le tableau de bord, la carte de chaleur, les filtres d'URL du catalogue

**Files:**
- Create: `frontend/src/features/dashboard/presentation.ts`
- Create: `frontend/src/features/dashboard/useDashboard.ts`
- Create: `frontend/src/features/dashboard/HeatmapChart.vue`
- Create: `frontend/src/features/dashboard/DashboardSection.vue`
- Create: `frontend/tests/useDashboard.spec.ts`, `frontend/tests/DashboardSection.spec.ts`
- Modify: `frontend/src/features/elements/ElementCatalogue.vue` (`onMounted`, ligne ~63)
- Modify: `frontend/tests/ElementCatalogue.spec.ts`, `frontend/tests/support/api.ts` (`anAggregate`, `aHeatmap`)
- Modify: `frontend/src/router/sections.ts`

**Interfaces:**
- Consumes: `AggregateRead`, `HeatmapRead`, `layoutHeatmap`, `legendLevels`, `LAYER_LABELS`, `RELATIONSHIP...` non
- Produces:
```ts
// presentation.ts
export const LEVEL_FILLS: readonly string[]  // 5 swatches, light to dark
export const LEVEL_INKS: readonly string[]   // the ink on each, never var(--text)
export const NO_LEVEL: { fill: string; ink: string }
export function swatchOf(level: number | null): { fill: string; ink: string }
export function keyLabel(key: string): string          // __unset__ → 'Non renseigné', __invalid__ → 'Valeur illisible', layer keys through LAYER_LABELS
export function catalogueQuery(rowsBy: string, row: string, colsBy: string, col: string): Record<string, string> | null
// useDashboard.ts
export type DashboardQuestion = { by: string; cols: string; metric: Metric; property: string }
export function useDashboard(): { tiles: Ref<AggregateRead | null>; heatmap: Ref<HeatmapRead | null>; status; error; load(question: DashboardQuestion): Promise<void> }
// route: { path: '/analyse/tableau-de-bord', name: 'dashboard', label: 'Tableau de bord', group: 'analysis' }, query ?by= ?cols= ?metric= ?property=
```

- [ ] **Step 1: Fabriques de test**

Dans `tests/support/api.ts` (types `AggregateRead`, `HeatmapRead`) :

```ts
/** An aggregate as `GET /analytics/aggregate` returns it. */
export function anAggregate(overrides: Partial<AggregateRead> = {}): AggregateRead {
  return {
    by: 'layer',
    metric: 'count',
    property_key: null,
    total: 3,
    unreadable: 0,
    truncated: false,
    buckets: [
      { key: 'business', count: 1, value: 1, level: 0 },
      { key: 'technology', count: 2, value: 2, level: 4 },
    ],
    ...overrides,
  }
}

/** A heat map as `GET /analytics/heatmap` returns it. */
export function aHeatmap(overrides: Partial<HeatmapRead> = {}): HeatmapRead {
  return {
    rows_by: 'layer',
    cols_by: 'property:lifecycle',
    rows: ['business', 'technology'],
    cols: ['en production', 'retiré', '__unset__'],
    metric: 'count',
    property_key: null,
    total: 3,
    unreadable: 0,
    truncated: false,
    cells: [
      { row: 'business', col: 'retiré', count: 1, value: 1, level: 0 },
      { row: 'technology', col: 'en production', count: 2, value: 2, level: 4 },
    ],
    ...overrides,
  }
}
```

- [ ] **Step 2: Écrire les tests qui échouent**

`frontend/tests/useDashboard.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useDashboard } from '../src/features/dashboard/useDashboard'
import { aHeatmap, anAggregate, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ASK = { by: 'layer', cols: 'property:lifecycle', metric: 'count', property: '' } as const

describe('useDashboard', () => {
  it('asks for the tiles and the heat map with the question given', async () => {
    const calls = stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', body: aHeatmap() },
    ])
    const dashboard = useDashboard()

    await dashboard.load(ASK)

    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('rows')).toBe('layer')
    expect(heat?.url.searchParams.get('cols')).toBe('property:lifecycle')
    expect(heat?.url.searchParams.has('property')).toBe(false)
    expect(dashboard.tiles.value?.total).toBe(3)
    expect(dashboard.heatmap.value?.cells).toHaveLength(2)
  })

  it('sends the property only when a metric reads one', async () => {
    const calls = stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', body: aHeatmap() },
    ])
    const dashboard = useDashboard()

    await dashboard.load({ ...ASK, metric: 'sum', property: 'cost' })

    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('metric')).toBe('sum')
    expect(heat?.url.searchParams.get('property')).toBe('cost')
  })

  it('reports a refusal in words and clears the pictures', async () => {
    stubApi([
      { path: '/analytics/aggregate', body: anAggregate() },
      { path: '/analytics/heatmap', status: 422, body: { error: 'invalid_analysis', detail: 'dimension inconnue' } },
    ])
    const dashboard = useDashboard()

    await dashboard.load(ASK)

    expect(dashboard.error.value).toBe('dimension inconnue')
    expect(dashboard.heatmap.value).toBeNull()
  })
})
```

`frontend/tests/DashboardSection.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import DashboardSection from '../src/features/dashboard/DashboardSection.vue'
import { catalogueQuery, keyLabel, swatchOf } from '../src/features/dashboard/presentation'
import { createAppRouter } from '../src/router'
import { aHeatmap, anAggregate, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const TILES: Route = { path: '/analytics/aggregate', body: anAggregate() }
const HEAT: Route = { path: '/analytics/heatmap', body: aHeatmap() }

async function open(routes: Route[] = [TILES, HEAT], query = '') {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/tableau-de-bord${query}`)
  await router.isReady()
  const view = render(DashboardSection, { global: { plugins: [router] } })
  return { calls, router, ...view }
}

describe('the presentation tables', () => {
  it('maps a level to a swatch with its own ink, and no level to a neutral one', () => {
    expect(swatchOf(0).fill).not.toBe(swatchOf(4).fill)
    expect(swatchOf(4).ink).not.toBe(swatchOf(0).ink)
    expect(swatchOf(null).fill).toBeTruthy()
  })

  it('names the sentinel buckets and translates a layer', () => {
    expect(keyLabel('__unset__')).toBe('Non renseigné')
    expect(keyLabel('__invalid__')).toBe('Valeur illisible')
    expect(keyLabel('business')).toBe('Métier')
    expect(keyLabel('en production')).toBe('en production')
  })

  it('links a cell to the catalogue by the dimension the catalogue can filter on', () => {
    expect(catalogueQuery('layer', 'business', 'property:lifecycle', 'retiré')).toEqual({ layer: 'business' })
    expect(catalogueQuery('property:risk', 'élevé', 'element_type', 'node')).toEqual({ type: 'node' })
    expect(catalogueQuery('property:risk', 'élevé', 'property:lifecycle', 'retiré')).toBeNull()
    expect(catalogueQuery('layer', '__unset__', 'property:lifecycle', 'retiré')).toBeNull()
  })
})

describe('DashboardSection', () => {
  it('shows a tile per bucket with the total', async () => {
    await open()

    const tiles = within(await screen.findByRole('list', { name: /répartition/i }))
    expect(tiles.getByText('Métier')).toBeInTheDocument()
    expect(tiles.getByText('Technologie')).toBeInTheDocument()
    expect(tiles.getByText(/3 éléments/)).toBeInTheDocument()
  })

  it('draws the heat map as rectangles with the value printed in the cell', async () => {
    const { container } = await open()
    await screen.findByRole('img', { name: /carte de chaleur/i })

    expect(container.querySelectorAll('svg rect').length).toBeGreaterThanOrEqual(6)
    const printed = within(screen.getByRole('img', { name: /carte de chaleur/i })).getAllByText('2')
    expect(printed.length).toBeGreaterThan(0)
  })

  it('titles each cell for the tooltip with its row, column and count', async () => {
    await open()

    expect(await screen.findByTitle(/Technologie.*en production.*2/)).toBeInTheDocument()
  })

  it('links a cell to the catalogue filtered on its layer', async () => {
    await open()

    const link = (await screen.findByTitle(/Technologie.*en production/)).closest('a')
    expect(link).toHaveAttribute('href', '/elements?layer=technology')
  })

  it('labels the sentinel columns and draws a legend of the levels received', async () => {
    await open()

    expect(await screen.findByText('Non renseigné')).toBeInTheDocument()
    const legend = screen.getByRole('list', { name: /légende/i })
    expect(within(legend).getAllByRole('listitem')).toHaveLength(3) // levels 0 and 4, and "no data"
  })

  it('warns when values were unreadable, and says where to look', async () => {
    await open([TILES, { path: '/analytics/heatmap', body: aHeatmap({ unreadable: 2 }) }])

    const warning = await screen.findByRole('status')
    expect(warning).toHaveTextContent(/2 valeurs illisibles/)
    expect(within(warning).getByRole('link')).toHaveAttribute('href', expect.stringContaining('code=unreadable_property'))
  })

  it('sends the question from the URL', async () => {
    const { calls } = await open(
      [TILES, HEAT],
      '?by=element_type&cols=property:risk&metric=sum&property=cost',
    )

    await screen.findByRole('img', { name: /carte de chaleur/i })
    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('rows')).toBe('element_type')
    expect(heat?.url.searchParams.get('cols')).toBe('property:risk')
    expect(heat?.url.searchParams.get('property')).toBe('cost')
  })

  it('writes a changed dimension into the URL', async () => {
    const { router } = await open()
    await screen.findByRole('img', { name: /carte de chaleur/i })

    await fireEvent.update(screen.getByLabelText(/lignes/i), 'element_type')

    await waitFor(() => expect(router.currentRoute.value.query.by).toBe('element_type'))
  })

  it('says the model is empty rather than drawing an empty grid', async () => {
    await open([
      { path: '/analytics/aggregate', body: anAggregate({ total: 0, buckets: [] }) },
      { path: '/analytics/heatmap', body: aHeatmap({ rows: [], cols: [], cells: [], total: 0 }) },
    ])

    expect(await screen.findByText(/aucun élément à analyser/i)).toBeInTheDocument()
  })

  it('shows a refusal in words', async () => {
    await open([TILES, { path: '/analytics/heatmap', status: 422, body: { error: 'invalid_analysis', detail: 'dimension inconnue' } }])

    expect(await screen.findByRole('alert')).toHaveTextContent('dimension inconnue')
  })
})
```

Dans `tests/ElementCatalogue.spec.ts`, ajouter :

```ts
  it('starts filtered when the URL names a layer or a type', async () => {
    const { calls } = await renderCatalogue(
      [{ path: '/elements', body: aPage([anElement()]) }],
      '?layer=business&type=business_process',
    )

    await screen.findByRole('row', { name: /Facturation/ })
    const listing = calls.find((call) => call.url.pathname === '/elements')
    expect(listing?.url.searchParams.get('layer')).toBe('business')
    expect(listing?.url.searchParams.get('element_type')).toBe('business_process')
  })
```
(à placer dans le `describe('ElementCatalogue', ...)`.)

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/useDashboard.spec.ts tests/DashboardSection.spec.ts tests/ElementCatalogue.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/dashboard/useDashboard"` ; le test du catalogue échoue aussi (aucun `layer=` dans la requête).

- [ ] **Step 4: Le catalogue lit ses filtres**

Dans `ElementCatalogue.vue`, importer les types (`import type { ElementCreate, ElementRead, ElementUpdate, ElementType, Layer } from './useElementCatalogue'` — `ElementType` et `Layer` y sont exportés) et remplacer le `onMounted` par :

```ts
/** A query parameter is `string | string[] | null`; only a string means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

onMounted(async () => {
  // A link from the dashboard arrives with `?layer=` / `?type=`: start filtered.
  // Values are passed as they stand; the API refuses a layer or type it does not know.
  catalogue.filters.layer = one(route.query.layer) as Layer | ''
  catalogue.filters.elementType = one(route.query.type) as ElementType | ''
  await Promise.all([metamodel.load(), catalogue.load()])
})
```

- [ ] **Step 5: Présentation et composable**

`frontend/src/features/dashboard/presentation.ts` :

```ts
// How the dashboard reads what the server decided: a `level` becomes a swatch,
// a sentinel key becomes a phrase. Presentation only — no threshold, no rule.
// The ink of each swatch is stated, never `var(--text)`, which inverts in the
// dark theme and would put pale text on a pale fill (as `BOX_TEXT` does for
// the layer colours).
import { LAYER_LABELS, type Layer } from '../metamodel/useMetamodel'

/** Five steps of one hue, light to dark: the level is also printed in the cell. */
export const LEVEL_FILLS = ['#e3eefa', '#bad6f0', '#86b6e0', '#4a86c5', '#1f5496'] as const
export const LEVEL_INKS = ['#24202b', '#24202b', '#24202b', '#ffffff', '#ffffff'] as const
export const NO_LEVEL = { fill: '#f1f0f3', ink: '#24202b' } as const

export function swatchOf(level: number | null): { fill: string; ink: string } {
  if (level === null || level < 0 || level >= LEVEL_FILLS.length) {
    return NO_LEVEL
  }
  return { fill: LEVEL_FILLS[level], ink: LEVEL_INKS[level] }
}

const UNSET = '__unset__'
const INVALID = '__invalid__'

/** A bucket key as a person reads it. Layer keys go through the metamodel's labels. */
export function keyLabel(key: string): string {
  if (key === UNSET) {
    return 'Non renseigné'
  }
  if (key === INVALID) {
    return 'Valeur illisible'
  }
  return (LAYER_LABELS as Record<string, string>)[key] ?? key
}

/**
 * The catalogue filter a cell stands for, or null when none applies.
 *
 * The catalogue filters on a layer and on an element type, so a cell links
 * there when one of its two dimensions is one of those and its key is a real
 * value (not a sentinel).
 */
export function catalogueQuery(
  rowsBy: string,
  row: string,
  colsBy: string,
  col: string,
): Record<string, string> | null {
  const query: Record<string, string> = {}
  for (const [by, key] of [
    [rowsBy, row],
    [colsBy, col],
  ] as const) {
    if (key === UNSET || key === INVALID) {
      continue
    }
    if (by === 'layer') {
      query.layer = key
    } else if (by === 'element_type') {
      query.type = key
    }
  }
  return Object.keys(query).length > 0 ? query : null
}

export type { Layer }
```

`frontend/src/features/dashboard/useDashboard.ts` :

```ts
// The dashboard's state: the tiles (a count per bucket) and the heat map, two
// answers to one question. Each change of the question aborts the previous
// pair (lib/latest.ts), so the pictures always answer what the URL says now.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type AggregateRead = components['schemas']['AggregateRead']
export type HeatmapRead = components['schemas']['HeatmapRead']
export type Metric = components['schemas']['Metric']

export type DashboardQuestion = {
  /** The rows dimension: `layer`, `element_type` or `property:<key>`. */
  by: string
  /** The columns dimension. */
  cols: string
  metric: Metric
  /** The property a metric other than `count` reads; '' for none. */
  property: string
}

export function useDashboard() {
  const tiles = ref<AggregateRead | null>(null)
  const heatmap = ref<HeatmapRead | null>(null)
  const question = useLatestRequest()

  async function load(asked: DashboardQuestion): Promise<void> {
    // The property is left out for a count: an unused `property=` would be
    // validated for nothing, and a blank one is not a key.
    const property =
      asked.metric !== 'count' && asked.property ? { property: asked.property } : {}
    await question.run(
      async (signal) => {
        const [counted, crossed] = await Promise.all([
          api.GET('/analytics/aggregate', {
            params: { query: { by: asked.by, metric: 'count' } },
            signal,
          }),
          api.GET('/analytics/heatmap', {
            params: {
              query: { rows: asked.by, cols: asked.cols, metric: asked.metric, ...property },
            },
            signal,
          }),
        ])
        return { counted: unwrap(counted), crossed: unwrap(crossed) }
      },
      (answer) => {
        tiles.value = answer.counted
        heatmap.value = answer.crossed
      },
      () => {
        tiles.value = null
        heatmap.value = null
      },
    )
  }

  return { tiles, heatmap, status: question.status, error: question.error, load }
}
```

- [ ] **Step 6: La carte et la section**

`frontend/src/features/dashboard/HeatmapChart.vue` :

```vue
<script setup lang="ts">
// The heat map: rectangles, a printed value in each, a tooltip, a legend.
//
// Hand-drawn SVG over `lib/heatmapLayout.ts`. A cell is an `<a>` when it
// stands for something the catalogue can filter on, and its value is printed
// in the cell on top of the tint, so the picture does not depend on colour
// alone. Colours come from the server's `level`, mapped by `presentation.ts`.
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { layoutHeatmap, legendLevels } from '../../lib/heatmapLayout'
import { CELL_HEIGHT, CELL_WIDTH, HEADER_HEIGHT, HEADER_WIDTH } from '../../lib/heatmapLayout'
import { catalogueQuery, keyLabel, swatchOf } from './presentation'
import type { HeatmapRead } from './useDashboard'

const props = defineProps<{ heatmap: HeatmapRead }>()
const router = useRouter()

const layout = computed(() => layoutHeatmap(props.heatmap.rows, props.heatmap.cols, props.heatmap.cells))
const levels = computed(() => legendLevels(props.heatmap.cells))

function shown(value: number | null, count: number): string {
  if (value === null) {
    return count === 0 ? '' : '—'
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function tooltip(row: string, col: string, count: number, value: number | null): string {
  const base = `${keyLabel(row)} × ${keyLabel(col)} : ${count} élément${count > 1 ? 's' : ''}`
  return props.heatmap.metric === 'count' || value === null
    ? base
    : `${base} — ${props.heatmap.metric} = ${shown(value, count)}`
}

function hrefOf(row: string, col: string): string | null {
  const query = catalogueQuery(props.heatmap.rows_by, row, props.heatmap.cols_by, col)
  return query ? router.resolve({ path: '/elements', query }).href : null
}

function go(event: MouseEvent, href: string): void {
  event.preventDefault()
  void router.push(href)
}
</script>

<template>
  <figure class="heatmap">
    <svg
      role="img"
      aria-label="Carte de chaleur"
      :viewBox="layout.viewBox"
      :width="layout.width"
      :height="layout.height"
    >
      <text
        v-for="header in layout.colHeaders"
        :key="`c-${header.key}`"
        :x="header.x + CELL_WIDTH / 2"
        :y="HEADER_HEIGHT - 4"
        text-anchor="middle"
        class="heatmap__header"
      >
        {{ keyLabel(header.key) }}
      </text>
      <text
        v-for="header in layout.rowHeaders"
        :key="`r-${header.key}`"
        :x="HEADER_WIDTH - 4"
        :y="header.y + CELL_HEIGHT / 2 + 4"
        text-anchor="end"
        class="heatmap__header"
      >
        {{ keyLabel(header.key) }}
      </text>
      <template v-for="rect in layout.rects" :key="`${rect.row}/${rect.col}`">
        <component
          :is="hrefOf(rect.row, rect.col) ? 'a' : 'g'"
          :href="hrefOf(rect.row, rect.col) ?? undefined"
          @click="hrefOf(rect.row, rect.col) && go($event, hrefOf(rect.row, rect.col) as string)"
        >
          <title>{{ tooltip(rect.row, rect.col, rect.count, rect.value) }}</title>
          <rect
            :x="rect.x"
            :y="rect.y"
            :width="rect.width"
            :height="rect.height"
            :fill="swatchOf(rect.level).fill"
            rx="3"
          />
          <text
            :x="rect.x + rect.width / 2"
            :y="rect.y + rect.height / 2 + 5"
            text-anchor="middle"
            :fill="swatchOf(rect.level).ink"
            class="heatmap__value"
          >
            {{ shown(rect.value, rect.count) }}
          </text>
        </component>
      </template>
    </svg>
    <figcaption>
      <ul class="heatmap__legend" aria-label="Légende des niveaux">
        <li v-for="level in levels" :key="level">
          <span class="heatmap__swatch" :style="{ background: swatchOf(level).fill }"></span>
          niveau {{ level + 1 }} sur 5
        </li>
        <li>
          <span class="heatmap__swatch" :style="{ background: swatchOf(null).fill }"></span>
          pas de donnée
        </li>
      </ul>
    </figcaption>
  </figure>
</template>

<style scoped>
.heatmap__header {
  font-size: 12px;
  fill: var(--text, #24202b);
}
.heatmap__value {
  font-size: 14px;
  font-weight: 600;
}
.heatmap__legend {
  display: flex;
  gap: 1rem;
  list-style: none;
  padding: 0;
}
.heatmap__swatch {
  display: inline-block;
  width: 1rem;
  height: 1rem;
  margin-right: 0.25rem;
  border: 1px solid #8884;
  vertical-align: middle;
}
</style>
```

`frontend/src/features/dashboard/DashboardSection.vue` :

```vue
<script setup lang="ts">
// The `/analyse/tableau-de-bord` section: how the model is spread, and where it is
// worn — a count per layer, and a heat map crossing two dimensions.
//
// The question lives in the URL (`?by=`, `?cols=`, `?metric=`, `?property=`):
// a view of "layer × lifecycle" is a link one can send. Every number and every
// colour level comes from the API; this screen decides nothing.
import { computed, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import HeatmapChart from './HeatmapChart.vue'
import { keyLabel } from './presentation'
import { useDashboard, type DashboardQuestion, type Metric } from './useDashboard'

const route = useRoute()
const router = useRouter()
const dashboard = useDashboard()

function one(value: unknown, fallback: string): string {
  return typeof value === 'string' && value ? value : fallback
}

const question = computed<DashboardQuestion>(() => ({
  by: one(route.query.by, 'layer'),
  cols: one(route.query.cols, 'property:lifecycle'),
  // Passed on as it stands: the API knows the metrics and refuses any other.
  metric: one(route.query.metric, 'count') as Metric,
  property: one(route.query.property, ''),
}))

watch(
  question,
  async (asked) => {
    await dashboard.load(asked)
  },
  { immediate: true },
)

function ask(changes: Record<string, string | undefined>): void {
  void router.replace({ query: { ...route.query, ...changes } })
}

function onChange(name: string, event: Event): void {
  ask({ [name]: (event.target as HTMLSelectElement | HTMLInputElement).value || undefined })
}

const empty = computed(() => dashboard.tiles.value?.total === 0)
const unreadable = computed(() => dashboard.heatmap.value?.unreadable ?? 0)
</script>

<template>
  <section class="section">
    <header>
      <h2>Tableau de bord</h2>
      <p class="hint">Comment le modèle se répartit, et où il vieillit ou coûte.</p>
    </header>

    <form class="controls" aria-label="Question" @submit.prevent>
      <div class="field">
        <label for="dash-by">Lignes</label>
        <select id="dash-by" :value="question.by" @change="onChange('by', $event)">
          <option value="layer">Couche</option>
          <option value="element_type">Type d'élément</option>
        </select>
      </div>
      <div class="field">
        <label for="dash-cols">Colonnes</label>
        <input id="dash-cols" :value="question.cols" placeholder="property:lifecycle"
               @change="onChange('cols', $event)" />
      </div>
      <div class="field">
        <label for="dash-metric">Mesure</label>
        <select id="dash-metric" :value="question.metric" @change="onChange('metric', $event)">
          <option value="count">Nombre</option>
          <option value="sum">Somme</option>
          <option value="avg">Moyenne</option>
          <option value="min">Minimum</option>
          <option value="max">Maximum</option>
        </select>
      </div>
      <div v-if="question.metric !== 'count'" class="field">
        <label for="dash-property">Propriété</label>
        <input id="dash-property" :value="question.property" placeholder="cost"
               @change="onChange('property', $event)" />
      </div>
    </form>

    <p v-if="dashboard.status.value === 'error'" class="banner banner--error" role="alert">
      {{ dashboard.error.value }}
    </p>
    <p v-else-if="dashboard.status.value === 'loading' && !dashboard.tiles.value" class="hint">
      Calcul en cours…
    </p>

    <template v-if="dashboard.tiles.value && dashboard.heatmap.value">
      <p v-if="empty" class="hint">Aucun élément à analyser.</p>
      <template v-else>
        <p v-if="unreadable > 0" class="banner" role="status">
          {{ unreadable }} valeurs illisibles ont été laissées de côté dans le calcul.
          <RouterLink :to="{ path: '/analyse/qualite', query: { code: 'unreadable_property' } }">
            Voir lesquelles
          </RouterLink>
        </p>

        <ul class="tiles" aria-label="Répartition">
          <li class="tile">
            <strong>{{ dashboard.tiles.value.total }} éléments</strong>
          </li>
          <li v-for="bucket in dashboard.tiles.value.buckets" :key="bucket.key" class="tile">
            <span>{{ keyLabel(bucket.key) }}</span>
            <strong>{{ bucket.count }}</strong>
          </li>
        </ul>

        <HeatmapChart :heatmap="dashboard.heatmap.value" />
      </template>
    </template>
  </section>
</template>

<style scoped>
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  list-style: none;
  padding: 0;
}
.tile {
  display: flex;
  flex-direction: column;
  min-width: 8rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border, #c9c4d1);
  border-radius: 6px;
}
</style>
```

Dans `sections.ts`, après l'entrée `quality` :

```ts
  {
    path: '/analyse/tableau-de-bord',
    name: 'dashboard',
    label: 'Tableau de bord',
    summary: 'Répartition du modèle et carte de chaleur : couverture, cycle de vie, coût, risque.',
    group: 'analysis',
    view: () => import('../features/dashboard/DashboardSection.vue'),
  },
```

- [ ] **Step 7: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/useDashboard.spec.ts tests/DashboardSection.spec.ts tests/ElementCatalogue.spec.ts tests/router.spec.ts tests/AppNav.spec.ts && npm run typecheck && npm run lint`
Expected: tous passent, aucune erreur. (Si `hrefOf` appelé trois fois par case fait râler ESLint ou ralentit, le déplacer dans un `computed` de rectangles enrichis : comportement identique.)

- [ ] **Step 8: Commit**

```bash
git add frontend/src frontend/tests
git commit -m "feat(dashboard): add the dashboard with a hand-drawn heat map" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 29: Vérification du lot 3 et ADR de la lecture des propriétés

**Files:**
- Create: `docs/adr/<N3>-lecture-des-proprietes-pour-l-analyse.md`

**Interfaces:**
- Consumes: —
- Produces: l'ADR du lot 3 (fusionne le choix de ne pas avoir de bibliothèque de graphiques).

- [ ] **Step 1: Numéro**

`ls docs/adr | grep -E '^[0-9]{4}-' | tail -1` → N3 = ce numéro + 1.

- [ ] **Step 2: Écrire l'ADR**

```markdown
---
titre: Lire les propriétés pour l'analyse, et colorer côté serveur
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/analytics.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/api/schemas.py, backend/src/ea/mcp/server.py, frontend/src/features/dashboard, frontend/src/lib/heatmapLayout.ts, CLAUDE.md
---

# <N3>. Lire les propriétés pour l'analyse, et colorer côté serveur

Date : 2026-10-10
Statut : Proposition

## Contexte

Le tableau de bord veut répondre à « combien de composants en fin de vie, par
couche ? », « quel coût cumulé ? », « où est le risque ? ». Ces trois quantités
(`lifecycle`, `cost`, `risk`) sont des clés de `properties`, une `jsonb` dont les
valeurs sont des chaînes (`adr/0033`). Trois questions se posent : faut-il les
typer, où se fait l'agrégation, et qui décide de la couleur.

Typer le stockage obligerait une migration de données et un schéma de
propriétés, pour trois clés. Calculer les couleurs dans le SPA y mettrait des
seuils, c'est-à-dire une règle, ce que `adr/0012` interdit. Et une bibliothèque
de graphiques pour une grille de rectangles ajouterait une dépendance
(`CLAUDE.md`, « Drawing a graph »).

## Décision

1. **Le stockage ne change pas.** `properties` reste `Mapping[str, str]`. Le
   domaine gagne un *catalogue de lectures* (`ANALYTIC_PROPERTIES`) : `lifecycle`
   et `risk` sont des énumérations ordonnées, `cost` un nombre. Toute autre clé est
   un libellé nominal : on la compte, on ne l'additionne pas.
2. **Une valeur illisible n'est jamais ignorée en silence.** Un nombre accepte `.`
   ou `,` décimal et l'espace, l'espace insécable (U+00A0) et l'espace fine
   insécable (U+202F) comme séparateurs de milliers ; tout le reste (`1 200 €`,
   `1.200,50`, un exposant) est illisible, parce que deviner une graphie
   transformerait une faute de frappe en total faux. Les illisibles sont comptés
   (`unreadable`), isolés dans un seau (`__invalid__` pour une énumération,
   `__unset__` pour une valeur absente) et signalés par une règle de qualité
   `unreadable_property` (information). Écrire reste libre : valider à l'écriture
   est un chantier ultérieur.
3. **L'agrégation se fait en SQL, la classification une seule fois.** La requête
   groupe par la valeur *brute* de chaque dimension et renvoie des sommes
   partielles ; `summarise` replie les graphies (casse, accents, ordre déclaré),
   ordonne, coupe à `MAX_BUCKETS = 200` et colore. `PostgreSQL lower()` dépend de
   la locale de la base : le laisser décider de ce qu'est « Élevé » ferait diverger
   le SQL et la référence en mémoire (`group_elements`), contre laquelle un test
   d'intégration compare sur des valeurs sales. La clé de propriété suit le motif
   de `domain/model.py` puis est un paramètre lié ; `GROUP BY` se fait par rang de
   colonne ; le cast numérique est derrière un `CASE` sur le motif.
4. **Le serveur décide de la couleur, sous la forme d'un `level` de 0 à 4.** Pour
   une énumération déclarée comptée seule, c'est le rang dans l'ordre déclaré ; sinon
   le quantile de la valeur du seau (les valeurs égales partagent un niveau, une
   valeur absente n'en a pas). Le SPA ne fait que `level → couleur`
   (`LEVEL_FILLS`, avec une encre propre à chaque fond) et imprime la valeur dans
   la case, pour ne pas dépendre de la seule teinte.
5. **Pas de bibliothèque de graphiques.** La carte de chaleur est du SVG écrit à
   la main sur un module de géométrie pur (`lib/heatmapLayout.ts`), comme le
   voisinage (`adr/0010`). Un diagramme à barres n'est pas fourni : un tableau
   trié suffit.
6. **Garde-fous** : un délai (`statement_timeout`, 5 s, local à la transaction)
   borne une requête coûteuse ; un plafond de 5 000 groupes bruts coupe un cardinal
   absurde en le journalisant. Le dépassement du délai n'est pas traduit en erreur
   de domaine (aucun motif de traduction par message n'existe ici) : il sort en
   `internal_error` 500, la trace dans les logs.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Typer les propriétés (colonnes ou schéma) | validation à l'écriture | migration de données, schéma à maintenir pour trois clés | écartée, retournable |
| Classer en SQL (`lower`, `CASE`) | un seul passage | dépend de la locale ; deux implémentations à garder égales | écartée |
| Couleur calculée dans le SPA | moins de champs | seuils = règle dans TypeScript | écartée |
| Teinte (`#rrggbb`) envoyée par le serveur | SPA minimal | le serveur connaîtrait le thème clair/sombre | écartée |
| Bibliothèque de graphiques | barres, axes, animations gratuits | dépendance pour une grille de rectangles | écartée |
| Index GIN sur `elements.properties` | vitesse | à justifier par un `EXPLAIN` | repoussée |

## Conséquences

- `cost` saisi « 1 200 € » est visible (compté, signalé) mais ne participe pas
  aux sommes ; la correction est dans la donnée.
- Ajouter une clé lisible (par exemple `criticality`) est une ligne de
  `ANALYTIC_PROPERTIES` et un test.
- La carte ne montre pas la tendance : rien n'est stocké, donc aucun historique.
- La requête groupée lit toute la table des éléments ; à l'échelle visée (quelques
  milliers) c'est suffisant. Un index GIN est à décider sur un `EXPLAIN`, pas avant.
- Une énumération ordonnée comptée avec une autre mesure que `count` est colorée
  par quantile, pas par rang : la couleur suit alors la valeur affichée.
- Les dimensions `package` et les périmètres par paquet ou requête arriveront avec
  le sous-projet 1, par le paramètre `scope` du port.

## Références

- ADR liés : [0010](0010-dessiner-le-voisinage-d-un-element.md), [0012](0012-ecran-du-metamodele.md), [0033](0033-postgresql-seul-pour-le-graphe.md), [<N1>](<N1>-matrice-de-relations.md), [<N2>](<N2>-controles-de-qualite-du-modele.md)
- Code concerné : `backend/src/ea/domain/analytics.py`, `frontend/src/lib/heatmapLayout.ts`
```

- [ ] **Step 3: Régénérer et vérifier**

Run: `make openapi && make check`
Expected: vert.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/<N3>-lecture-des-proprietes-pour-l-analyse.md
git commit -m "docs(adr): record how properties are read for analysis" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 30: (Conditionnelle — sous-projet 1) Périmètres par paquet et par requête enregistrée

À n'exécuter que si la Task 1 a conclu « SP1 présent ». Sinon, passer à la Task 31 et ne rien faire ici.

**Files:**
- Modify: `backend/src/ea/domain/matrix.py` (`AxisSelection`), `backend/src/ea/services/analysis.py`, `backend/src/ea/api/schemas.py`, `backend/src/ea/api/analysis.py`, `backend/src/ea/main.py`, `backend/src/ea/mcp/server.py`
- Modify: `backend/tests/unit/test_analysis_service.py`, `backend/tests/e2e/test_analysis_api.py`
- Modify: `frontend/src/features/matrix/axis.ts`, `MatrixSection.vue`, `frontend/tests/matrixAxis.spec.ts`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: de SP1 — un `ElementQuery` (avec `package_id` et `include_subpackages`) évaluable, un `SavedQuery.query: ElementQuery`, et les services de paquets / de requêtes enregistrées. Les noms exacts sont ceux du plan du sous-projet 1 ; les trois lieux d'adaptation sont marqués `(SP1)` ci-dessous.
- Produces:
```python
ScopeResolver = Callable[[UUID | None, UUID | None], Awaitable[tuple[UUID, ...]]]
# AnalysisService(architecture, repository, *, params=None, scope_ids: ScopeResolver | None = None)
# AxisSelection gains: package_id: UUID | None = None; query_id: UUID | None = None
# check(..., package_id: UUID | None = None, query_id: UUID | None = None)
# aggregate/heatmap(..., package_id=None, query_id=None)  -> grouped(..., scope=ids)
# Dimension: package  (group by elements.package_id, keyed by package name)
```
```ts
// axis.ts: { kind: 'package'; id: string } | { kind: 'query'; id: string }  — URL 'pkg:<id>' / 'q:<id>'
```

- [ ] **Step 1: Garde**

```bash
cd backend && grep -rn "class ElementQuery\|package_id" src/ea/domain | head
```
Attendu : des résultats. Sinon, arrêter ici.

- [ ] **Step 2: Le résolveur de périmètre — la seule couture avec le sous-projet 1**

Le service ne connaît ni paquets ni requêtes : il reçoit un `ScopeResolver` qui répond « les ids d'éléments de ce paquet (descendants compris) ou de cette requête enregistrée ». Dans `main.py`, l'implémenter avec les services du sous-projet 1 et le passer à `AnalysisService(..., scope_ids=...)`. Lire d'abord, dans le plan et le code du sous-projet 1, les trois symboles suivants et les noter :

1. (SP1) la méthode qui renvoie un `SavedQuery` par id (`SavedQueryService.get(...)`) ;
2. (SP1) la méthode qui évalue un `ElementQuery` en éléments (`ArchitectureService.search_elements(...)` ou `list_elements(query)`), avec `include_subpackages=True` ;
3. (SP1) le nom de la colonne `ElementRecord.package_id` et de la table des paquets pour la dimension `package`.

Puis écrire dans `main.py` :

```python
def build_scope_resolver(app: FastAPI) -> ScopeResolver:
    async def resolve(package_id: UUID | None, query_id: UUID | None) -> tuple[UUID, ...]:
        if query_id is not None:
            saved = await saved_query_service_of(app).get(query_id)          # (SP1)
            query = saved.query
        else:
            query = ElementQuery(package_id=package_id, include_subpackages=True)  # (SP1)
        found = await architecture_service_of(app).search_elements(query)    # (SP1)
        return tuple(element.id for element in found)
    return resolve
```

- [ ] **Step 3: Tests qui échouent (écrits contre la couture, sans rien du sous-projet 1)**

Dans `test_analysis_service.py`, avec un résolveur factice :

```python
@pytest.mark.asyncio
class TestScopedByPackageOrQuery:
    async def test_a_package_axis_is_the_ids_the_resolver_names(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        inside = await service.create_element(element_type=E.NODE, name="inside")
        await service.create_element(element_type=E.NODE, name="outside")
        package = uuid4()

        async def resolver(package_id, query_id):  # type: ignore[no-untyped-def]
            return (inside.id,) if package_id == package else ()

        scoped = AnalysisService(service, repository, scope_ids=resolver)

        matrix = await scoped.matrix(AxisSelection(package_id=package), AxisSelection())

        assert [e.name for e in matrix.rows.elements] == ["inside"]
        assert len(matrix.cols.elements) == 2

    async def test_a_scope_over_the_ceiling_is_cut_not_refused(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        ids = [
            (await service.create_element(element_type=E.NODE, name=f"n{i:03}")).id
            for i in range(MAX_MATRIX_AXIS + 2)
        ]

        async def resolver(package_id, query_id):  # type: ignore[no-untyped-def]
            return tuple(ids)

        matrix = await AnalysisService(service, repository, scope_ids=resolver).matrix(
            AxisSelection(query_id=uuid4()), AxisSelection()
        )

        assert matrix.rows.truncated and matrix.rows.total == MAX_MATRIX_AXIS + 2

    async def test_the_quality_run_and_the_aggregate_are_narrowed_to_the_scope(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        inside = await service.create_element(element_type=E.NODE, name="inside")
        await service.create_element(element_type=E.NODE, name="outside")

        async def resolver(package_id, query_id):  # type: ignore[no-untyped-def]
            return (inside.id,)

        scoped = AnalysisService(service, repository, scope_ids=resolver)

        page = await scoped.check(code=RuleCode.ORPHAN, package_id=uuid4())
        counted = await scoped.aggregate(Dimension.parse("layer"), Metric.COUNT, query_id=uuid4())

        assert [f.element_id for f in page.findings] == [inside.id]
        assert counted.total == 1

    async def test_a_scope_asked_without_a_resolver_is_a_refused_question(
        self, analysis: AnalysisService
    ) -> None:
        with pytest.raises(InvalidAnalysisError):
            await analysis.check(package_id=uuid4())

    async def test_both_a_package_and_a_query_is_a_refused_question(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        async def resolver(package_id, query_id):  # type: ignore[no-untyped-def]
            return ()

        scoped = AnalysisService(service, repository, scope_ids=resolver)

        with pytest.raises(InvalidAnalysisError):
            await scoped.check(package_id=uuid4(), query_id=uuid4())
```
(imports : `uuid4`, `MAX_MATRIX_AXIS`, `InvalidAnalysisError`, `Dimension`, `Metric`, `RuleCode`.)

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py -x -k Scoped`
Expected: FAIL — `TypeError: AnalysisService.__init__() got an unexpected keyword argument 'scope_ids'`.

- [ ] **Step 4: Implémenter côté service**

`AxisSelection` gagne `package_id: UUID | None = None` et `query_id: UUID | None = None`. Dans `services/analysis.py` :

```python
from collections.abc import Awaitable, Callable

ScopeResolver = Callable[[UUID | None, UUID | None], Awaitable[tuple[UUID, ...]]]
```
`__init__` : paramètre `scope_ids: ScopeResolver | None = None` stocké dans `self._scope_ids`. Méthode privée :

```python
    async def _scope(self, package_id: UUID | None, query_id: UUID | None) -> tuple[UUID, ...] | None:
        """The element ids of a package or a saved query; `None` means the whole catalogue."""
        if package_id is None and query_id is None:
            return None
        if package_id is not None and query_id is not None:
            msg = "a scope is a package or a saved query, not both"
            raise InvalidAnalysisError(msg)
        if self._scope_ids is None:
            msg = "this deployment has no packages or saved queries to scope by"
            raise InvalidAnalysisError(msg)
        return await self._scope_ids(package_id, query_id)
```
Dans `_axis`, avant la branche `element_ids` :

```python
        scope = await self._scope(selection.package_id, selection.query_id)
        if scope is not None:
            found = await self._repository.elements_by_ids(scope)
            return MatrixAxis.of(found, total=len(found))
```
`check` prend `package_id`/`query_id` : après `require_caller()`, `scope = await self._scope(...)` ; si `scope is not None`, `elements = await self._repository.elements_by_ids(scope)` (et le plafond s'applique à `len(elements)`) au lieu de `list_elements`. `aggregate`/`heatmap` prennent les mêmes deux paramètres et passent `scope=` à `grouped` (`_summary` reçoit `scope`). Importer `InvalidAnalysisError`.

- [ ] **Step 5: API, MCP, SPA**

- `AxisSelect` gagne `package_id: UUID | None = None`, `query_id: UUID | None = None` (et les transmet dans `selection()`).
- `GET /quality`, `/analytics/aggregate`, `/analytics/heatmap` gagnent `package_id` et `query_id` (`Annotated[UUID | None, Query()] = None`) transmis au service ; les outils `check_model` et `aggregate_model` aussi (`UUID | None`).
- `main.py` : `scope_ids=build_scope_resolver(app)` dans le lifespan.
- `axis.ts` : deux variantes `{ kind: 'package'; id: string }` et `{ kind: 'query'; id: string }`, URL `pkg:<id>` / `q:<id>`, corps `{ package_id }` / `{ query_id }` ; `parseAxis`/`axisToParam`/`axisToBody` les traitent (ajouter les cas aux `switch`). Test dans `matrixAxis.spec.ts` : `pkg:abc` et `q:abc` font l'aller-retour et donnent `{ package_id: 'abc' }`, `{ query_id: 'abc' }`.
- `MatrixSection.vue` : deux listes (paquets, requêtes) dans les sélecteurs d'axe, lues par les routes du sous-projet 1 (`GET /packages`, `GET /queries`) ; si le sous-projet 1 fournit déjà des composants de sélection, les réutiliser plutôt que d'en écrire.

- [ ] **Step 6: Dimension `package`**

`DimensionKind.PACKAGE = "package"` ; `DIMENSION_PATTERN` gagne `package` ; `_raw_key` : `sql_cast(ElementRecord.package_id, Text)` coalescé à `""` (une valeur absente tombe dans `__unset__`, comme une propriété) ; `classify` traite `PACKAGE` comme `PROPERTY` pour le seau absent ; `_raw_of` lit `str(element.package_id or "")`. Les noms de paquets s'affichent côté SPA par `GET /packages` (ids → noms), pas par le serveur d'agrégation. Test d'intégration : trois éléments dans deux paquets et un sans paquet → `package` donne deux seaux d'ids et `__unset__`.

- [ ] **Step 7: Relancer tout, régénérer, vérifier**

Run: `cd backend && uv run pytest tests/unit/test_analysis_service.py tests/e2e/test_analysis_api.py tests/unit/test_mcp_server.py -x && cd .. && make openapi && make check`
Expected: vert.

- [ ] **Step 8: Commit**

```bash
git add backend frontend
git commit -m "feat(analysis): scope the matrix, the checks and the dashboards by package or saved query" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 31: Clôture — `CLAUDE.md`, vérification d'ensemble

**Files:**
- Modify: `CLAUDE.md` (section « Project status » et « Repository layout »)
- Modify: aucun autre (les trois entrées de `router/sections.ts` ont été ajoutées aux Tasks 10, 18 et 28)

**Interfaces:**
- Consumes: les trois ADR
- Produces: la mémoire du dépôt à jour.

- [ ] **Step 1: Vérifier les entrées de menu**

Run: `grep -n "analyse/matrice\|analyse/qualite\|analyse/tableau-de-bord" frontend/src/router/sections.ts`
Expected: trois lignes, toutes avec `view:` ; `cd frontend && npm test -- --run tests/router.spec.ts tests/AppNav.spec.ts` passe.

- [ ] **Step 2: Une phrase factuelle dans `CLAUDE.md`**

Dans la section « Project status », à la fin du premier paragraphe (après la phrase sur `/mcp` et ses « thirty-four tools »), ajouter — et corriger « thirty-four » en « thirty-seven » (les trois outils `get_relationship_matrix`, `check_model`, `aggregate_model`) :

```
Le modèle s'analyse aussi (sous-projet « analyse du modèle ») : `/analyse/matrice` croise deux ensembles d'éléments et crée ou retire un lien par cellule en passant par `/relationships` (voir `docs/adr/<N1>`), `/analyse/qualite` rend les constats de sept règles à code stable, calculés à la demande (voir `docs/adr/<N2>`), et `/analyse/tableau-de-bord` agrège le catalogue avec une carte de chaleur en SVG dont la couleur est un `level` décidé par le serveur (voir `docs/adr/<N3>`) — sans table ni migration, `AnalysisService` en lecture seule, et trois outils MCP de lecture (`get_relationship_matrix`, `check_model`, `aggregate_model`).
```

Dans « Repository layout », ajouter sous `domain/` : `matrix.py`, `quality.py`, `analytics.py` (une ligne chacun), sous `services/` : `analysis.py`, sous `api/` : `analysis.py`, sous `src/features/` : `matrix/`, `quality/`, `dashboard/`.

Dans la section « The MCP adapter is a sibling of `api/` », remplacer « holds **four** service providers » par « holds **five** service providers » et ajouter `get_analysis` à la liste de ceux qui sont requis.

- [ ] **Step 3: Vérification d'ensemble**

Run: `make openapi-check && make check && make test-integration`
Expected: tout vert ; couverture backend ≥ 90 %.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the model analysis in CLAUDE.md" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revue

**Couverture de la spec.** §3 matrice → Tasks 2, 4 ; qualité (six règles + deux paramètres) → Tasks 12-15 ; analytique (`Dimension`, `Metric`, `Aggregate`, `scale_for`, catalogue de lectures) → Tasks 20-21. §4 aucune table → respecté ; paramètres de `Settings` → Task 15 ; index GIN → ADR du lot 3 (repoussé sur `EXPLAIN`). §5 API : `POST /matrix`, `GET /matrix/cell` (Task 5), `GET /quality` (Task 16), `/analytics/aggregate` et `/heatmap` (Task 24) ; plafonds `MAX_MATRIX_AXIS`, `MAX_FINDINGS`, `MAX_BUCKETS` (Tasks 2, 12, 20, réimportés par `schemas.py`) ; timeout (Task 22). §6 MCP trois outils (Tasks 6, 17, 25), annotations, liste complète, bornes (`TestTheSameBoundsAsTheHttpAdapter`), `INSTRUCTIONS`. §7 SPA trois sections (Tasks 7-10, 18, 27-28), clavier, panneau, URL, `useLatestRequest`, `heatmapLayout.ts`, légende, encre propre, valeur imprimée. §8 sécurité : lecture authentifiée (tests 401/200 lecteur), écriture par `/relationships`, pas de SQL dynamique (test du paramètre lié, Task 22), texte et jamais `v-html` (test, Task 18), log `quality_run` (Task 15). §9 tests : unit, intégration, API, MCP, SPA présents ; E2E Playwright non scaffoldé — le parcours « ouvrir la matrice, créer un lien » est en spec Vitest (`MatrixSection.spec.ts`, Task 10). §10 trois ADR (Tasks 11, 19, 29, l'ADR 4 fusionnée dans la 3 comme la spec l'autorise). §11 : défauts retenus appliqués (nombre toléré, plafond 5 000, matrice 50, seuil réglable, constat `error` sans suppression). Le sous-projet 1 est isolé dans la Task 30 derrière la Task 1.

**Review Focus.** Matrice vide : Tasks 2, 4, 9. Axe > 50 : Tasks 4, 5, 6. Valeur illisible : Tasks 20, 21, 22, 26. Élément dans les deux axes : Tasks 2, 4. Deux sens : Tasks 2, 9.

**Noms et signatures croisés.** `AxisSelection` (domaine) ↔ `AxisSelect` (API, `.selection()`) ↔ `AxisChoice`/`axisToBody` (SPA) : trois niveaux, un nom chacun, convertis explicitement. `MatrixRead.cells[].links` ↔ `MatrixGrid.linksIn`. `RuleCode` (domaine) ↔ `RuleCode` (schéma généré) ↔ `RULE_LABELS` : la Task 26 ajoute `unreadable_property` aux quatre endroits. `Aggregate.property_key` (domaine) ↔ `AggregateRead.property_key` ↔ paramètre HTTP `property` (alias) ↔ argument MCP `property_key`. `grouped(dimensions, metric_key, *, scope=None)` : même signature dans le port, le SQL, le double en mémoire et le test. `build_mcp_server(...)` : cinquième argument `get_analysis` ajouté à la Task 6 aux trois appelants (`main.py`, deux fixtures).

**Placeholders.** Aucun marqueur d'attente ni formule du type « idem tâche précédente ». Les seuls marqueurs variables sont `<N1>`, `<N2>`, `<N3>` (numéros d'ADR, calculés à l'exécution par la commande donnée à la Task 11, 19, 29) et `<ADR lot n>` dans les docstrings, à remplacer par le même numéro au moment de l'écriture de l'ADR correspondante (rechercher `<ADR lot` avant le commit de la tâche de clôture de chaque lot). La Task 30 est conditionnelle par construction (garde, couture `ScopeResolver` testée sans le sous-projet 1) ; ses trois lieux `(SP1)` se lisent dans le code du sous-projet 1 une fois livré.

**Points à surveiller à l'exécution.** (1) La forme exacte de `$defs` dans le schéma d'entrée d'un outil MCP à argument modèle (test `test_its_axis_bounds_are_the_http_ones`, Task 6) : si le SDK aplatit autrement, comparer sur `AxisSelect.model_json_schema()` seul. (2) `getByTitle` sur un `<title>` SVG et le `href` d'un `<a>` SVG sous jsdom (Task 28) : si jsdom ne les expose pas, interroger `container.querySelector('a')` et `getAttribute('href')`. (3) Les formatages ruff/prettier : lancer `make lint` avant chaque commit.
