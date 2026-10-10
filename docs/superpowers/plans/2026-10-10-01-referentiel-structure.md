# Référentiel structuré (paquets + recherche avancée) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ranger éléments et diagrammes dans un arbre de paquets (façon *Project Browser* de Sparx EA) et offrir une recherche structurée d'éléments (`ElementQuery`) avec requêtes enregistrées partagées, exposées en REST, en MCP (34 → 44 outils) et dans le SPA.

**Architecture:** Un paquet est un classement en liste d'adjacence (`packages`, `elements.package_id`, `diagrams.package_id`), invisible des traversées du graphe. Un objet de domaine pur `ElementQuery` (types, couches, texte, paquet, propriétés, relations présentes/absentes) est compilé par un unique chemin SQL (`_matching` étendu dans `architecture_store.py`) ; il est partagé par `GET /elements`, `POST /elements/search`, les requêtes enregistrées (`saved_queries`, JSON versionné) et les outils MCP. Aucune règle d'ArchiMate n'est recopiée.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2 async + Alembic (PostgreSQL), `mcp` SDK, Vue 3 `<script setup>` + TypeScript + Vitest/Testing Library, client OpenAPI généré.

**Spec:** `docs/superpowers/specs/2026-10-10-01-referentiel-structure-design.md`

## Écarts constatés avec la spec

Relevés en lisant le code réel (la spec n'est pas corrigée ; le plan suit le code).

1. **`Element` du domaine n'a pas de `package_id`** (`backend/src/ea/domain/model.py`) et `ArchitectureService.update_element` reconstruit l'`Element` champ par champ (il perdrait le paquet). La spec ne le dit pas : le plan ajoute `package_id: UUID | None = None` à `Element`, remplace cette reconstruction par `dataclasses.replace`, et garde `package_id` **hors** de `element_row()` (un `save_element` ne doit jamais écraser un rangement fait entre-temps ; `add_element` laisse la colonne à `NULL`).
2. **Les bornes `MAX_PACKAGES`, `MAX_PACKAGE_DEPTH`, `MAX_MOVE_BATCH` ne peuvent pas être « déclarées dans `api/schemas.py` »** : les services en ont besoin et `services/` n'importe pas `api/`. Elles sont déclarées dans `domain/packages.py` et importées par `api/schemas.py` (même schéma que `domain/files.py` → `schemas.py`). Les adaptateurs HTTP et MCP les partagent via `schemas.py` comme demandé.
3. **Détection de cycle/profondeur** : la spec demande une CTE récursive sur les ancêtres. Le plan la remplace par une fonction pure `check_tree()` sur la liste à plat (≤ 2 000 paquets, déjà chargée par le service pour calculer les chemins) : testable sans base, et la course entre deux déplacements concurrents est la même qu'avec une CTE (seul un verrou la fermerait). La CTE récursive est conservée là où la spec la place pour le filtre « sous-arbre » d'éléments (`_subtree` dans `architecture_store.py`). Si le relecteur préfère la CTE, seule la Task 6 change.
4. **Erreurs non listées au §3** mais nécessaires : `TooManyPackagesError` (plafond Q6 → `422 too_many_packages`), `UnknownPackageError` (`422 unknown_package`, §5), `InvalidQueryError` (version inconnue/JSON stocké illisible → `422 invalid_query`, §8), `SavedQueryNotFoundError` (`404 not_found`), `DuplicateSavedQueryError` (`409 duplicate`). Chacune reçoit sa ligne dans `_STATUS` ; un test vérifie que **toute** sous-classe de `DomainError` y figure.
5. **Schéma `ElementQuerySchema` sans valeur par défaut** : une valeur par défaut scinderait le composant OpenAPI en `-Input`/`-Output` (le schéma sert à la fois de corps de requête et de `SavedQueryRead.query`). Précédent dans le dépôt : `DiagramNode` (« Required, not defaulted »). Tous les champs sont donc requis (les outils MCP et le SPA construisent l'objet complet) ; un test garde l'unicité du composant.
6. **« Relue par la même porte » (§8)** : le JSON stocké est relu par `domain/query.py::from_json` (pur, valide la version et chaque énumération) et non par le schéma Pydantic, car `domain/` et `services/` n'importent pas `api/`. Le schéma `ElementQuerySchema` produit ce même JSON (`to_json`).
7. **Le double mémoire ignore `ElementFilter`** (`tests/conftest.py::InMemoryRepository.list_elements` renvoie tout) : le filtrage par paquet, propriété et relation n'est donc prouvé qu'en intégration (PostgreSQL jetable), pas dans la suite E2E doublée.
8. **E2E de bout en bout** : la spec le range dans `tests/e2e` (suite API doublée) ; or les doubles ne filtrent pas (point 7). Le parcours « créer un paquet → ranger → chercher “sous-arbre” → enregistrer → rejouer » est placé dans `tests/integration/test_referentiel_journey.py` (HTTP réel sur PostgreSQL jetable). Playwright reste non installé.
9. **Section Diagrammes** : la spec parle d'un sélecteur « dans l'édition du nom » ; l'écran n'a aucune édition de nom (seulement créer/supprimer). Le sélecteur de paquet est donc à la création et sur chaque ligne de la liste (« Ranger dans… »).
10. **Détail d'un élément** : `ElementRead` ne porte que `package_id`, pas `path` (la spec dit « `path` fourni par l'API »). Le SPA lit le chemin sur `GET /packages` (déjà chargé pour l'arbre) ; aucun champ ajouté à `ElementRead`.
11. **MCP : « parent nul = racine »** (`update_package`) est inexprimable avec un argument optionnel (absent et `null` se confondent). Les outils prennent une chaîne `package_id`/`parent_id`/`move_contents_to` valant un UUID **ou** le mot `root`, analysée par une fonction unique de `api/schemas.py` (`package_target`) partagée avec `DELETE /packages/{id}?move_contents_to=`.
12. **`build_mcp_server` a quatre fournisseurs positionnels** (`get_service, get_documents, get_ipam, get_files`) ; le plan en ajoute deux (`get_packages`, `get_queries`), requis comme les autres, ce qui touche `main.py` et les deux fixtures de `tests/unit/test_mcp_server.py`.
13. **`saved_queries.name`** : unicité sensible à la casse (contrainte `uq_saved_queries_name`), contrairement aux frères d'un paquet (insensibles à la casse, spec §4). Lecture littérale de la spec ; non tranché ailleurs.

## Global Constraints

- Dépendances : `api → services → domain ← repositories` ; `mcp/` à côté de `api/` ; `domain/` sans import FastAPI/SQLAlchemy/`api/`.
- Chaque méthode publique de `PackageService` et `SavedQueryService` commence par `require_caller()` (lecture) ou `require_editor()` (écriture) ; `tests/unit/test_service_guards.py::WRITES` les liste (AST).
- `MAX_PACKAGE_DEPTH = 12` (racine = niveau 1), `MAX_PACKAGES = 2000`, `MAX_MOVE_BATCH = 100`.
- Nom de paquet : `strip()`, 1..200 caractères, sans `/`.
- Frères uniques sans tenir compte de la casse : index partiels `uq_packages_root_name` (`lower(name)` où `parent_id IS NULL`) et `uq_packages_child_name` (`parent_id, lower(name)` où `parent_id IS NOT NULL`).
- Clés étrangères `elements.package_id`, `diagrams.package_id`, `packages.parent_id` : `ON DELETE RESTRICT`, colonnes nullables, index `ix_elements_package_id`, `ix_diagrams_package_id`.
- `UniqueConstraint(element_type, name)` des éléments reste **globale**.
- Suppression d'un paquet non vide : `409 package_not_empty` ; `?move_contents_to=<uuid|root>` déplace le contenu direct dans la même transaction ; cible = descendant du paquet supprimé → `409 cyclic_package`.
- Codes d'erreur : `PackageNotFoundError` 404 `not_found` ; `DuplicatePackageError` 409 `duplicate` ; `CyclicPackageError` 409 `cyclic_package` ; `PackageNotEmptyError` 409 `package_not_empty` ; `PackageTooDeepError` 422 `package_too_deep` ; `TooManyPackagesError` 422 `too_many_packages` ; `UnknownPackageError` 422 `unknown_package` ; `InvalidQueryError` 422 `invalid_query` ; `SavedQueryNotFoundError` 404 `not_found` ; `DuplicateSavedQueryError` 409 `duplicate`.
- `ElementQuery` : `MAX_PROPERTY_TESTS = 10`, `MAX_RELATION_TESTS = 5`, `MAX_TEXT = 200`, ops `eq|contains|exists|absent`, directions `out|in|any`, ordre `name|created_at|updated_at`, `version: 1`, JSON stocké ≤ 16 Ko (16 384 octets).
- Aucun SQL libre : filtre compilé par SQLAlchemy Core, clés/valeurs de `properties` en paramètres liés (`->>` avec clé liée), `text` par `strpos`.
- `created_by` d'une requête enregistrée vient du `Caller`, jamais du corps.
- Une ligne de journal par écriture : `package_created`, `package_updated`, `package_deleted`, `elements_moved`, `diagram_updated`, `query_saved`, `query_updated`, `query_deleted`.
- Migrations réversibles, première numérotée **0009** (à renuméroter si une autre migration est entrée avant), puis **0010** pour `saved_queries` ; noms de contraintes par la convention figée de `db/base.py` ; `db/models/__init__.py` importe chaque nouveau modèle ; `mypy --strict src migrations` propre.
- MCP : 34 → 44 outils ; l'assertion de liste complète de `tests/unit/test_mcp_server.py` et `TestTheSameBoundsAsTheHttpAdapter` sont mis à jour ; docstrings en intention ; annotations `READS/ADDS/EDITS/REMOVES`.
- OpenAPI : `make openapi` après tout changement de schéma, `backend/openapi.json` et `frontend/src/api/schema.d.ts` committés ; `make openapi-check` vert.
- Couverture backend ≥ 90 % (`make test`), `ruff`, `mypy --strict`, ESLint, `vue-tsc` verts : `make check` avant chaque clôture de lot.
- SPA : client généré uniquement (aucun type recopié) ; chaque chargement répété passe par `useLatestRequest()` ; saisie texte par `debounce.ts` ; état partageable dans l'URL : `?package=<id|root>`, `?subpackages=0`, `?saved=<id>`, `?q=<JSON>` (≤ 2 000 caractères) ; section `/recherche` dans le groupe `analysis` de `sections.ts` ; arbre `role="tree"` avec `aria-expanded` et flèches ; un lecteur ne voit aucune commande d'écriture.
- Aucun test ne sort de la machine (garde-socket de `tests/conftest.py`) ; chaque test a un appelant (fixture autouse `an_editor`).
- Commits Conventional Commits, branche depuis `main` (`feat/referentiel-structure`), jamais `--no-verify`.

## Review Focus

1. **Un `PATCH /elements/{id}` ou un `save_element` ne doit ni perdre ni écraser le rangement** (la spec n'en parle pas ; aucune route de la spec ne l'exerce). Tests : Task 2 (`update_element` conserve `package_id`) et Task 4 (un `Element` lu avant un déplacement puis sauvé ne remet pas le paquet à `NULL`).
2. **Suppression d'un paquet dont le déplacement de contenu heurte un nom de frère, ou qui ne contient que des diagrammes** : `409 duplicate` / `409 package_not_empty` lisibles et **rien** de déplacé (transaction annulée), jamais un 500 d'intégrité. Tests : Task 4 (dépôt, atomicité), Task 6 (service) et Task 7 (API).
3. **Une requête enregistrée relue après coup** : version inconnue, JSON corrompu, ou paquet disparu → `422 invalid_query` / `422 unknown_package`, et la liste `GET /queries` reste lisible (elle ne parse pas les requêtes). Tests : Task 22 (dépôt) et Task 23 (service).
4. **Filtre contradictoire** (`root_only` + `package`) → `422` explicite et non résultat vide silencieux, par HTTP comme par MCP. Tests : Task 8 (`GET /elements`) et Task 17 (`ElementQuery`).
5. **`?package=` d'une URL pointant un paquet supprimé ou inconnu** : l'arbre n'a aucun nœud sélectionné, le catalogue affiche « Aucun élément » sans erreur ni boucle, et un lecteur ne dispose d'aucune commande de rangement. Tests : Task 13 (catalogue) et Task 12 (gestionnaire d'arbre).

---

## Carte des fichiers

**Backend — créés**
- `backend/src/ea/domain/packages.py` — `Package`, `PackageCounts`, `PackageSummary`, `UNSET`, bornes, `validate_package_name`, `check_tree`, `path_of`, `descendants_of`.
- `backend/src/ea/domain/query.py` — `ElementQuery`, `PropertyTest`, `RelationTest`, énumérations, `to_json`/`from_json`, `QueryResult`.
- `backend/src/ea/domain/saved_queries.py` — `SavedQuery`, `SavedQuerySummary`.
- `backend/src/ea/db/models/package.py` — `PackageRecord`.
- `backend/src/ea/db/models/saved_query.py` — `SavedQueryRecord`.
- `backend/migrations/versions/0009_packages.py`, `backend/migrations/versions/0010_saved_queries.py`.
- `backend/src/ea/repositories/package_store.py` — `PostgresPackageRepository`.
- `backend/src/ea/repositories/saved_query_store.py` — `PostgresSavedQueryRepository`.
- `backend/src/ea/services/packages.py` — `PackageService` ; `backend/src/ea/services/saved_queries.py` — `SavedQueryService`.
- `backend/src/ea/api/packages.py` (paquets + `POST /elements/move`), `backend/src/ea/api/queries.py` (`/queries`, `POST /elements/search`).
- Tests : `tests/unit/test_packages.py`, `test_package_service.py`, `test_error_statuses.py`, `test_element_query.py`, `test_saved_queries.py`, `test_saved_query_service.py`, `test_query_schema.py` ; `tests/e2e/test_package_api.py`, `test_query_api.py` ; `tests/integration/test_packages_schema.py`, `test_package_store.py`, `test_element_search.py`, `test_saved_query_store.py`, `test_referentiel_journey.py`.

**Backend — modifiés**
- `domain/model.py` (Element.package_id, `PROPERTY_KEY` public), `domain/errors.py`, `domain/ports.py` (`ElementFilter`, `PackageRepository`, `SavedQueryRepository`), `domain/diagrams.py`.
- `db/models/{architecture,diagram,__init__}.py`, `repositories/{architecture_store,diagram_store}.py`, `services/{architecture,diagrams}.py`.
- `api/{schemas,errors,dependencies,architecture,diagrams}.py`, `main.py`, `mcp/server.py`.
- `tests/conftest.py` (`InMemoryPackages`, `InMemorySavedQueries`, fixtures), `tests/unit/{test_service_guards,test_mcp_server,test_migrations}.py`, `tests/e2e/test_diagram_api.py`.
- `backend/openapi.json` (régénéré).

**Frontend — créés** (`frontend/src/features/packages/`) : `packageTree.ts`, `usePackages.ts`, `PackageTree.vue`, `PackageManager.vue` ; (`frontend/src/features/search/`) : `queryState.ts`, `useElementSearch.ts`, `useSavedQueries.ts`, `QueryBuilder.vue`, `SavedQueries.vue`, `SearchSection.vue`. Tests dans `frontend/tests/` : `packageTree.spec.ts`, `usePackages.spec.ts`, `PackageTree.spec.ts`, `PackageManager.spec.ts`, `queryState.spec.ts`, `useElementSearch.spec.ts`, `useSavedQueries.spec.ts`, `QueryBuilder.spec.ts`, `SearchSection.spec.ts`.

**Frontend — modifiés** : `src/features/elements/{ElementCatalogue.vue,useElementCatalogue.ts,ElementDetail.vue}`, `src/features/diagrams/{DiagramsSection.vue,useDiagrams.ts}`, `src/router/sections.ts`, `src/api/schema.d.ts` (généré), `tests/support/api.ts`, `tests/{ElementCatalogue,DiagramsSection,router,ElementDetail}.spec.ts`.

**Docs** : `docs/adr/00NN-les-paquets-classement-en-liste-d-adjacence.md`, `docs/adr/00NN-le-filtre-d-elements-objet-de-domaine-pur.md` (deux numéros consécutifs libres au moment de l'exécution ; le prochain libre aujourd'hui est 0042), `CLAUDE.md`.

---

# LOT 1 — Paquets côté backend

### Task 1: Domaine des paquets et erreurs

**Files:**
- Create: `backend/src/ea/domain/packages.py`
- Modify: `backend/src/ea/domain/errors.py` (ajout en fin de fichier, après `FileStorageUnavailableError`, ≈ l.148)
- Test: `backend/tests/unit/test_packages.py`, `backend/tests/unit/test_error_statuses.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `ea.domain.packages`: `MAX_PACKAGE_DEPTH: Final = 12`, `MAX_PACKAGES: Final = 2000`, `MAX_MOVE_BATCH: Final = 100`, `MAX_PACKAGE_NAME_LENGTH: Final = 200`, `class Unset`, `UNSET: Final`, `validate_package_name(name: str) -> str`, `Package` (frozen, slots: `id, parent_id, name, description, created_at, updated_at`; `Package.create(*, name, now, parent_id=None, description="") -> Package`; `.revise(*, now, name=None, description=None) -> Package`; `.moved_to(parent_id: UUID | None, *, now) -> Package`), `PackageCounts(elements=0, diagrams=0, children=0)`, `PackageSummary(package, path, counts)`, `check_tree(packages: Mapping[UUID, Package]) -> None`, `path_of(package, packages) -> str`, `descendants_of(package_id, packages) -> frozenset[UUID]`.
  - `ea.domain.errors`: `PackageNotFoundError`, `DuplicatePackageError`, `CyclicPackageError`, `PackageNotEmptyError`, `PackageTooDeepError`, `TooManyPackagesError`, `UnknownPackageError` (toutes `DomainError`).

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/unit/test_packages.py`

```python
"""The classification tree: names, paths, cycles and depth — pure rules, no database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ea.domain.errors import CyclicPackageError, PackageTooDeepError
from ea.domain.packages import (
    MAX_PACKAGE_DEPTH,
    UNSET,
    Package,
    PackageCounts,
    Unset,
    check_tree,
    descendants_of,
    path_of,
    validate_package_name,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_package(name: str = "Finance", parent: Package | None = None, description: str = "") -> Package:
    return Package.create(
        name=name, parent_id=parent.id if parent else None, now=NOW, description=description
    )


def tree(*packages: Package) -> dict[UUID, Package]:
    return {package.id: package for package in packages}


class TestNames:
    def test_a_name_is_trimmed(self) -> None:
        assert validate_package_name("  Finance ") == "Finance"

    @pytest.mark.parametrize("name", ["", "   ", "a/b", "x" * 201])
    def test_blank_slashed_or_long_names_are_refused(self, name: str) -> None:
        with pytest.raises(ValueError):
            validate_package_name(name)

    def test_two_hundred_characters_are_allowed(self) -> None:
        assert len(validate_package_name("x" * 200)) == 200


class TestThePackage:
    def test_create_trims_and_stamps(self) -> None:
        package = a_package(" Finance ", description=" Les comptes ")

        assert (package.name, package.description) == ("Finance", "Les comptes")
        assert package.created_at == package.updated_at == NOW
        assert package.parent_id is None

    def test_revise_keeps_what_is_omitted(self) -> None:
        later = NOW + timedelta(hours=1)
        revised = a_package().revise(now=later, description="neuf")

        assert (revised.name, revised.description, revised.updated_at) == ("Finance", "neuf", later)

    def test_moved_to_changes_only_the_parent(self) -> None:
        parent = a_package("Racine")
        moved = a_package().moved_to(parent.id, now=NOW + timedelta(hours=1))

        assert moved.parent_id == parent.id
        assert moved.name == "Finance"

    def test_the_unset_marker_is_not_none(self) -> None:
        assert isinstance(UNSET, Unset)
        assert UNSET is not None
        assert PackageCounts() == PackageCounts(elements=0, diagrams=0, children=0)


class TestPaths:
    def test_a_path_reads_from_the_root_down(self) -> None:
        finance = a_package("Finance")
        budget = a_package("Budget", finance)

        assert path_of(budget, tree(finance, budget)) == "Finance / Budget"

    def test_descendants_are_every_depth_below(self) -> None:
        a = a_package("A")
        b = a_package("B", a)
        c = a_package("C", b)
        d = a_package("D")

        assert descendants_of(a.id, tree(a, b, c, d)) == {b.id, c.id}
        assert descendants_of(c.id, tree(a, b, c, d)) == frozenset()


class TestTheShapeOfTheTree:
    def test_a_chain_at_the_maximum_depth_is_accepted(self) -> None:
        chain = [a_package("p0")]
        for level in range(1, MAX_PACKAGE_DEPTH):
            chain.append(a_package(f"p{level}", chain[-1]))

        check_tree(tree(*chain))

    def test_one_level_deeper_is_refused(self) -> None:
        chain = [a_package("p0")]
        for level in range(1, MAX_PACKAGE_DEPTH + 1):
            chain.append(a_package(f"p{level}", chain[-1]))

        with pytest.raises(PackageTooDeepError):
            check_tree(tree(*chain))

    def test_a_package_under_itself_is_a_cycle(self) -> None:
        a = a_package("A")

        with pytest.raises(CyclicPackageError):
            check_tree(tree(replace(a, parent_id=a.id)))

    def test_two_packages_under_each_other_are_a_cycle(self) -> None:
        a, b = a_package("A"), a_package("B")
        a = replace(a, parent_id=b.id)
        b = replace(b, parent_id=a.id)

        with pytest.raises(CyclicPackageError):
            check_tree(tree(a, b))

    def test_a_dangling_parent_is_treated_as_a_root(self) -> None:
        orphan = a_package("Orpheline", a_package("Disparu"))

        check_tree(tree(orphan))
```

Et `backend/tests/unit/test_error_statuses.py` :

```python
"""Every domain refusal has an HTTP status — otherwise it leaves as a bare 400."""

from __future__ import annotations

import pytest

from ea.api.errors import _STATUS
from ea.domain.errors import DomainError


def all_domain_errors() -> list[type[DomainError]]:
    found: list[type[DomainError]] = []
    frontier = list(DomainError.__subclasses__())
    while frontier:
        error = frontier.pop()
        found.append(error)
        frontier.extend(error.__subclasses__())
    return found


@pytest.mark.parametrize("error", all_domain_errors(), ids=lambda e: e.__name__)
def test_every_domain_error_has_a_row_in_the_status_table(error: type[DomainError]) -> None:
    assert error in _STATUS, f"{error.__name__} would be answered 400 invalid_request"
```

- [ ] **Step 2: Lancer et constater l'échec**

Run: `cd backend && uv run pytest tests/unit/test_packages.py -x`
Expected: FAIL — `ImportError: cannot import name 'CyclicPackageError' from 'ea.domain.errors'`.

- [ ] **Step 3: Ajouter les erreurs** — fin de `backend/src/ea/domain/errors.py`

```python


class PackageNotFoundError(DomainError):
    """A package referenced by id is absent from the store."""


class DuplicatePackageError(DomainError):
    """Two packages with the same parent would carry the same name (case ignored)."""


class CyclicPackageError(DomainError):
    """A package that would end up inside itself."""


class PackageNotEmptyError(DomainError):
    """A package still holds sub-packages, elements or diagrams.

    Refused rather than cascaded: deleting elements to tidy a folder would
    destroy facts of the graph on a click.
    """


class PackageTooDeepError(DomainError):
    """A tree deeper than `MAX_PACKAGE_DEPTH` levels."""


class TooManyPackagesError(DomainError):
    """The tree is served in one piece, so it is capped at `MAX_PACKAGES`."""


class UnknownPackageError(DomainError):
    """A stored query names a package that no longer exists.

    Its own error rather than `PackageNotFoundError`: that one is a 404 for the
    resource in the URL, and here the query exists — it is its content that
    went stale, which is a 422.
    """
```

- [ ] **Step 4: Écrire l'implémentation** — `backend/src/ea/domain/packages.py`

```python
"""The classification tree of the catalogue: packages, as in Sparx EA's Project Browser.

A package *files* elements and diagrams, it models nothing: it has no
relationship and no ArchiMate type, and it never appears in a traversal. That
is what separates it from the ArchiMate `grouping`, which is a fact of the
graph. An element sits in at most one package; outside any, it is "at the root".

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from ea.domain.errors import CyclicPackageError, PackageTooDeepError

#: A root package is level 1.
MAX_PACKAGE_DEPTH: Final = 12
#: The tree is served flat and in one piece (spec §11, Q6).
MAX_PACKAGES: Final = 2000
#: Elements moved in one call.
MAX_MOVE_BATCH: Final = 100
MAX_PACKAGE_NAME_LENGTH: Final = 200
PATH_SEPARATOR: Final = " / "


class Unset:
    """'Not given' — distinct from `None`, which means 'the root'.

    A `PATCH` body and `?move_contents_to=` need three answers: leave it,
    put it at the root, put it there.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def validate_package_name(name: str) -> str:
    """Trimmed, 1..200 characters, no `/` so that a path reads `A / B / C`."""
    cleaned = name.strip()
    if not cleaned:
        msg = "a package name cannot be blank"
        raise ValueError(msg)
    if len(cleaned) > MAX_PACKAGE_NAME_LENGTH:
        msg = f"a package name is at most {MAX_PACKAGE_NAME_LENGTH} characters"
        raise ValueError(msg)
    if "/" in cleaned:
        msg = "a package name cannot contain '/'"
        raise ValueError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class Package:
    id: UUID
    parent_id: UUID | None
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        parent_id: UUID | None = None,
        description: str = "",
    ) -> Package:
        return cls(
            id=uuid4(),
            parent_id=parent_id,
            name=validate_package_name(name),
            description=description.strip(),
            created_at=now,
            updated_at=now,
        )

    def revise(
        self, *, now: datetime, name: str | None = None, description: str | None = None
    ) -> Package:
        """The same package under a new name or description; an omitted one is kept."""
        return replace(
            self,
            name=self.name if name is None else validate_package_name(name),
            description=self.description if description is None else description.strip(),
            updated_at=now,
        )

    def moved_to(self, parent_id: UUID | None, *, now: datetime) -> Package:
        return replace(self, parent_id=parent_id, updated_at=now)


@dataclass(frozen=True, slots=True)
class PackageCounts:
    """What a package holds directly. Read, never written: the rows are the truth."""

    elements: int = 0
    diagrams: int = 0
    children: int = 0


@dataclass(frozen=True, slots=True)
class PackageSummary:
    package: Package
    path: str
    counts: PackageCounts


def check_tree(packages: Mapping[UUID, Package]) -> None:
    """Refuse a cycle or a tree deeper than `MAX_PACKAGE_DEPTH`.

    Run on the *resulting* tree, before it is written: the same call guards a
    creation, a move and the re-parenting a deletion does. A parent that is not
    in the mapping ends the walk — the database's foreign key is what owns that.
    """
    for package in packages.values():
        seen = {package.id}
        depth = 1
        current = package
        while current.parent_id is not None:
            if current.parent_id in seen:
                msg = f"{package.name!r} would end up inside itself"
                raise CyclicPackageError(msg)
            parent = packages.get(current.parent_id)
            if parent is None:
                break
            seen.add(parent.id)
            depth += 1
            if depth > MAX_PACKAGE_DEPTH:
                msg = f"packages nest at most {MAX_PACKAGE_DEPTH} levels deep"
                raise PackageTooDeepError(msg)
            current = parent


def path_of(package: Package, packages: Mapping[UUID, Package]) -> str:
    """`Finance / Budget` — names from the root down."""
    names = [package.name]
    current = package
    for _ in range(MAX_PACKAGE_DEPTH):
        if current.parent_id is None:
            break
        parent = packages.get(current.parent_id)
        if parent is None:
            break
        names.append(parent.name)
        current = parent
    return PATH_SEPARATOR.join(reversed(names))


def descendants_of(package_id: UUID, packages: Mapping[UUID, Package]) -> frozenset[UUID]:
    """Every package below this one, at any depth."""
    children: dict[UUID, list[UUID]] = {}
    for package in packages.values():
        if package.parent_id is not None:
            children.setdefault(package.parent_id, []).append(package.id)
    found: set[UUID] = set()
    frontier = [package_id]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return frozenset(found)
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_packages.py -x`
Expected: PASS.

`test_error_statuses.py` reste rouge tant que la table `_STATUS` n'est pas complétée (Task 7) : lancer `uv run pytest tests/unit/test_error_statuses.py -q` et constater 7 échecs `… would be answered 400 invalid_request` ; il passe à la Task 7. Ne pas le committer dans ce commit (il l'est avec la Task 7).

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/packages.py backend/src/ea/domain/errors.py backend/tests/unit/test_packages.py
git commit -m "feat(domain): add the package tree rules and their errors" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `Element.package_id`, `ElementFilter` et le port des paquets

**Files:**
- Modify: `backend/src/ea/domain/model.py` (classe `Element`, ≈ l.58-66 ; constante `_PROPERTY_KEY` ≈ l.37 et son usage dans `_freeze`)
- Modify: `backend/src/ea/domain/ports.py` (`ElementFilter` l.43-52 ; ajout de `PackageRepository` après `DiagramRepository`, ≈ l.226)
- Modify: `backend/src/ea/services/architecture.py` (`update_element`, imports)
- Test: `backend/tests/unit/test_architecture_service.py` (ajout en fin de fichier)

**Interfaces:**
- Consumes: `ea.domain.packages.Package`, `PackageCounts` (Task 1).
- Produces:
  - `Element.package_id: UUID | None = None` (dernier champ, après `properties`).
  - `ea.domain.model.PROPERTY_KEY` (le motif compilé, désormais public ; sert à `domain/query.py`).
  - `ElementFilter(element_types=(), layers=(), search=None, package_id=None, include_subpackages=True, root_only=False, limit=50, offset=0)`.
  - `PackageRepository` (Protocol) : `list_all() -> tuple[Package, ...]`, `counts() -> dict[UUID, PackageCounts]`, `get(package_id) -> Package | None`, `add(package) -> Package`, `save(package) -> Package`, `delete(package_id, *, into: UUID | None) -> bool`, `move_elements(element_ids: Sequence[UUID], package_id: UUID | None) -> None`.

- [ ] **Step 1: Écrire le test qui échoue** — ajouter à `backend/tests/unit/test_architecture_service.py`

D'abord vérifier les imports déjà présents en tête du fichier (`pytest`, `E`, `ArchitectureService`) ; ajouter `from dataclasses import replace`, `from uuid import uuid4` et `from tests.conftest import InMemoryRepository` s'ils manquent, puis :

```python
@pytest.mark.asyncio
class TestAnElementKeepsItsPackage:
    async def test_updating_a_description_keeps_the_package(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        created = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        package_id = uuid4()
        repository.elements[created.id] = replace(created, package_id=package_id)

        updated = await service.update_element(created.id, description="Les factures")

        assert updated.package_id == package_id

    async def test_renaming_and_replacing_properties_keep_the_package(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        created = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        package_id = uuid4()
        repository.elements[created.id] = replace(created, package_id=package_id)

        renamed = await service.update_element(
            created.id, name="Billing 2", properties={"owner": "x"}
        )

        assert renamed.package_id == package_id

    async def test_a_new_element_is_at_the_root(self, service: ArchitectureService) -> None:
        created = await service.create_element(element_type=E.NODE, name="db-01")

        assert created.package_id is None
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_architecture_service.py -x -k TestAnElementKeepsItsPackage`
Expected: FAIL — `TypeError: Element.__init__() got an unexpected keyword argument 'package_id'`.

- [ ] **Step 3: Ajouter le champ** — dans `domain/model.py`, classe `Element`, après `properties`:

```python
    properties: Mapping[str, str] = field(default=_EMPTY_PROPERTIES)
    #: The package that files this element; `None` is "at the root". Not one of
    #: the element's facts: `update_element` keeps it, only a move changes it.
    package_id: UUID | None = None
```

et rendre public le motif de clé : remplacer `_PROPERTY_KEY` par `PROPERTY_KEY` (déclaration et l'unique usage dans `_freeze`).

Dans `domain/ports.py`, remplacer la classe `ElementFilter` :

```python
@dataclass(frozen=True, slots=True)
class ElementFilter:
    """The ways the catalogue can be narrowed. All criteria are combined with AND."""

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    package_id: UUID | None = None
    include_subpackages: bool = True
    root_only: bool = False
    limit: int = 50
    offset: int = 0
```

et, après `DiagramRepository`, ajouter (avec `from ea.domain.packages import Package, PackageCounts` dans les imports de tête) :

```python
class PackageRepository(Protocol):
    """Persistence for the classification tree."""

    async def list_all(self) -> tuple[Package, ...]:
        """Every package, sorted by lower-cased name."""
        ...

    async def counts(self) -> dict[UUID, PackageCounts]:
        """What each package holds directly. A package holding nothing may be absent."""
        ...

    async def get(self, package_id: UUID) -> Package | None: ...

    async def add(self, package: Package) -> Package:
        """Store a new package, or refuse a sibling name already taken."""
        ...

    async def save(self, package: Package) -> Package:
        """Overwrite name, description, parent and update time of a stored package."""
        ...

    async def delete(self, package_id: UUID, *, into: UUID | None) -> bool:
        """Move the direct sub-packages, elements and diagrams to `into`, then delete.

        `into=None` is the root. One transaction: a sibling name taken at the
        destination refuses everything and moves nothing. `False` if the
        package is gone.
        """
        ...

    async def move_elements(self, element_ids: Sequence[UUID], package_id: UUID | None) -> None:
        """Put these elements in a package (`None`: the root), all or none.

        Raises `ElementNotFoundError` naming an absent id and
        `PackageNotFoundError` for an absent package; nothing is moved then.
        """
        ...
```

- [ ] **Step 4: Relancer** — Expected: FAIL (assertion) : `assert None == UUID(...)` (la reconstruction manuelle de `update_element` perd `package_id`).

- [ ] **Step 5: Corriger `update_element`** — dans `services/architecture.py`, ajouter `from dataclasses import replace` aux imports, puis remplacer le bloc `if description is not None or documentation is not None:`

```python
        if description is not None or documentation is not None:
            updated = replace(
                updated,
                updated_at=now,
                description=(
                    description.strip() if description is not None else updated.description
                ),
                documentation=(
                    documentation.strip() if documentation is not None else updated.documentation
                ),
            )
```

- [ ] **Step 6: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_architecture_service.py -x && uv run mypy src`
Expected: PASS ; mypy propre.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/domain/model.py backend/src/ea/domain/ports.py backend/src/ea/services/architecture.py backend/tests/unit/test_architecture_service.py
git commit -m "feat(domain): let an element carry a package and filter by it" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Tables `packages` et migration 0009

**Files:**
- Create: `backend/src/ea/db/models/package.py`
- Create: `backend/migrations/versions/0009_packages.py` (à renuméroter si une autre migration est entrée avant)
- Modify: `backend/src/ea/db/models/architecture.py` (`ElementRecord`, après la colonne `properties`), `backend/src/ea/db/models/diagram.py` (`DiagramRecord`, après `description`), `backend/src/ea/db/models/__init__.py`
- Test: `backend/tests/unit/test_migrations.py` (ajout), `backend/tests/integration/test_packages_schema.py`

**Interfaces:**
- Consumes: —
- Produces: `PackageRecord` (table `packages`) ; `ElementRecord.package_id`, `DiagramRecord.package_id` ; revision `0009` (`down_revision = "0008"`).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajout à `backend/tests/unit/test_migrations.py` :

```python
def test_revision_0009_follows_0008() -> None:
    revision = ScriptDirectory.from_config(_config()).get_revision("0009")

    assert revision is not None
    assert revision.down_revision == "0008"


def test_the_packages_table_declares_its_two_sibling_indexes_by_name() -> None:
    table = Base.metadata.tables["packages"]
    indexes = {index.name: index for index in table.indexes}

    assert set(indexes) == {"uq_packages_root_name", "uq_packages_child_name"}
    assert all(index.unique for index in indexes.values())


def test_elements_and_diagrams_restrict_the_deletion_of_their_package() -> None:
    for name in ("elements", "diagrams"):
        column = Base.metadata.tables[name].c.package_id
        (foreign_key,) = column.foreign_keys
        assert column.nullable
        assert foreign_key.ondelete == "RESTRICT"
        assert foreign_key.column.table.name == "packages"
```

`backend/tests/integration/test_packages_schema.py` :

```python
"""Revision 0009 as the server holds it: sibling names, RESTRICT, and a populated base."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

ELEMENT = "00000000-0000-0000-0000-0000000000e1"
DIAGRAM = "00000000-0000-0000-0000-0000000000d1"


async def a_package(engine: AsyncEngine, name: str, parent: str | None = None) -> str:
    async with engine.begin() as connection:
        found = await connection.execute(
            text(
                "INSERT INTO packages (id, parent_id, name, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :parent, :name, now(), now()) RETURNING id"
            ),
            {"parent": parent, "name": name},
        )
        return str(found.scalar_one())


async def test_a_populated_base_upgrades_with_everything_at_the_root(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0008")
    try:
        async with postgres_engine.begin() as connection:
            # Test setup with literal values only: an element and a diagram that predate packages.
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    f"updated_at) VALUES ('{ELEMENT}', 'node', 'technology', 'active_structure', "
                    "'db-01', now(), now())"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO diagrams (id, name, created_at, updated_at) "
                    f"VALUES ('{DIAGRAM}', 'Vente', now(), now())"
                )
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0009")

        async with postgres_engine.connect() as connection:
            # Catalogue reads with no runtime value: nothing to bind.
            element = await connection.execute(text("SELECT package_id FROM elements"))
            diagram = await connection.execute(text("SELECT package_id FROM diagrams"))
        assert element.scalar_one() is None
        assert diagram.scalar_one() is None

        await asyncio.to_thread(command.downgrade, alembic_config, "0008")
        async with postgres_engine.connect() as connection:
            kept = await connection.execute(text("SELECT count(*) FROM elements"))
        assert kept.scalar_one() == 1
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_two_root_packages_cannot_differ_only_by_case(engine_at_head: AsyncEngine) -> None:
    await a_package(engine_at_head, "Finance")

    with pytest.raises(IntegrityError, match="uq_packages_root_name"):
        await a_package(engine_at_head, "finance")


async def test_two_siblings_cannot_differ_only_by_case(engine_at_head: AsyncEngine) -> None:
    parent = await a_package(engine_at_head, "Finance")
    await a_package(engine_at_head, "Budget", parent)

    with pytest.raises(IntegrityError, match="uq_packages_child_name"):
        await a_package(engine_at_head, "BUDGET", parent)


async def test_the_same_name_is_fine_under_another_parent_or_at_the_root(
    engine_at_head: AsyncEngine,
) -> None:
    finance = await a_package(engine_at_head, "Finance")
    rh = await a_package(engine_at_head, "RH")
    await a_package(engine_at_head, "Budget", finance)
    await a_package(engine_at_head, "Budget", rh)
    await a_package(engine_at_head, "Budget")


async def test_a_package_that_holds_an_element_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    package = await a_package(engine_at_head, "Finance")
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO elements (id, element_type, layer, aspect, name, package_id, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                "'active_structure', 'db-01', :package, now(), now())"
            ),
            {"package": package},
        )

    with pytest.raises(IntegrityError, match="fk_elements_package_id_packages"):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM packages WHERE id = :id"), {"id": package})


async def test_a_package_that_holds_a_package_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    parent = await a_package(engine_at_head, "Finance")
    await a_package(engine_at_head, "Budget", parent)

    with pytest.raises(IntegrityError, match="fk_packages_parent_id_packages"):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM packages WHERE id = :id"), {"id": parent})
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x -k "0009 or packages or restrict"`
Expected: FAIL — `KeyError: 'packages'` ; `get_revision("0009")` renvoie `None`.

- [ ] **Step 3: Modèles** — `backend/src/ea/db/models/package.py`

```python
"""The classification tree of the catalogue.

A package is a row pointing at its parent. The foreign keys that name it —
`packages.parent_id`, `elements.package_id`, `diagrams.package_id` — are all
`RESTRICT`: deleting a package never deletes (or silently relocates) what it
holds; the service decides what becomes of the contents and says so.

Sibling names are unique ignoring case, and that takes two partial indexes
rather than one `NULLS NOT DISTINCT` constraint, which would tie the schema to
the PostgreSQL version of the Infra.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class PackageRecord(Base):
    __tablename__ = "packages"
    __table_args__ = (
        # Static DDL text, no runtime value.
        Index(
            "uq_packages_root_name",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_packages_child_name",
            "parent_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Dans `ElementRecord` (`db/models/architecture.py`), après `properties` :

```python
    package_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
```

Dans `DiagramRecord` (`db/models/diagram.py`), après `description` (`ForeignKey` est déjà importé) :

```python
    package_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
```

Dans `db/models/__init__.py` : `from ea.db.models.package import PackageRecord`, et `"PackageRecord"` dans `__all__` (ordre alphabétique).

- [ ] **Step 4: Migration** — `backend/migrations/versions/0009_packages.py`

```python
"""les paquets : un arbre de classement pour les éléments et les diagrammes

Revision ID: 0009
Revises: 0008
Date: 2026-10-10

`packages` est une liste d'adjacence (`parent_id`). `elements.package_id` et
`diagrams.package_id` sont nullables : tout ce qui existe déjà reste « à la
racine », aucune donnée n'est réécrite. Toutes les clés sont `RESTRICT` — la
suppression d'un paquet ne supprime ni ne déplace jamais son contenu, le
service décide et le dit.

Les noms de frères sont uniques sans tenir compte de la casse, ce qui demande
deux index partiels (racine / enfants) plutôt qu'un `NULLS NOT DISTINCT`,
lequel dépendrait de la version de PostgreSQL de l'Infra.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["packages.id"],
            name=op.f("fk_packages_parent_id_packages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packages")),
    )
    # DDL statique, aucune valeur d'exécution.
    op.create_index(
        "uq_packages_root_name",
        "packages",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "uq_packages_child_name",
        "packages",
        ["parent_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    for table in ("elements", "diagrams"):
        op.add_column(table, sa.Column("package_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_package_id_packages"),
            table,
            "packages",
            ["package_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(op.f(f"ix_{table}_package_id"), table, ["package_id"], unique=False)


def downgrade() -> None:
    for table in ("diagrams", "elements"):
        op.drop_index(op.f(f"ix_{table}_package_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_package_id_packages"), table, type_="foreignkey")
        op.drop_column(table, "package_id")
    op.drop_index("uq_packages_child_name", table_name="packages")
    op.drop_index("uq_packages_root_name", table_name="packages")
    op.drop_table("packages")
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x && uv run mypy src migrations`
Expected: PASS. Puis sur base jetable : `make pg-up && cd backend && uv run pytest tests/integration/test_packages_schema.py -x` (sans conteneur, les tests se *skippent* : exiger un vrai passage via `make test-integration`). Expected: 6 PASS. Relire la révision contre `Base.metadata` (noms d'index et de FK identiques à ceux des modèles).

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/db backend/migrations/versions/0009_packages.py backend/tests/unit/test_migrations.py backend/tests/integration/test_packages_schema.py
git commit -m "feat(db): add packages and the package_id of elements and diagrams (0009)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Dépôt PostgreSQL des paquets et filtre d'éléments par paquet

**Files:**
- Create: `backend/src/ea/repositories/package_store.py`
- Modify: `backend/src/ea/repositories/architecture_store.py` (imports ≈ l.28-45 ; `element_from_row` l.89-99 ; `_matching` l.197-207)
- Test: `backend/tests/integration/test_package_store.py`

**Interfaces:**
- Consumes: `PackageRecord`, `ElementRecord.package_id`, `DiagramRecord.package_id` (Task 3) ; `PackageRepository` (Task 2) ; `constraint_of` de `architecture_store`.
- Produces: `PostgresPackageRepository(sessions: async_sessionmaker[AsyncSession])` implémentant `PackageRepository` ; `architecture_store._subtree(package_id) -> CTE` ; `element_from_row` renseigne `package_id` ; `_matching` honore `package_id`, `include_subpackages`, `root_only`.

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/integration/test_package_store.py`

```python
"""The package repository and the element filter by package, against a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    DuplicatePackageError,
    ElementNotFoundError,
    PackageNotFoundError,
)
from ea.domain.model import Element
from ea.domain.packages import Package
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.package_store import PostgresPackageRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, engine: AsyncEngine) -> None:
        sessions = create_session_factory(engine)
        self.packages = PostgresPackageRepository(sessions)
        self.graph = PostgresArchitectureRepository(sessions)

    async def package(self, name: str, parent: Package | None = None) -> Package:
        return await self.packages.add(
            Package.create(name=name, parent_id=parent.id if parent else None, now=NOW)
        )

    async def element(self, name: str, package: Package | None = None) -> Element:
        stored = await self.graph.add_element(
            Element.create(element_type=E.NODE, name=name, now=NOW)
        )
        if package is not None:
            await self.packages.move_elements([stored.id], package.id)
        return stored


@pytest.fixture
def world(engine_at_head: AsyncEngine) -> World:
    return World(engine_at_head)


async def names(world: World, **criteria: object) -> list[str]:
    wanted = ElementFilter(**criteria)  # type: ignore[arg-type]
    found = await world.graph.list_elements(wanted)
    assert await world.graph.count_elements(wanted) == len(found)
    return [element.name for element in found]


class TestSiblingNames:
    async def test_a_taken_root_name_is_refused_whatever_its_case(self, world: World) -> None:
        await world.package("Finance")

        with pytest.raises(DuplicatePackageError):
            await world.package("FINANCE")

    async def test_a_taken_child_name_is_refused_under_the_same_parent_only(
        self, world: World
    ) -> None:
        finance, rh = await world.package("Finance"), await world.package("RH")
        await world.package("Budget", finance)
        await world.package("Budget", rh)

        with pytest.raises(DuplicatePackageError):
            await world.package("budget", finance)

    async def test_renaming_onto_a_sibling_is_refused(self, world: World) -> None:
        await world.package("A")
        b = await world.package("B")

        with pytest.raises(DuplicatePackageError):
            await world.packages.save(b.revise(name="a", now=NOW))

    async def test_a_parent_that_is_gone_is_a_not_found(self, world: World) -> None:
        ghost = Package.create(name="Fantôme", now=NOW)

        with pytest.raises(PackageNotFoundError):
            await world.packages.add(Package.create(name="X", parent_id=ghost.id, now=NOW))


class TestListingAndCounting:
    async def test_counts_are_direct_not_recursive(self, world: World) -> None:
        finance = await world.package("Finance")
        budget = await world.package("Budget", finance)
        await world.element("a", finance)
        await world.element("b", budget)
        await world.element("c", budget)

        counts = await world.packages.counts()

        assert (counts[finance.id].elements, counts[finance.id].children) == (1, 1)
        assert (counts[budget.id].elements, counts[budget.id].diagrams) == (2, 0)

    async def test_list_all_is_sorted_ignoring_case(self, world: World) -> None:
        await world.package("beta")
        await world.package("Alpha")

        assert [p.name for p in await world.packages.list_all()] == ["Alpha", "beta"]


class TestDeleting:
    async def test_the_contents_move_up_in_the_same_transaction(self, world: World) -> None:
        finance = await world.package("Finance")
        budget = await world.package("Budget", finance)
        await world.element("a", finance)
        await world.element("b", budget)

        assert await world.packages.delete(finance.id, into=None) is True

        assert await world.packages.get(finance.id) is None
        moved = await world.packages.get(budget.id)
        assert moved is not None
        assert moved.parent_id is None
        assert await names(world, root_only=True) == ["a"]

    async def test_a_sibling_name_taken_at_the_destination_moves_nothing(
        self, world: World
    ) -> None:
        finance = await world.package("Finance")
        await world.package("Budget", finance)
        await world.package("Budget")  # a root "Budget" already
        await world.element("a", finance)

        with pytest.raises(DuplicatePackageError):
            await world.packages.delete(finance.id, into=None)

        assert await world.packages.get(finance.id) is not None
        assert await names(world, package_id=finance.id, include_subpackages=False) == ["a"]

    async def test_an_unknown_package_is_not_deleted(self, world: World) -> None:
        assert await world.packages.delete(uuid4(), into=None) is False


class TestMovingElements:
    async def test_all_or_none(self, world: World) -> None:
        finance = await world.package("Finance")
        kept = await world.element("a")

        with pytest.raises(ElementNotFoundError):
            await world.packages.move_elements([kept.id, uuid4()], finance.id)

        stored = await world.graph.get_element(kept.id)
        assert stored is not None
        assert stored.package_id is None

    async def test_to_a_package_that_is_gone_is_a_not_found(self, world: World) -> None:
        element = await world.element("a")

        with pytest.raises(PackageNotFoundError):
            await world.packages.move_elements([element.id], uuid4())

    async def test_back_to_the_root(self, world: World) -> None:
        finance = await world.package("Finance")
        element = await world.element("a", finance)

        await world.packages.move_elements([element.id], None)

        stored = await world.graph.get_element(element.id)
        assert stored is not None
        assert stored.package_id is None

    async def test_saving_an_element_read_before_a_move_does_not_undo_the_move(
        self, world: World
    ) -> None:
        finance = await world.package("Finance")
        stale = await world.element("a")
        await world.packages.move_elements([stale.id], finance.id)

        await world.graph.save_element(stale.rename("a2", now=NOW))

        stored = await world.graph.get_element(stale.id)
        assert stored is not None
        assert stored.package_id == finance.id


class TestTheFilter:
    async def seed(self, world: World) -> Package:
        a = await world.package("A")
        b = await world.package("B", a)
        c = await world.package("C", b)
        await world.element("in-a", a)
        await world.element("in-b", b)
        await world.element("in-c", c)
        await world.element("at-root")
        return a

    async def test_a_package_includes_its_subtree_by_default(self, world: World) -> None:
        a = await self.seed(world)

        assert sorted(await names(world, package_id=a.id)) == ["in-a", "in-b", "in-c"]

    async def test_without_subpackages_only_the_package_itself(self, world: World) -> None:
        a = await self.seed(world)

        assert await names(world, package_id=a.id, include_subpackages=False) == ["in-a"]

    async def test_a_middle_package_takes_what_is_below_it(self, world: World) -> None:
        await self.seed(world)
        b = next(p for p in await world.packages.list_all() if p.name == "B")

        assert sorted(await names(world, package_id=b.id)) == ["in-b", "in-c"]

    async def test_root_only_keeps_the_unfiled(self, world: World) -> None:
        await self.seed(world)

        assert await names(world, root_only=True) == ["at-root"]

    async def test_an_unknown_package_matches_nothing(self, world: World) -> None:
        await self.seed(world)

        assert await names(world, package_id=uuid4()) == []
```

- [ ] **Step 2: Lancer**

Run: `make pg-up && cd backend && uv run pytest tests/integration/test_package_store.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.repositories.package_store'`.

- [ ] **Step 3: Implémenter** — `backend/src/ea/repositories/package_store.py`

```python
"""The PostgreSQL implementation of `PackageRepository`.

One unit of work per call, like the other stores. The refusals are translated by
the **name** of the constraint (`constraint_of`), never by its message: the
sibling-name rule is two partial unique indexes, the references to a package are
three `RESTRICT` foreign keys, and a lookup-then-insert would let two callers
both find a name free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord
from ea.db.models.diagram import DiagramRecord
from ea.db.models.package import PackageRecord
from ea.domain.errors import (
    DomainError,
    DuplicatePackageError,
    ElementNotFoundError,
    PackageNotEmptyError,
    PackageNotFoundError,
)
from ea.domain.packages import Package, PackageCounts
from ea.repositories.architecture_store import constraint_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NAME_TAKEN: Final = ("uq_packages_root_name", "uq_packages_child_name")
#: The three foreign keys that name a package.
REFERENCES: Final = (
    "fk_packages_parent_id_packages",
    "fk_elements_package_id_packages",
    "fk_diagrams_package_id_packages",
)


def _to_package(row: PackageRecord) -> Package:
    return Package(
        id=row.id,
        parent_id=row.parent_id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _refusal(error: IntegrityError, name: str, *, deleting: bool = False) -> DomainError | None:
    """The refusal this constraint means, or `None` for one we do not know."""
    constraint = constraint_of(error)
    if constraint in NAME_TAKEN:
        return DuplicatePackageError(f"a package is already named {name!r} in that place")
    if constraint in REFERENCES:
        if deleting:
            return PackageNotEmptyError(
                "the package still holds sub-packages, elements or diagrams"
            )
        return PackageNotFoundError("the package does not exist")
    return None


async def _count_by(session: AsyncSession, column: Any) -> dict[UUID, int]:
    """`{package id: rows}` for a column that names a package."""
    statement = select(column, func.count()).where(column.is_not(None)).group_by(column)
    return {key: total for key, total in (await session.execute(statement)).tuples().all()}


class PostgresPackageRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[Package, ...]:
        statement = select(PackageRecord).order_by(func.lower(PackageRecord.name), PackageRecord.id)
        async with self._sessions() as session:
            return tuple(_to_package(row) for row in await session.scalars(statement))

    async def counts(self) -> dict[UUID, PackageCounts]:
        async with self._sessions() as session:
            elements = await _count_by(session, ElementRecord.package_id)
            diagrams = await _count_by(session, DiagramRecord.package_id)
            children = await _count_by(session, PackageRecord.parent_id)
        return {
            key: PackageCounts(
                elements=elements.get(key, 0),
                diagrams=diagrams.get(key, 0),
                children=children.get(key, 0),
            )
            for key in {*elements, *diagrams, *children}
        }

    async def get(self, package_id: UUID) -> Package | None:
        async with self._sessions() as session:
            row = await session.get(PackageRecord, package_id)
            return _to_package(row) if row is not None else None

    async def add(self, package: Package) -> Package:
        async with self._sessions.begin() as session:
            session.add(
                PackageRecord(
                    id=package.id,
                    parent_id=package.parent_id,
                    name=package.name,
                    description=package.description,
                    created_at=package.created_at,
                    updated_at=package.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                refusal = _refusal(error, package.name)
                if refusal is None:
                    raise
                raise refusal from error
        return package

    async def save(self, package: Package) -> Package:
        async with self._sessions.begin() as session:
            row = await session.get(PackageRecord, package.id)
            if row is None:
                msg = f"no package with id {package.id}"
                raise PackageNotFoundError(msg)
            row.name = package.name
            row.description = package.description
            row.parent_id = package.parent_id
            row.updated_at = package.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                refusal = _refusal(error, package.name)
                if refusal is None:
                    raise
                raise refusal from error
        return package

    async def delete(self, package_id: UUID, *, into: UUID | None) -> bool:
        async with self._sessions.begin() as session:
            row = await session.get(PackageRecord, package_id, with_for_update=True)
            if row is None:
                return False
            try:
                await session.execute(
                    update(PackageRecord)
                    .where(PackageRecord.parent_id == package_id)
                    .values(parent_id=into)
                )
                await session.execute(
                    update(ElementRecord)
                    .where(ElementRecord.package_id == package_id)
                    .values(package_id=into)
                )
                await session.execute(
                    update(DiagramRecord)
                    .where(DiagramRecord.package_id == package_id)
                    .values(package_id=into)
                )
                deleted = await session.execute(
                    delete(PackageRecord).where(PackageRecord.id == package_id)
                )
            except IntegrityError as error:
                refusal = _refusal(error, row.name, deleting=True)
                if refusal is None:
                    raise
                raise refusal from error
        return bool(cast("CursorResult[Any]", deleted).rowcount)

    async def move_elements(self, element_ids: Sequence[UUID], package_id: UUID | None) -> None:
        wanted = tuple(dict.fromkeys(element_ids))
        async with self._sessions.begin() as session:
            found = set(
                await session.scalars(select(ElementRecord.id).where(ElementRecord.id.in_(wanted)))
            )
            missing = [str(element_id) for element_id in wanted if element_id not in found]
            if missing:
                msg = f"no element with id {', '.join(missing)}"
                raise ElementNotFoundError(msg)
            try:
                await session.execute(
                    update(ElementRecord)
                    .where(ElementRecord.id.in_(wanted))
                    .values(package_id=package_id)
                    .execution_options(synchronize_session=False)
                )
            except IntegrityError as error:
                refusal = _refusal(error, "")
                if refusal is None:
                    raise
                raise refusal from error
```

Dans `architecture_store.py` : ajouter `from ea.db.models.package import PackageRecord` aux imports ; dans `element_from_row`, ajouter `package_id=row.package_id,` ; ajouter après `_text_of` :

```python
def _subtree(package_id: UUID) -> CTE:
    """The package and every package below it, by a recursive CTE on `parent_id`.

    The id is a bound parameter; the depth is bounded by `MAX_PACKAGE_DEPTH`
    on write, so the walk always ends.
    """
    tree = (
        select(PackageRecord.id.label("id"))
        .where(PackageRecord.id == package_id)
        .cte("subtree", recursive=True)
    )
    below = select(PackageRecord.id).join(tree, PackageRecord.parent_id == tree.c.id)
    return tree.union(below)
```

et dans `_matching`, avant `return clauses` :

```python
    if criteria.root_only:
        clauses.append(ElementRecord.package_id.is_(None))
    if criteria.package_id is not None:
        if criteria.include_subpackages:
            clauses.append(ElementRecord.package_id.in_(select(_subtree(criteria.package_id).c.id)))
        else:
            clauses.append(ElementRecord.package_id == criteria.package_id)
```

`element_row` **ne change pas** (écart 1) : `ElementRecord(**element_row(element))` laisse `package_id` à `NULL`, et `save_element` n'y touche pas.

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/integration/test_package_store.py -x && uv run mypy src && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS ; si `ruff format --check` échoue, `make lint` puis relire le diff.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories backend/tests/integration/test_package_store.py
git commit -m "feat(store): add the package repository and filter elements by package" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Les diagrammes se rangent dans un paquet

**Files:**
- Modify: `backend/src/ea/domain/diagrams.py` (`Diagram`, `Diagram.create`, `Diagram.revise`, imports)
- Modify: `backend/src/ea/domain/ports.py` (`DiagramRepository.list_all`, import `Collection`)
- Modify: `backend/src/ea/repositories/diagram_store.py` (`_to_diagram`, `list_all`, `add`, `save`)
- Modify: `backend/src/ea/services/diagrams.py` (`__init__`, `list_diagrams`, `create`, `update`)
- Modify: `backend/tests/conftest.py` (`InMemoryDiagrams.list_all`, nouvelle classe `InMemoryPackages`, fixtures `packages` et `diagram_service`)
- Test: `backend/tests/unit/test_diagram_service.py` (ajout), `backend/tests/integration/test_diagram_store.py` (ajout)

**Interfaces:**
- Consumes: `Package`, `PackageCounts`, `Unset`, `UNSET`, `descendants_of` (Task 1) ; `PackageRepository` (Task 2) ; `PackageRecord`, `DiagramRecord.package_id` (Task 3) ; `constraint_of` (existant).
- Produces:
  - `Diagram.package_id: UUID | None = None` (après `node_count`) ; `Diagram.create(*, name, now, description="", package_id=None)` ; `Diagram.revise(*, now, name=None, description=None, package_id: UUID | None | Unset = UNSET)`.
  - `DiagramRepository.list_all(*, package_ids: Collection[UUID] | None = None, root_only: bool = False) -> tuple[Diagram, ...]`.
  - `DiagramService(repository, architecture, *, packages: PackageRepository | None = None, clock=...)` ; `list_diagrams(*, package_id: UUID | None = None, include_subpackages: bool = True, root_only: bool = False)` ; `create(*, name, description="", package_id: UUID | None = None)` ; `update(diagram_id, *, name=None, description=None, package_id: UUID | None | Unset = UNSET)`.
  - `tests.conftest.InMemoryPackages(graph: InMemoryRepository, diagrams: InMemoryDiagrams)` : un `PackageRepository` en mémoire (attribut `.packages: dict[UUID, Package]`) ; fixture `packages`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `backend/tests/unit/test_diagram_service.py` (imports : `from ea.domain.errors import PackageNotFoundError`, `from ea.domain.packages import Package`, `from tests.conftest import InMemoryPackages`, `import pytest` déjà là) :

```python
async def a_package(packages: InMemoryPackages, name: str, parent: Package | None = None) -> Package:
    return await packages.add(
        Package.create(name=name, parent_id=parent.id if parent else None, now=FIXED_NOW)
    )


@pytest.mark.asyncio
class TestDiagramsInPackages:
    async def test_a_diagram_is_created_in_a_package(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")

        diagram = await diagram_service.create(name="Vente", package_id=finance.id)

        assert diagram.package_id == finance.id

    async def test_a_package_that_does_not_exist_is_a_not_found(
        self, diagram_service: DiagramService
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await diagram_service.create(name="Vente", package_id=uuid4())

    async def test_updating_leaves_the_package_unless_it_is_given(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")
        diagram = await diagram_service.create(name="Vente", package_id=finance.id)

        renamed = await diagram_service.update(diagram.id, name="Ventes")
        assert renamed.package_id == finance.id

        at_the_root = await diagram_service.update(diagram.id, package_id=None)
        assert at_the_root.package_id is None

        moved_again = await diagram_service.update(diagram.id, package_id=finance.id)
        assert moved_again.package_id == finance.id

    async def test_listing_by_package_takes_the_subpackages_unless_told_otherwise(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")
        budget = await a_package(packages, "Budget", finance)
        await diagram_service.create(name="Racine")
        await diagram_service.create(name="Compta", package_id=finance.id)
        await diagram_service.create(name="Prévisions", package_id=budget.id)

        with_children = await diagram_service.list_diagrams(package_id=finance.id)
        alone = await diagram_service.list_diagrams(package_id=finance.id, include_subpackages=False)
        unfiled = await diagram_service.list_diagrams(root_only=True)

        assert [d.name for d in with_children] == ["Compta", "Prévisions"]
        assert [d.name for d in alone] == ["Compta"]
        assert [d.name for d in unfiled] == ["Racine"]

    async def test_asking_for_the_root_and_a_package_at_once_is_refused(
        self, diagram_service: DiagramService
    ) -> None:
        with pytest.raises(ValueError, match="both"):
            await diagram_service.list_diagrams(package_id=uuid4(), root_only=True)
```

Ajouter à `backend/tests/integration/test_diagram_store.py` (imports : `from ea.domain.errors import PackageNotFoundError`, `from ea.domain.packages import Package`, `from ea.repositories.package_store import PostgresPackageRepository`) :

```python
class TestPackages:
    async def test_a_diagram_keeps_its_package_and_is_filtered_by_it(
        self, engine_at_head: AsyncEngine, diagrams: PostgresDiagramRepository
    ) -> None:
        packages = PostgresPackageRepository(create_session_factory(engine_at_head))
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        filed = await diagrams.add(Diagram.create(name="Compta", now=FIXED_NOW, package_id=finance.id))
        await diagrams.add(Diagram.create(name="Racine", now=FIXED_NOW))

        assert (await diagrams.get(filed.id)).package_id == finance.id  # type: ignore[union-attr]
        assert [d.name for d in await diagrams.list_all(package_ids={finance.id})] == ["Compta"]
        assert [d.name for d in await diagrams.list_all(root_only=True)] == ["Racine"]
        assert (await packages.counts())[finance.id].diagrams == 1

    async def test_an_unknown_package_is_a_not_found_not_a_taken_name(
        self, diagrams: PostgresDiagramRepository
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await diagrams.add(Diagram.create(name="Vente", now=FIXED_NOW, package_id=uuid4()))

    async def test_deleting_a_package_moves_its_diagrams_up(
        self, engine_at_head: AsyncEngine, diagrams: PostgresDiagramRepository
    ) -> None:
        packages = PostgresPackageRepository(create_session_factory(engine_at_head))
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        diagram = await diagrams.add(Diagram.create(name="Compta", now=FIXED_NOW, package_id=finance.id))

        await packages.delete(finance.id, into=None)

        assert (await diagrams.get(diagram.id)).package_id is None  # type: ignore[union-attr]
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_diagram_service.py -x -k TestDiagramsInPackages`
Expected: FAIL — `ImportError: cannot import name 'InMemoryPackages' from 'tests.conftest'`.

- [ ] **Step 3: Domaine** — `backend/src/ea/domain/diagrams.py` : ajouter `from ea.domain.packages import UNSET, Unset` aux imports, puis remplacer `Diagram` :

```python
@dataclass(frozen=True, slots=True)
class Diagram:
    """A named view. `node_count` is read, never written: the rows are the truth."""

    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    node_count: int = 0
    #: The package that files this view; `None` is "at the root".
    package_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        description: str = "",
        package_id: UUID | None = None,
    ) -> Diagram:
        return cls(
            id=uuid4(),
            name=_clean_name(name),
            description=description.strip(),
            created_at=now,
            updated_at=now,
            package_id=package_id,
        )

    def revise(
        self,
        *,
        now: datetime,
        name: str | None = None,
        description: str | None = None,
        package_id: UUID | None | Unset = UNSET,
    ) -> Diagram:
        """The same diagram under a new name, description or package; an omitted one is kept.

        `package_id=None` is the root; leaving it out (`UNSET`) is "do not move".
        """
        return replace(
            self,
            name=self.name if name is None else _clean_name(name),
            description=self.description if description is None else description.strip(),
            package_id=self.package_id if isinstance(package_id, Unset) else package_id,
            updated_at=now,
        )

    def touched(self, now: datetime) -> Diagram:
        return replace(self, updated_at=now)

    def with_node_count(self, node_count: int) -> Diagram:
        return replace(self, node_count=node_count)
```

`domain/ports.py` : importer `Collection` (`from collections.abc import AsyncIterator, Collection, Sequence`) et remplacer `DiagramRepository.list_all` :

```python
    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        """Every diagram, sorted by name, each with its node count.

        `package_ids` keeps only the diagrams filed in one of those packages
        (the service resolves a subtree to ids); `root_only` only the unfiled.
        """
        ...
```

- [ ] **Step 4: Dépôt** — `backend/src/ea/repositories/diagram_store.py` : imports `from collections.abc import Collection` (dans `TYPE_CHECKING` avec `Sequence`), `from ea.domain.errors import DiagramNotFoundError, DuplicateDiagramError, PackageNotFoundError`, `from ea.repositories.architecture_store import constraint_of` ; remplacer `_to_diagram`, `list_all`, la gestion d'erreur de `add`/`save` :

```python
FOREIGN_KEY_TO_PACKAGE: Final = "fk_diagrams_package_id_packages"


def _to_diagram(row: DiagramRecord, node_count: int) -> Diagram:
    return Diagram(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        node_count=node_count,
        package_id=row.package_id,
    )


def _refused(error: IntegrityError, diagram: Diagram) -> Exception:
    """A missing package is a 404; any other refusal here is the unique name."""
    if constraint_of(error) == FOREIGN_KEY_TO_PACKAGE:
        return PackageNotFoundError(f"no package with id {diagram.package_id}")
    return _taken(diagram)
```

(`Final` à importer de `typing`.) Dans la classe :

```python
    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        statement = _summaries().order_by(DiagramRecord.name, DiagramRecord.id)
        if root_only:
            statement = statement.where(DiagramRecord.package_id.is_(None))
        elif package_ids is not None:
            statement = statement.where(DiagramRecord.package_id.in_(package_ids))
        async with self._sessions() as session:
            return tuple(_to_diagram(row, count) for row, count in await session.execute(statement))
```

`add` : ajouter `package_id=diagram.package_id,` à `DiagramRecord(...)` et remplacer `raise _taken(diagram) from error` par `raise _refused(error, diagram) from error` ; `save` : ajouter `row.package_id = diagram.package_id` et le même remplacement.

- [ ] **Step 5: Service** — `backend/src/ea/services/diagrams.py` : imports `from ea.domain.errors import DiagramNotFoundError, PackageNotFoundError, UnknownLayoutElementError`, `from ea.domain.packages import UNSET, Unset, descendants_of` ; `PackageRepository` sous `TYPE_CHECKING`. Remplacer `__init__`, `list_diagrams`, `create`, `update` :

```python
    def __init__(
        self,
        repository: DiagramRepository,
        architecture: ArchitectureService,
        *,
        packages: PackageRepository | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._packages = packages
        self._now = clock

    async def list_diagrams(
        self,
        *,
        package_id: UUID | None = None,
        include_subpackages: bool = True,
        root_only: bool = False,
    ) -> tuple[Diagram, ...]:
        require_caller()
        if root_only and package_id is not None:
            msg = "a diagram cannot be both at the root and in a package"
            raise ValueError(msg)
        if package_id is None:
            return await self._repository.list_all(root_only=root_only)
        wanted = {package_id}
        if include_subpackages and self._packages is not None:
            everything = {p.id: p for p in await self._packages.list_all()}
            wanted |= descendants_of(package_id, everything)
        return await self._repository.list_all(package_ids=wanted)

    async def _require_package(self, package_id: UUID | None) -> None:
        if package_id is None:
            return
        if self._packages is None or await self._packages.get(package_id) is None:
            msg = f"no package with id {package_id}"
            raise PackageNotFoundError(msg)

    async def create(
        self, *, name: str, description: str = "", package_id: UUID | None = None
    ) -> Diagram:
        require_editor()
        await self._require_package(package_id)
        stored = await self._repository.add(
            Diagram.create(
                name=name, description=description, now=self._now(), package_id=package_id
            )
        )
        self._audit("diagram created: %r", stored, "diagram_created")
        return stored
```

et pour `update` :

```python
    async def update(
        self,
        diagram_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        package_id: UUID | None | Unset = UNSET,
    ) -> Diagram:
        require_editor()
        current = await self.get(diagram_id)
        if not isinstance(package_id, Unset):
            await self._require_package(package_id)
        stored = await self._repository.save(
            current.revise(
                name=name, description=description, package_id=package_id, now=self._now()
            )
        )
        self._audit("diagram updated: %r", stored, "diagram_updated")
        return stored
```

- [ ] **Step 6: Doubles de test** — `backend/tests/conftest.py`

Imports : `from ea.domain.errors import DuplicatePackageError, PackageNotFoundError` (à ajouter à la liste existante), `from ea.domain.packages import Package, PackageCounts`. Remplacer `InMemoryDiagrams.list_all` :

```python
    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        wanted = [
            d
            for d in self.diagrams.values()
            if (not root_only or d.package_id is None)
            and (package_ids is None or d.package_id in package_ids)
        ]
        return tuple(self._counted(d) for d in sorted(wanted, key=lambda d: d.name))
```

(`Collection` à ajouter à l'import `from collections.abc import AsyncIterator, Iterator, Sequence`.) Ajouter après `InMemoryDiagrams` :

```python
class InMemoryPackages:
    """A dictionary pretending to be `packages`: the sibling-name rule of its two indexes,
    and the counts and moves that SQL does across tables, reaching into the element and
    diagram doubles it is built over."""

    def __init__(self, graph: InMemoryRepository, diagrams: InMemoryDiagrams) -> None:
        self.packages: dict[UUID, Package] = {}
        self._graph = graph
        self._diagrams = diagrams

    def _refuse_a_taken_name(self, package: Package) -> None:
        if any(
            p.id != package.id
            and p.parent_id == package.parent_id
            and p.name.lower() == package.name.lower()
            for p in self.packages.values()
        ):
            msg = f"a package is already named {package.name!r} in that place"
            raise DuplicatePackageError(msg)

    def _require_parent(self, package: Package) -> None:
        if package.parent_id is not None and package.parent_id not in self.packages:
            raise PackageNotFoundError(str(package.parent_id))

    async def list_all(self) -> tuple[Package, ...]:
        return tuple(sorted(self.packages.values(), key=lambda p: (p.name.lower(), p.id)))

    async def counts(self) -> dict[UUID, PackageCounts]:
        return {
            p.id: PackageCounts(
                elements=sum(1 for e in self._graph.elements.values() if e.package_id == p.id),
                diagrams=sum(1 for d in self._diagrams.diagrams.values() if d.package_id == p.id),
                children=sum(1 for c in self.packages.values() if c.parent_id == p.id),
            )
            for p in self.packages.values()
        }

    async def get(self, package_id: UUID) -> Package | None:
        return self.packages.get(package_id)

    async def add(self, package: Package) -> Package:
        self._require_parent(package)
        self._refuse_a_taken_name(package)
        self.packages[package.id] = package
        return package

    async def save(self, package: Package) -> Package:
        if package.id not in self.packages:
            raise PackageNotFoundError(str(package.id))
        self._require_parent(package)
        self._refuse_a_taken_name(package)
        self.packages[package.id] = package
        return package

    async def delete(self, package_id: UUID, *, into: UUID | None) -> bool:
        if package_id not in self.packages:
            return False
        children = [p for p in self.packages.values() if p.parent_id == package_id]
        for child in children:  # checked before anything moves: all or none
            self._refuse_a_taken_name(replace(child, parent_id=into))
        for child in children:
            self.packages[child.id] = replace(child, parent_id=into)
        for element_id, element in list(self._graph.elements.items()):
            if element.package_id == package_id:
                self._graph.elements[element_id] = replace(element, package_id=into)
        for diagram_id, diagram in list(self._diagrams.diagrams.items()):
            if diagram.package_id == package_id:
                self._diagrams.diagrams[diagram_id] = replace(diagram, package_id=into)
        del self.packages[package_id]
        return True

    async def move_elements(self, element_ids: Sequence[UUID], package_id: UUID | None) -> None:
        missing = [str(i) for i in element_ids if i not in self._graph.elements]
        if missing:
            raise ElementNotFoundError(f"no element with id {', '.join(missing)}")
        if package_id is not None and package_id not in self.packages:
            raise PackageNotFoundError(str(package_id))
        for element_id in element_ids:
            self._graph.elements[element_id] = replace(
                self._graph.elements[element_id], package_id=package_id
            )
```

Fixtures (remplacer `diagram_service`, ajouter `packages`) :

```python
@pytest.fixture
def packages(repository: InMemoryRepository, diagrams: InMemoryDiagrams) -> InMemoryPackages:
    return InMemoryPackages(repository, diagrams)


@pytest.fixture
def diagram_service(
    diagrams: InMemoryDiagrams, service: ArchitectureService, packages: InMemoryPackages
) -> DiagramService:
    return DiagramService(diagrams, service, packages=packages, clock=lambda: FIXED_NOW)
```

- [ ] **Step 7: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q && uv run mypy src && uv run ruff check src tests`
Expected: PASS (tout le dossier unit : le champ `Diagram.package_id` ne casse aucun test existant). Puis `uv run pytest tests/integration/test_diagram_store.py tests/integration/test_package_store.py -x` sur conteneur jetable : PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/ea/domain backend/src/ea/repositories/diagram_store.py backend/src/ea/services/diagrams.py backend/tests/conftest.py backend/tests/unit/test_diagram_service.py backend/tests/integration/test_diagram_store.py
git commit -m "feat(diagrams): file diagrams in packages" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `PackageService`

**Files:**
- Create: `backend/src/ea/services/packages.py`
- Modify: `backend/tests/conftest.py` (fixture `package_service`), `backend/tests/unit/test_service_guards.py` (`WRITES`, imports)
- Test: `backend/tests/unit/test_package_service.py`

**Interfaces:**
- Consumes: `PackageRepository` (Task 2), `InMemoryPackages` (Task 5), `check_tree`, `descendants_of`, `path_of`, `Package`, `PackageCounts`, `PackageSummary`, `UNSET`, `Unset`, `MAX_PACKAGES`, `MAX_MOVE_BATCH` (Task 1), erreurs de la Task 1, `require_caller`, `require_editor`.
- Produces: `PackageService(repository: PackageRepository, *, clock=...)` avec
  - `async list_packages() -> tuple[PackageSummary, ...]` (lecture, trié par chemin insensible à la casse)
  - `async get(package_id: UUID) -> PackageSummary` (lecture)
  - `async create(*, name: str, description: str = "", parent_id: UUID | None = None) -> PackageSummary` (écriture)
  - `async update(package_id: UUID, *, name: str | None = None, description: str | None = None, parent_id: UUID | None | Unset = UNSET) -> PackageSummary` (écriture)
  - `async delete(package_id: UUID, *, move_contents_to: UUID | None | Unset = UNSET) -> None` (écriture)
  - `async move_elements(element_ids: Sequence[UUID], package_id: UUID | None) -> int` (écriture ; renvoie le nombre d'éléments distincts déplacés)

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/unit/test_package_service.py`

```python
"""The package use cases against in-memory stores: the rules that need the whole tree."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.diagrams import Diagram
from ea.domain.errors import (
    CyclicPackageError,
    DuplicatePackageError,
    ElementNotFoundError,
    NotAuthorisedError,
    PackageNotEmptyError,
    PackageNotFoundError,
    PackageTooDeepError,
    TooManyPackagesError,
)
from ea.domain.packages import MAX_MOVE_BATCH, MAX_PACKAGE_DEPTH
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.packages import PackageService
from tests.conftest import FIXED_NOW, InMemoryDiagrams, a_reader

pytestmark = pytest.mark.asyncio


async def an_element(service: ArchitectureService, name: str = "Billing"):  # type: ignore[no-untyped-def]
    return await service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)


class TestCreating:
    async def test_a_root_package_has_a_path_and_no_content(
        self, package_service: PackageService
    ) -> None:
        created = await package_service.create(name=" Finance ", description="Les comptes")

        assert created.package.name == "Finance"
        assert created.path == "Finance"
        assert (created.counts.elements, created.counts.diagrams, created.counts.children) == (0, 0, 0)

    async def test_a_child_reads_its_whole_path(self, package_service: PackageService) -> None:
        finance = await package_service.create(name="Finance")

        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        assert budget.path == "Finance / Budget"

    async def test_a_parent_that_does_not_exist_is_a_not_found(
        self, package_service: PackageService
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.create(name="Budget", parent_id=uuid4())

    async def test_a_taken_sibling_name_is_refused_ignoring_case(
        self, package_service: PackageService
    ) -> None:
        await package_service.create(name="Finance")

        with pytest.raises(DuplicatePackageError):
            await package_service.create(name="FINANCE")

    async def test_a_name_with_a_slash_is_a_value_error(self, package_service: PackageService) -> None:
        with pytest.raises(ValueError, match="/"):
            await package_service.create(name="a/b")

    async def test_the_tree_stops_at_the_maximum_depth(self, package_service: PackageService) -> None:
        parent = None
        for level in range(MAX_PACKAGE_DEPTH):
            parent = (await package_service.create(name=f"p{level}", parent_id=parent)).package.id

        with pytest.raises(PackageTooDeepError):
            await package_service.create(name="trop", parent_id=parent)

    async def test_the_tree_is_capped(
        self, package_service: PackageService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.services.packages.MAX_PACKAGES", 1)
        await package_service.create(name="Un")

        with pytest.raises(TooManyPackagesError):
            await package_service.create(name="Deux")


class TestUpdating:
    async def test_renaming_keeps_the_parent(self, package_service: PackageService) -> None:
        finance = await package_service.create(name="Finance")
        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        renamed = await package_service.update(budget.package.id, name="Prévisions")

        assert renamed.path == "Finance / Prévisions"

    async def test_an_omitted_parent_is_kept_and_an_explicit_null_is_the_root(
        self, package_service: PackageService
    ) -> None:
        finance = await package_service.create(name="Finance")
        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        kept = await package_service.update(budget.package.id, description="x")
        at_root = await package_service.update(budget.package.id, parent_id=None)

        assert kept.package.parent_id == finance.package.id
        assert at_root.package.parent_id is None
        assert at_root.path == "Budget"

    async def test_a_package_cannot_go_under_its_own_descendant(
        self, package_service: PackageService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        c = await package_service.create(name="C", parent_id=b.package.id)

        with pytest.raises(CyclicPackageError):
            await package_service.update(a.package.id, parent_id=c.package.id)

    async def test_a_package_cannot_be_its_own_parent(self, package_service: PackageService) -> None:
        a = await package_service.create(name="A")

        with pytest.raises(CyclicPackageError):
            await package_service.update(a.package.id, parent_id=a.package.id)

    async def test_moving_a_subtree_too_deep_is_refused(self, package_service: PackageService) -> None:
        deep = None
        for level in range(MAX_PACKAGE_DEPTH - 1):
            deep = (await package_service.create(name=f"d{level}", parent_id=deep)).package.id
        top = await package_service.create(name="top")
        await package_service.create(name="child", parent_id=top.package.id)

        with pytest.raises(PackageTooDeepError):
            await package_service.update(top.package.id, parent_id=deep)

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.update(uuid4(), name="x")


class TestDeleting:
    async def test_an_empty_package_is_deleted(self, package_service: PackageService) -> None:
        a = await package_service.create(name="A")

        await package_service.delete(a.package.id)

        assert await package_service.list_packages() == ()

    async def test_a_package_holding_an_element_is_refused_without_a_target(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        with pytest.raises(PackageNotEmptyError, match="move_contents_to"):
            await package_service.delete(a.package.id)

    async def test_a_package_holding_only_a_diagram_is_refused_too(
        self,
        package_service: PackageService,
        diagrams: InMemoryDiagrams,
    ) -> None:
        a = await package_service.create(name="A")
        diagram = Diagram.create(name="Vente", now=FIXED_NOW, package_id=a.package.id)
        await diagrams.add(diagram)

        with pytest.raises(PackageNotEmptyError):
            await package_service.delete(a.package.id)

    async def test_with_a_target_the_contents_move_and_the_package_goes(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        other = await package_service.create(name="Autre")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        await package_service.delete(a.package.id, move_contents_to=other.package.id)

        assert (await service.get_element(element.id)).package_id == other.package.id
        assert (await package_service.get(b.package.id)).package.parent_id == other.package.id

    async def test_to_the_root_with_none(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        await package_service.delete(a.package.id, move_contents_to=None)

        assert (await service.get_element(element.id)).package_id is None

    async def test_the_target_cannot_be_a_descendant_of_the_deleted_package(
        self, package_service: PackageService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)

        with pytest.raises(CyclicPackageError):
            await package_service.delete(a.package.id, move_contents_to=b.package.id)

        assert await package_service.get(a.package.id)

    async def test_a_name_taken_at_the_destination_moves_nothing(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        await package_service.create(name="Budget", parent_id=a.package.id)
        await package_service.create(name="Budget")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        with pytest.raises(DuplicatePackageError):
            await package_service.delete(a.package.id, move_contents_to=None)

        assert (await service.get_element(element.id)).package_id == a.package.id

    async def test_moving_the_children_up_too_deep_is_refused(
        self, package_service: PackageService
    ) -> None:
        deep = None
        for level in range(MAX_PACKAGE_DEPTH - 1):
            deep = (await package_service.create(name=f"d{level}", parent_id=deep)).package.id
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        await package_service.create(name="C", parent_id=b.package.id)

        with pytest.raises(PackageTooDeepError):
            await package_service.delete(a.package.id, move_contents_to=deep)

    async def test_an_unknown_target_is_a_not_found(self, package_service: PackageService) -> None:
        a = await package_service.create(name="A")

        with pytest.raises(PackageNotFoundError):
            await package_service.delete(a.package.id, move_contents_to=uuid4())

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.delete(uuid4())


class TestMovingElements:
    async def test_elements_go_to_a_package_and_back_to_the_root(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        one, two = await an_element(service, "one"), await an_element(service, "two")

        assert await package_service.move_elements([one.id, two.id, one.id], a.package.id) == 2
        assert (await package_service.get(a.package.id)).counts.elements == 2

        await package_service.move_elements([one.id], None)
        assert (await package_service.get(a.package.id)).counts.elements == 1

    async def test_an_absent_element_moves_nothing(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        kept = await an_element(service)

        with pytest.raises(ElementNotFoundError):
            await package_service.move_elements([kept.id, uuid4()], a.package.id)

        assert (await service.get_element(kept.id)).package_id is None

    async def test_an_absent_package_is_a_not_found(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(PackageNotFoundError):
            await package_service.move_elements([element.id], uuid4())

    async def test_more_than_the_batch_is_refused(self, package_service: PackageService) -> None:
        with pytest.raises(ValueError, match=str(MAX_MOVE_BATCH)):
            await package_service.move_elements([uuid4() for _ in range(MAX_MOVE_BATCH + 1)], None)

    async def test_an_empty_batch_is_a_no_op(self, package_service: PackageService) -> None:
        assert await package_service.move_elements([], None) == 0


class TestReading:
    async def test_the_listing_is_sorted_by_path_with_direct_counts(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        finance = await package_service.create(name="finance")
        await package_service.create(name="Budget", parent_id=finance.package.id)
        await package_service.create(name="Achats")
        element = await an_element(service)
        await package_service.move_elements([element.id], finance.package.id)

        listed = await package_service.list_packages()

        assert [(s.path, s.counts.elements, s.counts.children) for s in listed] == [
            ("Achats", 0, 0),
            ("finance", 1, 1),
            ("finance / Budget", 0, 0),
        ]

    async def test_a_reader_reads_but_cannot_write(self, package_service: PackageService) -> None:
        created = await package_service.create(name="A")

        with acting_as(a_reader()):
            assert [s.path for s in await package_service.list_packages()] == ["A"]
            assert (await package_service.get(created.package.id)).path == "A"
            with pytest.raises(NotAuthorisedError):
                await package_service.create(name="B")
            with pytest.raises(NotAuthorisedError):
                await package_service.update(created.package.id, name="C")
            with pytest.raises(NotAuthorisedError):
                await package_service.delete(created.package.id)
            with pytest.raises(NotAuthorisedError):
                await package_service.move_elements([], None)

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.get(uuid4())


class TestTheAuditTrail:
    async def test_each_write_leaves_one_line_naming_what_changed(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="ea.services"):
            a = await package_service.create(name="A")
            await package_service.update(a.package.id, name="A2")
            element = await an_element(service)
            await package_service.move_elements([element.id], a.package.id)
            await package_service.delete(a.package.id, move_contents_to=None)

        actions = [getattr(r, "action", None) for r in caplog.records if r.name.startswith("ea.services")]
        assert [x for x in actions if x and x.startswith(("package", "elements"))] == [
            "package_created",
            "package_updated",
            "elements_moved",
            "package_deleted",
        ]
        moved = next(r for r in caplog.records if getattr(r, "action", "") == "elements_moved")
        assert moved.count == 1  # type: ignore[attr-defined]

```

Note : retirer la dernière fonction `test_unset_is_the_default_of_the_optional_targets` et les imports `UNSET`/`Package` s'ils ne servent pas (ruff F401).

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_package_service.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.services.packages'` (la fixture `package_service` n'existe pas non plus).

- [ ] **Step 3: Fixture** — `backend/tests/conftest.py`, après `packages` :

```python
@pytest.fixture
def package_service(packages: InMemoryPackages) -> PackageService:
    return PackageService(packages, clock=lambda: FIXED_NOW)
```

avec `from ea.services.packages import PackageService` en tête de fichier.

- [ ] **Step 4: Implémenter** — `backend/src/ea/services/packages.py`

```python
"""Use cases over the classification tree.

The rules that need the whole tree live here, on the flat list the service
loads anyway to compute paths: no cycle, no tree deeper than
`MAX_PACKAGE_DEPTH`, no more than `MAX_PACKAGES`. The rules one row can check
alone (a taken sibling name, a package that still holds something) are the
database's, and arrive here as the repository's refusals.

A package is never a fact of the graph: nothing here reads or writes a
relationship, and moving an element changes nothing about it but its folder.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import (
    CyclicPackageError,
    PackageNotEmptyError,
    PackageNotFoundError,
    TooManyPackagesError,
)
from ea.domain.packages import (
    MAX_MOVE_BATCH,
    MAX_PACKAGES,
    UNSET,
    Package,
    PackageCounts,
    PackageSummary,
    Unset,
    check_tree,
    descendants_of,
    path_of,
)
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import PackageRepository

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _not_found(package_id: UUID) -> PackageNotFoundError:
    return PackageNotFoundError(f"no package with id {package_id}")


class PackageService:
    """The single entry point `api/` and `mcp/` use to read and change the tree."""

    def __init__(self, repository: PackageRepository, *, clock: Clock = _utc_now) -> None:
        self._repository = repository
        self._now = clock

    async def _tree(self) -> dict[UUID, Package]:
        return {package.id: package for package in await self._repository.list_all()}

    async def _summaries(self) -> dict[UUID, PackageSummary]:
        packages = await self._tree()
        counts = await self._repository.counts()
        return {
            package.id: PackageSummary(
                package=package,
                path=path_of(package, packages),
                counts=counts.get(package.id, PackageCounts()),
            )
            for package in packages.values()
        }

    async def list_packages(self) -> tuple[PackageSummary, ...]:
        require_caller()
        summaries = await self._summaries()
        return tuple(sorted(summaries.values(), key=lambda s: s.path.lower()))

    async def get(self, package_id: UUID) -> PackageSummary:
        require_caller()
        summary = (await self._summaries()).get(package_id)
        if summary is None:
            raise _not_found(package_id)
        return summary

    async def create(
        self, *, name: str, description: str = "", parent_id: UUID | None = None
    ) -> PackageSummary:
        require_editor()
        packages = await self._tree()
        if len(packages) >= MAX_PACKAGES:
            msg = f"the tree holds at most {MAX_PACKAGES} packages"
            raise TooManyPackagesError(msg)
        if parent_id is not None and parent_id not in packages:
            raise _not_found(parent_id)
        package = Package.create(
            name=name, description=description, parent_id=parent_id, now=self._now()
        )
        check_tree({**packages, package.id: package})
        stored = await self._repository.add(package)
        self._audit("package created: %r", stored, "package_created")
        return await self.get(stored.id)

    async def update(
        self,
        package_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_id: UUID | None | Unset = UNSET,
    ) -> PackageSummary:
        """Rename, describe or move. `parent_id=None` is the root; leaving it out keeps it."""
        require_editor()
        packages = await self._tree()
        current = packages.get(package_id)
        if current is None:
            raise _not_found(package_id)
        now = self._now()
        revised = current.revise(name=name, description=description, now=now)
        if not isinstance(parent_id, Unset):
            if parent_id is not None and parent_id not in packages:
                raise _not_found(parent_id)
            revised = revised.moved_to(parent_id, now=now)
        check_tree({**packages, package_id: revised})
        stored = await self._repository.save(revised)
        self._audit("package updated: %r", stored, "package_updated")
        return await self.get(stored.id)

    async def delete(
        self, package_id: UUID, *, move_contents_to: UUID | None | Unset = UNSET
    ) -> None:
        """Delete a package, never what it holds.

        Without `move_contents_to` a package that still holds anything is
        refused. With it — a package id, or `None` for the root — the direct
        sub-packages, elements and diagrams move there in the same transaction.
        """
        require_editor()
        packages = await self._tree()
        current = packages.get(package_id)
        if current is None:
            raise _not_found(package_id)
        into: UUID | None = None
        if isinstance(move_contents_to, Unset):
            held = (await self._repository.counts()).get(package_id, PackageCounts())
            if held.elements or held.diagrams or held.children:
                msg = (
                    f"{path_of(current, packages)!r} still holds {held.elements} element(s), "
                    f"{held.diagrams} diagram(s) and {held.children} sub-package(s): "
                    "pass move_contents_to to move them elsewhere (or to the root)"
                )
                raise PackageNotEmptyError(msg)
        else:
            into = move_contents_to
            if into is not None and into not in packages:
                raise _not_found(into)
            if into == package_id or (
                into is not None and into in descendants_of(package_id, packages)
            ):
                msg = "the contents cannot move into the package being deleted or below it"
                raise CyclicPackageError(msg)
            remaining = {key: value for key, value in packages.items() if key != package_id}
            now = self._now()
            for child in list(remaining.values()):
                if child.parent_id == package_id:
                    remaining[child.id] = child.moved_to(into, now=now)
            check_tree(remaining)
        if not await self._repository.delete(package_id, into=into):
            raise _not_found(package_id)
        logger.info(
            "package deleted: %r",
            current.name,
            extra={
                "action": "package_deleted",
                "package_id": str(package_id),
                "moved_to": str(into) if into is not None else "root",
            },
        )

    async def move_elements(self, element_ids: Sequence[UUID], package_id: UUID | None) -> int:
        """File up to `MAX_MOVE_BATCH` elements in a package (`None`: the root), all or none."""
        require_editor()
        wanted = tuple(dict.fromkeys(element_ids))
        if not wanted:
            return 0
        if len(wanted) > MAX_MOVE_BATCH:
            msg = f"at most {MAX_MOVE_BATCH} elements can be moved in one call"
            raise ValueError(msg)
        if package_id is not None and await self._repository.get(package_id) is None:
            raise _not_found(package_id)
        await self._repository.move_elements(wanted, package_id)
        logger.info(
            "%d element(s) filed",
            len(wanted),
            extra={
                "action": "elements_moved",
                "count": len(wanted),
                "package_id": str(package_id) if package_id is not None else "root",
            },
        )
        return len(wanted)

    @staticmethod
    def _audit(message: str, package: Package, action: str) -> None:
        logger.info(message, package.name, extra={"action": action, "package_id": str(package.id)})
```

- [ ] **Step 5: Garde AST** — `backend/tests/unit/test_service_guards.py` : importer `from ea.services.packages import PackageService` et ajouter à `WRITES` :

```python
    PackageService: {"create", "update", "delete", "move_elements"},
```

- [ ] **Step 6: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_package_service.py tests/unit/test_service_guards.py -x -q && uv run mypy src`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/services/packages.py backend/tests/conftest.py backend/tests/unit/test_package_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(services): add the package use cases" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: L'API des paquets (`/packages`, `/elements/move`) et le câblage

**Files:**
- Create: `backend/src/ea/api/packages.py`
- Modify: `backend/src/ea/api/schemas.py` (imports ; nouvelle section « Packages » en fin de fichier)
- Modify: `backend/src/ea/api/errors.py` (imports ≈ l.30-55 ; table `_STATUS` ≈ l.60-115)
- Modify: `backend/src/ea/api/dependencies.py` (après `diagram_service_of`)
- Modify: `backend/src/ea/main.py` (imports ; `_lifespan` ≈ l.116-280 ; `create_app` ≈ l.440-540)
- Test: `backend/tests/e2e/test_package_api.py` ; `backend/tests/unit/test_error_statuses.py` (devient vert)

**Interfaces:**
- Consumes: `PackageService` (Task 6) ; `InMemoryPackages`, fixtures `packages`, `service`, `diagrams` (Task 5-6) ; `StaticVerifier`, `a_reader`, `an_editor` (`tests/conftest.py`).
- Produces:
  - `api.schemas` : `PackageName`, `ROOT_PACKAGE = "root"`, `PackageTarget`, `package_target(value: str) -> UUID | None`, `PackageCreate{name, description="", parent_id=None}`, `PackageUpdate{name=None, description=None, parent_id=None}`, `PackageRead{id, parent_id, name, description, path, element_count, diagram_count, child_count, created_at, updated_at}` (+ `PackageRead.of(summary: PackageSummary)`), `MoveElements{element_ids: list[UUID] (1..100), package_id: UUID | None}`.
  - Routes : `GET/POST /packages`, `GET/PATCH/DELETE /packages/{package_id}` (`DELETE ?move_contents_to=<uuid|root>`), `POST /elements/move`.
  - `api.dependencies` : `package_service_of(app) -> PackageService`, `get_package_service`, `Packages` (dépendance annotée).
  - `main.create_app(..., packages: PackageRepository | None = None)` et `_lifespan(..., packages=None)` ; `app.state.package_service`.

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/e2e/test_package_api.py`

```python
"""The package endpoints, end to end against doubled stores — every status of the contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.api.schemas import ROOT_PACKAGE, package_target
from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryPackages,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams, packages: InMemoryPackages
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=packages,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def a_package(
    client: httpx.AsyncClient, name: str = "Finance", parent: str | None = None
) -> dict[str, Any]:
    response = await client.post("/packages", json={"name": name, "parent_id": parent})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def an_element(client: httpx.AsyncClient, name: str = "Billing") -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": name}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestCreatingAndReading:
    async def test_a_created_package_is_a_201_with_its_path_and_counts(
        self, client: httpx.AsyncClient
    ) -> None:
        finance = await a_package(client)
        budget = await a_package(client, "Budget", finance["id"])

        assert set(budget) == {
            "id", "parent_id", "name", "description", "path",
            "element_count", "diagram_count", "child_count", "created_at", "updated_at",
        }  # fmt: skip
        assert (budget["path"], budget["parent_id"]) == ("Finance / Budget", finance["id"])

    async def test_the_list_is_flat_and_counts_are_direct(self, client: httpx.AsyncClient) -> None:
        finance = await a_package(client)
        await a_package(client, "Budget", finance["id"])
        element = await an_element(client)
        await client.post(
            "/elements/move", json={"element_ids": [element], "package_id": finance["id"]}
        )

        listed = (await client.get("/packages")).json()

        assert [(p["path"], p["element_count"], p["child_count"]) for p in listed] == [
            ("Finance", 1, 1),
            ("Finance / Budget", 0, 0),
        ]

    async def test_one_package_is_read_by_id_or_is_a_404(self, client: httpx.AsyncClient) -> None:
        finance = await a_package(client)

        assert (await client.get(f"/packages/{finance['id']}")).json()["name"] == "Finance"
        missing = await client.get(f"/packages/{uuid4()}")
        assert (missing.status_code, missing.json()["error"]) == (404, "not_found")

    async def test_an_unknown_parent_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/packages", json={"name": "X", "parent_id": str(uuid4())})

        assert (response.status_code, response.json()["error"]) == (404, "not_found")

    async def test_a_taken_sibling_name_is_a_409_whatever_its_case(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_package(client)

        response = await client.post("/packages", json={"name": "FINANCE"})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    @pytest.mark.parametrize("name", ["", "a/b"])
    async def test_a_blank_or_slashed_name_is_a_422(
        self, client: httpx.AsyncClient, name: str
    ) -> None:
        assert (await client.post("/packages", json={"name": name})).status_code == 422

    async def test_a_name_of_only_blanks_is_a_422_from_the_domain(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/packages", json={"name": "   "})

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_too_deep_is_a_422(self, client: httpx.AsyncClient) -> None:
        parent = None
        for level in range(12):
            parent = (await a_package(client, f"p{level}", parent))["id"]

        response = await client.post("/packages", json={"name": "trop", "parent_id": parent})

        assert (response.status_code, response.json()["error"]) == (422, "package_too_deep")


class TestUpdating:
    async def test_an_absent_parent_is_kept_and_an_explicit_null_is_the_root(
        self, client: httpx.AsyncClient
    ) -> None:
        finance = await a_package(client)
        budget = await a_package(client, "Budget", finance["id"])

        kept = await client.patch(f"/packages/{budget['id']}", json={"description": "x"})
        at_root = await client.patch(f"/packages/{budget['id']}", json={"parent_id": None})

        assert kept.json()["parent_id"] == finance["id"]
        assert at_root.json()["parent_id"] is None

    async def test_a_move_below_itself_is_a_409_cyclic_package(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client, "A")
        b = await a_package(client, "B", a["id"])

        response = await client.patch(f"/packages/{a['id']}", json={"parent_id": b["id"]})

        assert (response.status_code, response.json()["error"]) == (409, "cyclic_package")

    async def test_a_rename_onto_a_sibling_is_a_409(self, client: httpx.AsyncClient) -> None:
        await a_package(client, "A")
        b = await a_package(client, "B")

        response = await client.patch(f"/packages/{b['id']}", json={"name": "a"})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_an_unknown_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.patch(f"/packages/{uuid4()}", json={"name": "x"})).status_code == 404

    async def test_an_unknown_field_is_a_422(self, client: httpx.AsyncClient) -> None:
        a = await a_package(client)

        assert (await client.patch(f"/packages/{a['id']}", json={"colour": "red"})).status_code == 422


class TestDeleting:
    async def test_an_empty_package_is_a_204(self, client: httpx.AsyncClient) -> None:
        a = await a_package(client)

        assert (await client.delete(f"/packages/{a['id']}")).status_code == 204
        assert (await client.get(f"/packages/{a['id']}")).status_code == 404

    async def test_a_package_that_holds_an_element_is_a_409_package_not_empty(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        response = await client.delete(f"/packages/{a['id']}")

        assert (response.status_code, response.json()["error"]) == (409, "package_not_empty")
        assert (await client.get(f"/elements/{element}")).status_code == 200

    async def test_move_contents_to_root_moves_then_deletes(self, client: httpx.AsyncClient) -> None:
        a = await a_package(client)
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": "root"})

        assert response.status_code == 204
        assert (await client.get(f"/elements/{element}")).json()["package_id"] is None

    async def test_move_contents_to_another_package(self, client: httpx.AsyncClient) -> None:
        a, b = await a_package(client, "A"), await a_package(client, "B")
        element = await an_element(client)
        await client.post("/elements/move", json={"element_ids": [element], "package_id": a["id"]})

        await client.delete(f"/packages/{a['id']}", params={"move_contents_to": b["id"]})

        assert (await client.get(f"/elements/{element}")).json()["package_id"] == b["id"]

    async def test_a_target_below_the_deleted_package_is_a_409_cyclic_package(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client, "A")
        b = await a_package(client, "B", a["id"])

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": b["id"]})

        assert (response.status_code, response.json()["error"]) == (409, "cyclic_package")

    async def test_a_target_that_is_neither_an_id_nor_root_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)

        response = await client.delete(f"/packages/{a['id']}", params={"move_contents_to": "ici"})

        assert response.status_code == 422

    async def test_an_unknown_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.delete(f"/packages/{uuid4()}")).status_code == 404


class TestMovingElements:
    async def test_a_batch_is_filed_and_can_go_back_to_the_root(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        one, two = await an_element(client, "one"), await an_element(client, "two")

        filed = await client.post(
            "/elements/move", json={"element_ids": [one, two], "package_id": a["id"]}
        )
        assert filed.status_code == 204
        assert (await client.get(f"/elements/{one}")).json()["package_id"] == a["id"]

        await client.post("/elements/move", json={"element_ids": [one], "package_id": None})
        assert (await client.get(f"/elements/{one}")).json()["package_id"] is None

    async def test_an_absent_element_is_a_404_and_nothing_moves(
        self, client: httpx.AsyncClient
    ) -> None:
        a = await a_package(client)
        kept = await an_element(client)

        response = await client.post(
            "/elements/move", json={"element_ids": [kept, str(uuid4())], "package_id": a["id"]}
        )

        assert (response.status_code, response.json()["error"]) == (404, "not_found")
        assert (await client.get(f"/elements/{kept}")).json()["package_id"] is None

    async def test_an_absent_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        element = await an_element(client)

        response = await client.post(
            "/elements/move", json={"element_ids": [element], "package_id": str(uuid4())}
        )

        assert response.status_code == 404

    async def test_over_the_batch_limit_or_empty_is_a_422(self, client: httpx.AsyncClient) -> None:
        too_many = [str(uuid4()) for _ in range(101)]

        for ids in (too_many, []):
            response = await client.post(
                "/elements/move", json={"element_ids": ids, "package_id": None}
            )
            assert response.status_code == 422


class TestWhoMayDoWhat:
    async def test_a_reader_reads_the_tree_but_cannot_change_it(
        self, service: ArchitectureService, packages: InMemoryPackages
    ) -> None:
        verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            packages=packages,  # type: ignore[arg-type]
            verifier=verifier,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.post(
                "/packages", json={"name": "A"}, headers={"Authorization": "Bearer ed"}
            )
            reader = {"Authorization": "Bearer rd"}

            assert (await client.get("/packages", headers=reader)).status_code == 200
            for request in (
                client.post("/packages", json={"name": "B"}, headers=reader),
                client.patch(f"/packages/{created.json()['id']}", json={"name": "C"}, headers=reader),
                client.delete(f"/packages/{created.json()['id']}", headers=reader),
                client.post("/elements/move", json={"element_ids": [str(uuid4())], "package_id": None}, headers=reader),
            ):
                response = await request
                assert (response.status_code, response.json()["error"]) == (403, "forbidden")


class TestThePackageTarget:
    def test_root_is_none_and_an_id_is_an_id(self) -> None:
        wanted = uuid4()

        assert package_target(ROOT_PACKAGE) is None
        assert package_target(str(wanted)) == wanted
        assert isinstance(package_target(str(wanted)), UUID)

    def test_anything_else_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="root"):
            package_target("ici")
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/e2e/test_package_api.py -x`
Expected: FAIL — `ImportError: cannot import name 'ROOT_PACKAGE' from 'ea.api.schemas'`.

- [ ] **Step 3: Schémas** — `backend/src/ea/api/schemas.py`

Imports à ajouter : `from ea.domain.packages import MAX_MOVE_BATCH, MAX_PACKAGE_NAME_LENGTH, PackageSummary`. Section à ajouter en fin de fichier :

```python
# --- Packages: the classification tree ---------------------------------------
# A package files elements and diagrams; it is not an ArchiMate element and
# has no relationship. The bounds come from `domain/packages.py`, which the
# services need too; they are re-declared as Pydantic types here, once, for
# both adapters.

PackageName = Annotated[
    str, Field(min_length=1, max_length=MAX_PACKAGE_NAME_LENGTH, pattern=r"^[^/]+$")
]
#: What an agent or a query string says where a body would say `null`.
ROOT_PACKAGE: Final = "root"
PackageTarget = Annotated[
    str,
    Field(
        min_length=1,
        max_length=36,
        description="The id of a package, or `root` for the top of the tree.",
    ),
]


def package_target(value: str) -> UUID | None:
    """`root` is the top of the tree (`None`); anything else must be a package id.

    One parser for both adapters: `DELETE /packages/{id}?move_contents_to=` and
    the MCP tools cannot express "the root" as `null`, so they say `root`.
    """
    if value == ROOT_PACKAGE:
        return None
    try:
        return UUID(value)
    except ValueError:
        msg = f"expected a package id or {ROOT_PACKAGE!r}, got {value!r}"
        raise ValueError(msg) from None


class PackageCreate(_Input):
    name: PackageName
    description: Description = ""
    parent_id: UUID | None = None


class PackageUpdate(_Input):
    """A partial update.

    `parent_id` is three-valued: absent keeps the parent, `null` puts the
    package at the root, an id moves it there — which is why the route reads
    `model_fields_set` rather than the value.
    """

    name: PackageName | None = None
    description: Description | None = None
    parent_id: UUID | None = None


class PackageRead(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    description: str
    path: str = Field(description="Names from the root down, e.g. `Finance / Budget`.")
    element_count: int = Field(description="Elements filed directly in this package.")
    diagram_count: int = Field(description="Diagrams filed directly in this package.")
    child_count: int = Field(description="Packages directly below this one.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: PackageSummary) -> PackageRead:
        package = summary.package
        return cls(
            id=package.id,
            parent_id=package.parent_id,
            name=package.name,
            description=package.description,
            path=summary.path,
            element_count=summary.counts.elements,
            diagram_count=summary.counts.diagrams,
            child_count=summary.counts.children,
            created_at=package.created_at,
            updated_at=package.updated_at,
        )


class MoveElements(_Input):
    """File up to `MAX_MOVE_BATCH` elements in a package, or at the root."""

    element_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_MOVE_BATCH)]
    package_id: UUID | None = Field(description="The package, or `null` for the root.")
```

(`MoveElements.package_id` sans défaut : requis mais nullable.)

- [ ] **Step 4: Erreurs** — `backend/src/ea/api/errors.py` : importer `CyclicPackageError, DuplicatePackageError, PackageNotEmptyError, PackageNotFoundError, PackageTooDeepError, TooManyPackagesError, UnknownPackageError` depuis `ea.domain.errors` (ordre alphabétique), puis dans `_STATUS`, après le bloc des diagrammes :

```python
    # --- Packages (the classification tree) ---
    # `package_not_empty` and `cyclic_package` are the two a caller acts on:
    # the first says to pass `move_contents_to`, the second to pick a target
    # outside the subtree.
    PackageNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicatePackageError: (status.HTTP_409_CONFLICT, "duplicate"),
    CyclicPackageError: (status.HTTP_409_CONFLICT, "cyclic_package"),
    PackageNotEmptyError: (status.HTTP_409_CONFLICT, "package_not_empty"),
    PackageTooDeepError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "package_too_deep"),
    TooManyPackagesError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_packages"),
    UnknownPackageError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_package"),
```

- [ ] **Step 5: Routes** — `backend/src/ea/api/packages.py`

```python
"""The endpoints of the classification tree, and the move of elements into it.

A package files elements and diagrams; it carries no relationship and never
appears in a traversal. `POST /elements/move` lives here and not in
`architecture.py` because it is the tree's operation: it changes where an
element is filed, never what the element says.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Packages
from ea.api.schemas import (
    ErrorResponse,
    MoveElements,
    PackageCreate,
    PackageRead,
    PackageTarget,
    PackageUpdate,
    package_target,
)
from ea.domain.packages import UNSET, Unset

router = APIRouter(tags=["packages"])

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/packages", response_model=list[PackageRead])
async def list_packages(packages: Packages) -> list[PackageRead]:
    """The whole tree, flat: each package names its parent, so the client builds the tree."""
    return [PackageRead.of(summary) for summary in await packages.list_packages()]


@router.post(
    "/packages", response_model=PackageRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def create_package(body: PackageCreate, packages: Packages) -> PackageRead:
    created = await packages.create(
        name=body.name, description=body.description, parent_id=body.parent_id
    )
    return PackageRead.of(created)


@router.get("/packages/{package_id}", response_model=PackageRead, responses=NOT_FOUND)
async def read_package(package_id: UUID, packages: Packages) -> PackageRead:
    return PackageRead.of(await packages.get(package_id))


@router.patch("/packages/{package_id}", response_model=PackageRead, responses=REJECTED)
async def update_package(package_id: UUID, body: PackageUpdate, packages: Packages) -> PackageRead:
    """Rename, describe or move. `parent_id: null` puts the package at the root; omit it to keep."""
    parent = body.parent_id if "parent_id" in body.model_fields_set else UNSET
    updated = await packages.update(
        package_id, name=body.name, description=body.description, parent_id=parent
    )
    return PackageRead.of(updated)


@router.delete(
    "/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED
)
async def delete_package(
    package_id: UUID,
    packages: Packages,
    move_contents_to: Annotated[PackageTarget | None, Query()] = None,
) -> Response:
    """Delete a package — never what it holds.

    A package that still holds anything is a 409 `package_not_empty`; pass
    `move_contents_to` (a package id, or `root`) to move its direct contents
    there in the same transaction.
    """
    target: UUID | None | Unset = UNSET
    if move_contents_to is not None:
        target = package_target(move_contents_to)
    await packages.delete(package_id, move_contents_to=target)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/elements/move", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED, tags=["packages"]
)
async def move_elements(body: MoveElements, packages: Packages) -> Response:
    """File up to 100 elements in a package, or at the root; all or none."""
    await packages.move_elements(body.element_ids, body.package_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 6: Dépendance** — `backend/src/ea/api/dependencies.py` : importer `from ea.services.packages import PackageService` et ajouter après `Diagrams = ...` :

```python
def package_service_of(app: FastAPI) -> PackageService:
    """The package use cases the lifespan attached, or a clear failure.

    Absent means the relational store is shut, and the tree lives in it: a
    wiring fault and a 500, never an empty tree a client could not tell from
    a catalogue with nothing filed.
    """
    service: PackageService | None = getattr(app.state, "package_service", None)
    if service is None:
        msg = "no package service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_package_service(request: Request) -> PackageService:
    return package_service_of(request.app)


Packages = Annotated[PackageService, Depends(get_package_service)]
```

- [ ] **Step 7: Câblage** — `backend/src/ea/main.py`

Imports : `from ea.api.packages import router as packages_router` ; `PackageRepository` dans l'import de `ea.domain.ports` ; `from ea.repositories.package_store import PostgresPackageRepository` ; `from ea.services.packages import PackageService`.

`_lifespan(...)` : ajouter le paramètre `packages: PackageRepository | None = None` ; dans le corps, à côté de `diagram_store = diagrams` : `package_store = packages` ; dans `if settings.postgres_enabled:` après la création de `diagram_store` :

```python
                if package_store is None:
                    package_store = PostgresPackageRepository(app.state.db_sessions)
```

et remplacer la construction du service de diagrammes et ajouter celle des paquets :

```python
            if package_store is not None:
                app.state.package_service = PackageService(package_store)
            if diagram_store is not None:
                app.state.diagram_service = DiagramService(
                    diagram_store, architecture_service_of(app), packages=package_store
                )
```

`create_app` : ajouter le paramètre `packages: PackageRepository | None = None` (+ phrase dans la docstring : « `packages` does the same for the classification tree »), le passer à `_lifespan(..., packages=packages)`, et dans le bloc `if architecture_service is not None:` :

```python
        if packages is not None:
            app.state.package_service = PackageService(packages)
        if diagrams is not None:
            app.state.diagram_service = DiagramService(
                diagrams, architecture_service, packages=packages
            )
```

(remplace l'ancienne ligne `DiagramService(diagrams, architecture_service)`). Enfin, avec les autres routeurs :

```python
    app.include_router(packages_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
```

- [ ] **Step 8: Relancer**

Run: `cd backend && uv run pytest tests/e2e/test_package_api.py tests/unit/test_error_statuses.py -x -q`
Expected: PASS (les sept lignes de `_STATUS` ont rendu `test_error_statuses.py` vert). Puis `uv run pytest tests/unit tests/e2e -q` : tout reste vert (le test « chaque route veut un jeton » de `test_auth_api.py` couvre les nouvelles routes sans modification).

- [ ] **Step 9: Commit**

```bash
git add backend/src/ea/api backend/src/ea/main.py backend/tests/e2e/test_package_api.py backend/tests/unit/test_error_statuses.py
git commit -m "feat(api): serve the package tree and the move of elements" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `package_id` sur les éléments et diagrammes, filtres `GET /elements` et `GET /diagrams`

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (`ElementRead` + `of` ≈ l.140-166 ; `DiagramCreate`, `DiagramUpdate`, `DiagramSummaryRead`, `DiagramRead` ≈ l.709-796)
- Modify: `backend/src/ea/api/architecture.py` (`list_elements` ≈ l.68-95)
- Modify: `backend/src/ea/api/diagrams.py` (`list_diagrams`, `create_diagram`, `update_diagram`)
- Test: `backend/tests/e2e/test_architecture_api.py` (ajout), `backend/tests/e2e/test_diagram_api.py` (ajout + correction de l.69)

**Interfaces:**
- Consumes: `Element.package_id` (Task 2), `Diagram.package_id`, `DiagramService.list_diagrams/create/update` (Task 5), `UNSET` (Task 1).
- Produces: `ElementRead.package_id: UUID | None` ; `GET /elements?package=&include_subpackages=true&root_only=false` ; `DiagramCreate.package_id`, `DiagramUpdate.package_id` (absent ≠ null), `DiagramSummaryRead.package_id`, `DiagramRead.package_id` ; `GET /diagrams?package=&include_subpackages=&root_only=`.

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `backend/tests/e2e/test_architecture_api.py`, ajouter (la fixture `client` y existe) :

```python
@pytest.mark.asyncio
class TestPackagesOnElements:
    async def test_an_element_reports_its_package_and_is_at_the_root_when_new(
        self, client: httpx.AsyncClient
    ) -> None:
        created = await create(client, "node", "db-01")

        assert created["package_id"] is None
        assert (await client.get(f"/elements/{created['id']}")).json()["package_id"] is None

    async def test_the_listing_keeps_working_without_any_package_parameter(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "node", "db-01")

        page = (await client.get("/elements")).json()

        assert page["total"] == 1
        assert page["items"][0]["package_id"] is None

    async def test_the_package_parameters_are_accepted(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/elements",
            params={"package": str(uuid4()), "include_subpackages": "false"},
        )

        assert response.status_code == 200

    async def test_the_root_and_a_package_at_once_is_a_422_not_an_empty_page(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/elements", params={"package": str(uuid4()), "root_only": "true"}
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")
```

Dans `backend/tests/e2e/test_diagram_api.py` : remplacer l'assertion de la l.69 par `{"id", "name", "description", "node_count", "package_id", "created_at", "updated_at"}`, étendre la fixture `client` avec `packages: InMemoryPackages` (`packages=packages,  # type: ignore[arg-type]`) et ajouter :

```python
class TestPackages:
    async def test_a_diagram_is_created_in_a_package_and_listed_by_it(
        self, client: httpx.AsyncClient
    ) -> None:
        package = (await client.post("/packages", json={"name": "Finance"})).json()
        filed = await client.post("/diagrams", json={"name": "Compta", "package_id": package["id"]})
        await a_diagram(client, "Racine")

        assert filed.json()["package_id"] == package["id"]
        by_package = (await client.get("/diagrams", params={"package": package["id"]})).json()
        unfiled = (await client.get("/diagrams", params={"root_only": "true"})).json()
        assert [d["name"] for d in by_package] == ["Compta"]
        assert [d["name"] for d in unfiled] == ["Racine"]

    async def test_an_unknown_package_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/diagrams", json={"name": "Compta", "package_id": str(uuid4())}
        )

        assert (response.status_code, response.json()["error"]) == (404, "not_found")

    async def test_patch_distinguishes_an_absent_package_from_null(
        self, client: httpx.AsyncClient
    ) -> None:
        package = (await client.post("/packages", json={"name": "Finance"})).json()
        diagram = (
            await client.post("/diagrams", json={"name": "Compta", "package_id": package["id"]})
        ).json()

        renamed = await client.patch(f"/diagrams/{diagram['id']}", json={"name": "Compta 2"})
        at_root = await client.patch(f"/diagrams/{diagram['id']}", json={"package_id": None})

        assert renamed.json()["package_id"] == package["id"]
        assert at_root.json()["package_id"] is None

    async def test_an_opened_diagram_names_its_package(self, client: httpx.AsyncClient) -> None:
        package = (await client.post("/packages", json={"name": "Finance"})).json()
        diagram = (
            await client.post("/diagrams", json={"name": "Compta", "package_id": package["id"]})
        ).json()

        assert (await client.get(f"/diagrams/{diagram['id']}")).json()["package_id"] == package["id"]

    async def test_the_root_and_a_package_at_once_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/diagrams", params={"package": str(uuid4()), "root_only": "true"}
        )

        assert response.status_code == 422
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/e2e/test_architecture_api.py tests/e2e/test_diagram_api.py -x -q`
Expected: FAIL — `KeyError: 'package_id'`.

- [ ] **Step 3: Schémas** — `api/schemas.py`

`ElementRead` : ajouter le champ `package_id: UUID | None` (après `properties`) et, dans `of`, `package_id=element.package_id,`.

Diagrammes :

```python
class DiagramCreate(_Input):
    name: Name
    description: Description = ""
    package_id: UUID | None = None


class DiagramUpdate(_Input):
    """A partial update. An omitted field keeps its stored value.

    `package_id` is three-valued like a package's `parent_id`: absent keeps the
    package, `null` puts the diagram at the root.
    """

    name: Name | None = None
    description: Description | None = None
    package_id: UUID | None = None
```

et ajouter `package_id: UUID | None` à `DiagramSummaryRead` (après `node_count`; `of` : `package_id=diagram.package_id`) et à `DiagramRead` (après `updated_at`; `of` : `package_id=diagram.package_id`).

- [ ] **Step 4: Routes**

`api/architecture.py` : dans `list_elements`, ajouter les paramètres et la construction :

```python
    search: Annotated[Search | None, Query()] = None,
    package: Annotated[UUID | None, Query()] = None,
    include_subpackages: Annotated[bool, Query()] = True,
    root_only: Annotated[bool, Query()] = False,
    limit: LimitQuery = 50,
```

```python
    if root_only and package is not None:
        msg = "root_only and package cannot be combined: an element is either filed or not"
        raise ValueError(msg)
    criteria = ElementFilter(
        element_types=tuple(element_type or ()),
        layers=tuple(layer or ()),
        search=search,
        package_id=package,
        include_subpackages=include_subpackages,
        root_only=root_only,
        limit=limit,
        offset=offset,
    )
```

(docstring : « …or filed in a package (subpackages included unless told otherwise). »)

`api/diagrams.py` : importer `Annotated`, `Query`, `UNSET` :

```python
@router.get("/diagrams", response_model=list[DiagramSummaryRead])
async def list_diagrams(
    diagrams: Diagrams,
    package: Annotated[UUID | None, Query()] = None,
    include_subpackages: Annotated[bool, Query()] = True,
    root_only: Annotated[bool, Query()] = False,
) -> list[DiagramSummaryRead]:
    """Every saved diagram, sorted by name — or only those filed in a package."""
    listed = await diagrams.list_diagrams(
        package_id=package, include_subpackages=include_subpackages, root_only=root_only
    )
    return [DiagramSummaryRead.of(diagram) for diagram in listed]
```

`create_diagram` : `responses=REJECTED` et `created = await diagrams.create(name=body.name, description=body.description, package_id=body.package_id)` ; `update_diagram` :

```python
    package = body.package_id if "package_id" in body.model_fields_set else UNSET
    updated = await diagrams.update(
        diagram_id, name=body.name, description=body.description, package_id=package
    )
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/api backend/tests/e2e
git commit -m "feat(api): report the package of elements and diagrams and filter by it" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Clôture du lot 1 — OpenAPI, intégration, ADR des paquets

**Files:**
- Modify (générés) : `backend/openapi.json`, `frontend/src/api/schema.d.ts`
- Modify: `frontend/tests/support/api.ts` (`anElement`)
- Create: `docs/adr/00NN-les-paquets-classement-en-liste-d-adjacence.md` (prochain numéro libre ; 0042 aujourd'hui)

**Interfaces:**
- Consumes: tout le lot 1.
- Produces: client TypeScript contenant `PackageRead`, `PackageCreate`, `PackageUpdate`, `MoveElements`, `ElementRead.package_id`, `DiagramSummaryRead.package_id`, chemins `/packages`, `/packages/{package_id}`, `/elements/move`.

- [ ] **Step 1: Régénérer le contrat**

Run: `make openapi && git diff --stat backend/openapi.json frontend/src/api/schema.d.ts`
Expected: les deux fichiers changent ; `grep -c '"/packages"' backend/openapi.json` ≥ 1 ; `grep -n "PackageRead" frontend/src/api/schema.d.ts` trouve le composant.

- [ ] **Step 1 bis: Garder les doublures du SPA conformes au contrat** — `ElementRead.package_id` est désormais un champ requis (nullable) du client généré ; `vue-tsc` type-vérifie `frontend/tests/`. Dans `frontend/tests/support/api.ts`, fonction `anElement`, ajouter `package_id: null,` après `properties: {},` (aucun autre test ne construit un `ElementRead` complet : `DiagramsSection.spec.ts` passe des objets littéraux non typés).

- [ ] **Step 2: La porte qualité**

Run: `make check`
Expected: vert (lint-check, lint-fe, typecheck, openapi-check, test ≥ 90 % de couverture, test-fe, pipelines-check). En cas d'échec de format : `make lint` puis relire le diff avant de relancer.

- [ ] **Step 3: L'intégration contre les conteneurs jetables**

Run: `make test-integration`
Expected: vert, y compris `test_packages_schema.py`, `test_package_store.py`, `test_diagram_store.py`, `test_element_cascade.py` (la suppression d'un élément rangé dans un paquet fonctionne : la FK est côté élément) et la chaîne `upgrade head` / `downgrade base` complète.

- [ ] **Step 4: Rédiger l'ADR** — `docs/adr/00NN-les-paquets-classement-en-liste-d-adjacence.md` (remplacer `NN` et le numéro du titre par le prochain libre au moment de l'exécution)

```markdown
---
titre: Les paquets — un classement en liste d'adjacence, distinct du grouping ArchiMate
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/packages.py, backend/src/ea/domain/errors.py, backend/src/ea/domain/ports.py, backend/src/ea/domain/model.py, backend/src/ea/db/models/package.py, backend/migrations/versions/0009_packages.py, backend/src/ea/repositories/package_store.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/services/packages.py, backend/src/ea/services/diagrams.py, backend/src/ea/api/packages.py, backend/src/ea/api/schemas.py, backend/src/ea/api/errors.py, frontend/src/features/packages, frontend/src/features/elements, frontend/src/features/diagrams
---

# NN. Les paquets — un classement en liste d'adjacence, distinct du grouping ArchiMate

Date : 2026-10-10
Statut : Proposition

## Contexte

Le catalogue est une liste plate : à quelques centaines d'éléments et une
dizaine de diagrammes, personne ne retrouve plus rien sans taper un nom. Sparx
EA résout cela par son *Project Browser*, un arbre de paquets où l'on range ce
que l'on modélise. Nous voulons la même chose, avec une contrainte que Sparx
n'a pas : les mêmes éléments sont lus par un agent (MCP), par un pipeline qui
écrit à la racine, et par des traversées (voisinage, impact) qui ne doivent
jamais être perturbées par la façon dont une équipe range ses dossiers.

ArchiMate a déjà un concept voisin, le `grouping`. C'est un *fait* du graphe : il
porte des relations (composition, agrégation), apparaît dans le voisinage et
l'impact, et son existence change ce que le modèle dit. Un dossier, lui, ne dit
rien du système : le déplacer ne doit changer aucune réponse d'analyse.

## Décision

Un **paquet** est une ligne de `packages(id, parent_id, name, description,
created_at, updated_at)` ; `elements.package_id` et `diagrams.package_id` sont
des clés étrangères nullables (`NULL` = « à la racine »). Un élément ou un
diagramme est dans **au plus un** paquet.

| Point | Choix |
|---|---|
| Modèle | Liste d'adjacence ; l'arbre est servi à plat (`GET /packages`, au plus 2 000 paquets) et le SPA le monte. |
| Noms | Frères uniques sans tenir compte de la casse, par deux index partiels (`uq_packages_root_name`, `uq_packages_child_name`) — pas de `NULLS NOT DISTINCT`, qui dépendrait de la version de PostgreSQL de l'Infra. Pas de `/` dans un nom, pour rendre un chemin `A / B / C`. |
| Suppression | Les trois clés sont `ON DELETE RESTRICT`. Un paquet non vide est refusé (`409 package_not_empty`) ; `?move_contents_to=<id\|root>` déplace le contenu direct dans la même transaction. Jamais de `CASCADE` (il détruirait des faits du graphe sur un geste de rangement) ni de `SET NULL` (il disperserait tout à la racine sans que personne l'ait décidé). |
| Cycles et profondeur | `check_tree()`, fonction pure du domaine sur la liste à plat que le service charge de toute façon : pas de cycle, 12 niveaux au plus. |
| Sous-arbre | Le filtre d'éléments par paquet inclut les sous-paquets par une CTE récursive à paramètre lié (`_subtree`). |
| Rangement | `Element.package_id` n'est pas dans `element_row()` : un `save_element` n'écrase jamais un rangement fait entre-temps. Un déplacement ne touche pas `updated_at`. |
| Unicité des éléments | `UniqueConstraint(element_type, name)` reste globale : le pipeline `alimenter-catalogue` et l'IPAM s'y appuient. |
| Droits | Ceux du rôle global : lecture = tout appelant, écriture = `ea-editor`. Pas de droits par paquet. |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Utiliser le `grouping` ArchiMate | Aucun nouveau concept | Un fait du graphe : apparaît dans voisinage et impact, exige des relations pour ranger | Écartée |
| Table de fermeture ou `ltree` | Sous-arbre en une jointure | Deuxième vérité à tenir cohérente ; inutile à quelques milliers d'éléments | Écartée, à mesurer avant de revenir dessus |
| Un élément dans plusieurs paquets | Souple | Le rôle des « vues multiples » est celui des diagrammes ; l'arbre perd son sens de dossier | Écartée |
| `ON DELETE CASCADE` / `SET NULL` | Suppression en un geste | Détruit ou disperse sans décision | Écartée |
| Détection de cycle par CTE sur les ancêtres | Une seule requête | Moins testable que la fonction pure ; même fenêtre de course | Écartée au profit de `check_tree()` |
| `NULLS NOT DISTINCT` | Un seul index | Version de PostgreSQL exigée | Écartée |

## Conséquences

- L'arbre est chargé en entier : au-delà de 2 000 paquets il faudra le servir
  paresseusement (le plafond est une erreur `422 too_many_packages`, pas un
  silence).
- Deux déplacements concurrents peuvent former un cycle que `check_tree()` ne
  voit pas (lecture puis écriture sans verrou) : le risque est identique avec une
  CTE ; un verrou consultatif le fermerait si le cas se présente.
- `Element` gagne un champ ; tout code qui reconstruit un `Element` à la main
  doit passer par `dataclasses.replace`.
- Le pipeline crée toujours à la racine ; un paramètre `package` est possible
  plus tard par l'API.
- Les requêtes enregistrées (ADR suivant) nomment un paquet par son identifiant,
  sans clé étrangère : un paquet supprimé les laisse intactes et les fait
  répondre `422 unknown_package`.

## Références

- ADR liés : [0033](0033-postgresql-seul-pour-le-graphe.md), [0031](0031-diagrammes-enregistres.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md)
- Spec : `docs/superpowers/specs/2026-10-10-01-referentiel-structure-design.md`
- Code concerné : `backend/src/ea/domain/packages.py`, `backend/src/ea/repositories/package_store.py`, `backend/src/ea/services/packages.py`, `backend/src/ea/api/packages.py`
```

- [ ] **Step 5: Commit**

```bash
git add backend/openapi.json frontend/src/api/schema.d.ts frontend/tests/support/api.ts docs/adr
git commit -m "docs(adr): record the package tree and regenerate the client" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# LOT 2 — L'arbre de paquets dans le SPA

### Task 10: Le montage de l'arbre et le composable `usePackages`

**Files:**
- Create: `frontend/src/features/packages/packageTree.ts`
- Create: `frontend/src/features/packages/usePackages.ts`
- Modify: `frontend/tests/support/api.ts` (ajout de `aPackage`, après `anElement`)
- Test: `frontend/tests/packageTree.spec.ts`, `frontend/tests/usePackages.spec.ts`

**Interfaces:**
- Consumes: `components['schemas']['PackageRead' | 'PackageCreate' | 'PackageUpdate']` du client généré (Task 9) ; `api`, `unwrap` (`src/lib/api.ts`) ; `useLatestRequest` (`src/lib/latest.ts`).
- Produces:
  - `packageTree.ts` : `type PackageRead`, `type TreeNode = { package: PackageRead; depth: number; children: TreeNode[] }`, `ALL = ''`, `ROOT = 'root'`, `ELEMENT_DRAG_TYPE = 'application/x-ea-element'`, `buildTree(flat: readonly PackageRead[]): TreeNode[]`, `visibleNodes(tree: readonly TreeNode[], expanded: ReadonlySet<string>): TreeNode[]`, `ancestorsOf(id: string, flat: readonly PackageRead[]): string[]` (ids des ancêtres, du plus haut au parent direct, sans `id` lui-même ; `[]` si `id` est inconnu, `''` ou `'root'`).
  - `usePackages()` : `{ packages: Ref<PackageRead[]>, byId: ComputedRef<Map<string, PackageRead>>, tree: ComputedRef<TreeNode[]>, status, error, load(): Promise<void>, create(body: PackageCreate): Promise<PackageRead>, update(id: string, body: PackageUpdate): Promise<PackageRead>, remove(id: string, moveContentsTo?: string): Promise<void>, moveElements(elementIds: string[], packageId: string | null): Promise<void> }`.
  - `aPackage(overrides?: Partial<PackageRead>): PackageRead` (doublure de test).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `frontend/tests/support/api.ts` (déclarer `type PackageRead = components['schemas']['PackageRead']` avec les autres types en tête) :

```ts
/** A package as `GET /packages` returns it — no children, no elements, at the root. */
export function aPackage(overrides: Partial<PackageRead> = {}): PackageRead {
  return {
    id: 'aaaaaaaa-0000-4000-8000-00000000f001',
    parent_id: null,
    name: 'Finance',
    description: '',
    path: 'Finance',
    element_count: 0,
    diagram_count: 0,
    child_count: 0,
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
    ...overrides,
  }
}
```

`frontend/tests/packageTree.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import { ancestorsOf, buildTree, visibleNodes } from '../src/features/packages/packageTree'
import { aPackage } from './support/api'

const FINANCE = aPackage({ id: 'f', name: 'Finance' })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f' })
const FORECAST = aPackage({ id: 'p', name: 'Prévisions', parent_id: 'b' })
const ACHATS = aPackage({ id: 'a', name: 'achats' })

describe('buildTree', () => {
  it('nests the flat list by parent and sorts the siblings by name, case aside', () => {
    const tree = buildTree([FORECAST, FINANCE, ACHATS, BUDGET])

    expect(tree.map((node) => node.package.name)).toEqual(['achats', 'Finance'])
    expect(tree[1].children.map((node) => node.package.name)).toEqual(['Budget'])
    expect(tree[1].children[0].children[0].depth).toBe(2)
  })

  it('treats a package whose parent is not in the list as a root rather than losing it', () => {
    const orphan = aPackage({ id: 'o', name: 'Orpheline', parent_id: 'disparu' })

    expect(buildTree([orphan]).map((node) => node.package.id)).toEqual(['o'])
  })

  it('does not loop on a cycle: the packages of a cycle are not reachable from a root', () => {
    const a = aPackage({ id: 'x', name: 'X', parent_id: 'y' })
    const b = aPackage({ id: 'y', name: 'Y', parent_id: 'x' })

    expect(buildTree([a, b])).toEqual([])
  })
})

describe('visibleNodes', () => {
  it('hides the subtree of a collapsed node and shows that of an expanded one', () => {
    const tree = buildTree([FINANCE, BUDGET, FORECAST])

    expect(visibleNodes(tree, new Set()).map((n) => n.package.id)).toEqual(['f'])
    expect(visibleNodes(tree, new Set(['f'])).map((n) => n.package.id)).toEqual(['f', 'b'])
    expect(visibleNodes(tree, new Set(['f', 'b'])).map((n) => n.package.id)).toEqual(['f', 'b', 'p'])
  })
})

describe('ancestorsOf', () => {
  it('lists the ids from the top down to the parent, the package itself left out', () => {
    expect(ancestorsOf('p', [FINANCE, BUDGET, FORECAST])).toEqual(['f', 'b'])
  })

  it('answers nothing for the pseudo-nodes and for an id the list does not know', () => {
    expect(ancestorsOf('', [FINANCE])).toEqual([])
    expect(ancestorsOf('root', [FINANCE])).toEqual([])
    expect(ancestorsOf('disparu', [FINANCE])).toEqual([])
  })
})
```

`frontend/tests/usePackages.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { usePackages } from '../src/features/packages/usePackages'
import { aPackage, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('usePackages', () => {
  it('reads the flat list and builds the tree from it', async () => {
    stubApi([
      {
        path: '/packages',
        body: [aPackage({ id: 'f', name: 'Finance' }), aPackage({ id: 'b', name: 'Budget', parent_id: 'f' })],
      },
    ])

    const store = usePackages()
    await store.load()

    expect(store.packages.value).toHaveLength(2)
    expect(store.tree.value[0].children[0].package.name).toBe('Budget')
    expect(store.byId.value.get('b')?.name).toBe('Budget')
    expect(store.status.value).toBe('ready')
  })

  it('creates a package then reloads, so the list is the stored one', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ name: 'RH' }) },
    ])

    const created = await usePackages().create({ name: 'RH', parent_id: null })

    expect(created.name).toBe('RH')
    expect(calls.map((call) => call.method)).toEqual(['POST', 'GET'])
    expect(calls[0].body).toEqual({ name: 'RH', parent_id: null })
  })

  it('sends an explicit null to put a package back at the root', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await usePackages().update('b', { parent_id: null })

    expect(calls[0].body).toEqual({ parent_id: null })
  })

  it('deletes with the target of the contents when there is one, and without otherwise', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'DELETE', path: '/packages/b', status: 204 },
    ])
    const store = usePackages()

    await store.remove('b')
    await store.remove('b', 'root')

    const deletes = calls.filter((call) => call.method === 'DELETE')
    expect(deletes[0].url.searchParams.has('move_contents_to')).toBe(false)
    expect(deletes[1].url.searchParams.get('move_contents_to')).toBe('root')
  })

  it('raises the refusal of a non-empty package for the screen to show', async () => {
    stubApi([
      {
        method: 'DELETE',
        path: '/packages/b',
        status: 409,
        body: { error: 'package_not_empty', detail: 'Le paquet contient 2 éléments.' },
      },
    ])

    await expect(usePackages().remove('b')).rejects.toThrow('Le paquet contient 2 éléments.')
  })

  it('files elements and reloads the counts', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'POST', path: '/elements/move', status: 204 },
    ])

    await usePackages().moveElements(['e1', 'e2'], null)

    expect(calls[0].body).toEqual({ element_ids: ['e1', 'e2'], package_id: null })
    expect(calls[1].method).toBe('GET')
  })

  it('keeps the answer to the last load, whichever arrives first', async () => {
    const calls = deferApi()
    const store = usePackages()

    const first = store.load()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = store.load()
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer([aPackage({ name: 'Récent' })])
    calls[0].answer([aPackage({ name: 'Ancien' })])
    await Promise.all([first, second])

    expect(store.packages.value.map((p) => p.name)).toEqual(['Récent'])
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/packageTree.spec.ts tests/usePackages.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/packages/packageTree"`.

- [ ] **Step 3: Implémenter** — `frontend/src/features/packages/packageTree.ts`

```ts
// The classification tree, built from the flat list `GET /packages` serves.
//
// The API gives each package its parent and nothing more; the nesting is the
// SPA's job, and it is pure so it is tested without mounting anything. No
// rule lives here — a cycle, a depth, a duplicate name are the API's to
// refuse; this only has to survive whatever it is handed.
import type { components } from '../../api/schema'

export type PackageRead = components['schemas']['PackageRead']

export type TreeNode = { package: PackageRead; depth: number; children: TreeNode[] }

/** The three things a node of the tree can select: no filter, the unfiled, one package. */
export const ALL = ''
export const ROOT = 'root'

/** What a dragged catalogue row carries, and what a node of the tree accepts. */
export const ELEMENT_DRAG_TYPE = 'application/x-ea-element'

function byName(one: PackageRead, other: PackageRead): number {
  return one.name.localeCompare(other.name, 'fr', { sensitivity: 'base' })
}

export function buildTree(flat: readonly PackageRead[]): TreeNode[] {
  const known = new Set(flat.map((entry) => entry.id))
  const byParent = new Map<string | null, PackageRead[]>()
  for (const entry of flat) {
    // A parent the list does not hold makes a root, so nothing is lost.
    const key = entry.parent_id !== null && known.has(entry.parent_id) ? entry.parent_id : null
    byParent.set(key, [...(byParent.get(key) ?? []), entry])
  }
  const build = (parent: string | null, depth: number): TreeNode[] =>
    [...(byParent.get(parent) ?? [])].sort(byName).map((entry) => ({
      package: entry,
      depth,
      children: build(entry.id, depth + 1),
    }))
  return build(null, 0)
}

/** The nodes a reader can see, in tree order: a collapsed node hides its subtree. */
export function visibleNodes(tree: readonly TreeNode[], expanded: ReadonlySet<string>): TreeNode[] {
  return tree.flatMap((node) => [
    node,
    ...(expanded.has(node.package.id) ? visibleNodes(node.children, expanded) : []),
  ])
}

/** The ids on the way from the top down to the parent of `id`, `id` itself left out. */
export function ancestorsOf(id: string, flat: readonly PackageRead[]): string[] {
  const byId = new Map(flat.map((entry) => [entry.id, entry]))
  const path: string[] = []
  let current = byId.get(id)
  // Bounded by the list's own size: a cycle must not hang the screen.
  for (let step = 0; current?.parent_id && step < flat.length; step += 1) {
    path.unshift(current.parent_id)
    current = byId.get(current.parent_id)
  }
  return path
}
```

`frontend/src/features/packages/usePackages.ts`

```ts
// The classification tree: the flat list, the tree built from it, and the writes.
//
// The tree is served in one piece (at most 2 000 packages) and counts what each
// package holds directly, so every write reloads: a count is the server's to
// compute, not ours to patch up after a move.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { buildTree, type PackageRead } from './packageTree'

export type PackageCreate = components['schemas']['PackageCreate']
export type PackageUpdate = components['schemas']['PackageUpdate']

export function usePackages() {
  const packages = ref<PackageRead[]>([])
  // The tree is reloaded after every write and by every screen that shows it:
  // the answer on screen is the one to the last question.
  const listing = useLatestRequest()
  const { status, error } = listing

  const tree = computed(() => buildTree(packages.value))
  const byId = computed(() => new Map(packages.value.map((entry) => [entry.id, entry])))

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/packages', { signal })),
      (answer) => {
        packages.value = answer
      },
    )
  }

  /** A refusal — a name already taken, a tree too deep — is thrown, for the form to show. */
  async function create(body: PackageCreate): Promise<PackageRead> {
    const created = unwrap(await api.POST('/packages', { body }))
    await load()
    return created
  }

  /** `parent_id: null` puts the package at the root; leaving it out keeps the parent. */
  async function update(id: string, body: PackageUpdate): Promise<PackageRead> {
    const updated = unwrap(
      await api.PATCH('/packages/{package_id}', { params: { path: { package_id: id } }, body }),
    )
    await load()
    return updated
  }

  /** Without `moveContentsTo` a package that holds anything is refused (409). */
  async function remove(id: string, moveContentsTo?: string): Promise<void> {
    unwrap(
      await api.DELETE('/packages/{package_id}', {
        params: {
          path: { package_id: id },
          query: moveContentsTo === undefined ? {} : { move_contents_to: moveContentsTo },
        },
      }),
    )
    await load()
  }

  /** `packageId: null` files the elements at the root. */
  async function moveElements(elementIds: string[], packageId: string | null): Promise<void> {
    unwrap(
      await api.POST('/elements/move', {
        body: { element_ids: elementIds, package_id: packageId },
      }),
    )
    await load()
  }

  return { packages, byId, tree, status, error, load, create, update, remove, moveElements }
}
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/packageTree.spec.ts tests/usePackages.spec.ts && npm run typecheck && npm run lint`
Expected: PASS ; typecheck et ESLint propres.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/packages frontend/tests/packageTree.spec.ts frontend/tests/usePackages.spec.ts frontend/tests/support/api.ts
git commit -m "feat(spa): build the package tree and its composable" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Le composant `PackageTree.vue`

**Files:**
- Create: `frontend/src/features/packages/PackageTree.vue`
- Test: `frontend/tests/PackageTree.spec.ts`

**Interfaces:**
- Consumes: `buildTree`, `visibleNodes`, `ancestorsOf`, `ALL`, `ROOT`, `ELEMENT_DRAG_TYPE`, `PackageRead`, `TreeNode` (Task 10).
- Produces: composant `PackageTree` — props `{ packages: PackageRead[]; selected: string; canDrop: boolean }` (`selected` : `''` tous, `'root'` non rangés, ou un id de paquet) ; événements `select(value: string)` et `drop(elementId: string, target: string)` (`target` : `'root'` ou un id). `<ul role="tree" aria-label="Paquets">` aplati ; chaque `<li role="treeitem">` porte `aria-level`, `aria-selected`, `aria-expanded` (si enfants), `data-key`.

- [ ] **Step 1: Écrire le test qui échoue** — `frontend/tests/PackageTree.spec.ts`

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { ELEMENT_DRAG_TYPE } from '../src/features/packages/packageTree'
import PackageTree from '../src/features/packages/PackageTree.vue'
import { aPackage } from './support/api'

const FINANCE = aPackage({ id: 'f', name: 'Finance', child_count: 1, element_count: 2 })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f', path: 'Finance / Budget' })
const RH = aPackage({ id: 'r', name: 'RH' })

function show(props: Record<string, unknown> = {}) {
  return render(PackageTree, {
    props: { packages: [BUDGET, RH, FINANCE], selected: '', canDrop: true, ...props },
  })
}

const item = (name: RegExp | string) => screen.getByRole('treeitem', { name })

describe('PackageTree', () => {
  it('is a tree with the two pseudo-nodes first and the roots collapsed', () => {
    show()

    expect(screen.getByRole('tree', { name: 'Paquets' })).toBeInTheDocument()
    expect(screen.getAllByRole('treeitem').map((node) => node.textContent?.trim())).toEqual([
      'Tous les éléments',
      'Racine (sans paquet)',
      expect.stringContaining('Finance'),
      expect.stringContaining('RH'),
    ])
    expect(item(/Finance/)).toHaveAttribute('aria-expanded', 'false')
    expect(item(/RH/)).not.toHaveAttribute('aria-expanded')
  })

  it('shows what each package holds directly', () => {
    show()

    expect(item(/Finance/)).toHaveTextContent('2')
  })

  it('selects by click: a package by its id, the unfiled as root, everything as empty', async () => {
    const { emitted } = show()

    await fireEvent.click(item(/RH/))
    await fireEvent.click(item(/Racine/))
    await fireEvent.click(item(/Tous/))

    expect(emitted().select).toEqual([['r'], ['root'], ['']])
  })

  it('expands with the right arrow, collapses with the left one, and moves with up and down', async () => {
    show()
    const finance = item(/Finance/)

    await fireEvent.keyDown(finance, { key: 'ArrowRight' })
    expect(finance).toHaveAttribute('aria-expanded', 'true')
    expect(item(/Budget/)).toHaveAttribute('aria-level', '2')

    await fireEvent.keyDown(finance, { key: 'ArrowRight' })
    await waitFor(() => expect(item(/Budget/)).toHaveFocus())

    await fireEvent.keyDown(item(/Budget/), { key: 'ArrowLeft' })
    await waitFor(() => expect(finance).toHaveFocus())
    await fireEvent.keyDown(finance, { key: 'ArrowLeft' })
    expect(finance).toHaveAttribute('aria-expanded', 'false')

    await fireEvent.keyDown(finance, { key: 'ArrowDown' })
    await waitFor(() => expect(item(/RH/)).toHaveFocus())
  })

  it('selects with Enter', async () => {
    const { emitted } = show()

    await fireEvent.keyDown(item(/RH/), { key: 'Enter' })

    expect(emitted().select).toEqual([['r']])
  })

  it('opens the ancestors of the selected package, and marks only that one selected', () => {
    show({ selected: 'b' })

    expect(item(/Budget/)).toHaveAttribute('aria-selected', 'true')
    expect(item(/Finance/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByRole('treeitem', { selected: true })).toHaveLength(1)
  })

  it('selects nothing when the URL names a package that is gone', () => {
    show({ selected: 'disparu' })

    expect(screen.queryAllByRole('treeitem', { selected: true })).toHaveLength(0)
  })

  it('emits a drop on a package or on the root, and ignores one on "all"', async () => {
    const { emitted } = show()
    const dataTransfer = { getData: () => 'e1', types: [ELEMENT_DRAG_TYPE] }

    await fireEvent.drop(item(/RH/), { dataTransfer })
    await fireEvent.drop(item(/Racine/), { dataTransfer })
    await fireEvent.drop(item(/Tous/), { dataTransfer })

    expect(emitted().drop).toEqual([
      ['e1', 'r'],
      ['e1', 'root'],
    ])
  })

  it('accepts no drop at all from a reader', async () => {
    const { emitted } = show({ canDrop: false })

    await fireEvent.drop(item(/RH/), {
      dataTransfer: { getData: () => 'e1', types: [ELEMENT_DRAG_TYPE] },
    })

    expect(emitted().drop).toBeUndefined()
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/PackageTree.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/packages/PackageTree.vue"`.

- [ ] **Step 3: Implémenter** — `frontend/src/features/packages/PackageTree.vue`

```vue
<script setup lang="ts">
// The classification tree, drawn as a WAI-ARIA tree.
//
// It is flat in the DOM — one `<li role="treeitem">` per visible node, the
// depth in `aria-level` — which keeps the keyboard handling to a list index,
// and it knows nothing of the API: it draws the packages it is given, says
// what was selected, and says what was dropped on what. The screen decides.
//
// Two nodes come before the packages: "all" (no filter) and "the root" (the
// unfiled). A package id that names nothing selects nothing — a link to a
// deleted package must not break the screen.
import { computed, nextTick, ref, watch } from 'vue'

import {
  ALL,
  ELEMENT_DRAG_TYPE,
  ROOT,
  ancestorsOf,
  buildTree,
  visibleNodes,
  type PackageRead,
  type TreeNode,
} from './packageTree'

const props = defineProps<{ packages: PackageRead[]; selected: string; canDrop: boolean }>()
const emit = defineEmits<{ select: [value: string]; drop: [elementId: string, target: string] }>()

type Item = {
  key: string
  value: string
  label: string
  depth: number
  count: number | null
  node: TreeNode | null
}

const ALL_KEY = '__all__'

const expanded = ref(new Set<string>())
const focusKey = ref(ALL_KEY)
const dropTarget = ref('')
const treeElement = ref<HTMLElement | null>(null)

const items = computed<Item[]>(() => [
  { key: ALL_KEY, value: ALL, label: 'Tous les éléments', depth: 0, count: null, node: null },
  { key: ROOT, value: ROOT, label: 'Racine (sans paquet)', depth: 0, count: null, node: null },
  ...visibleNodes(buildTree(props.packages), expanded.value).map((node) => ({
    key: node.package.id,
    value: node.package.id,
    label: node.package.name,
    depth: node.depth,
    count: node.package.element_count,
    node,
  })),
])

// The path to the selected package is opened, so a shared link lands on a
// tree in which the selection can be seen.
watch(
  () => [props.selected, props.packages] as const,
  ([selected, packages]) => {
    const opened = ancestorsOf(selected, packages)
    if (opened.length > 0) {
      expanded.value = new Set([...expanded.value, ...opened])
    }
    focusKey.value = packages.some((entry) => entry.id === selected)
      ? selected
      : selected === ROOT
        ? ROOT
        : ALL_KEY
  },
  { immediate: true },
)

function hasChildren(item: Item): boolean {
  return (item.node?.children.length ?? 0) > 0
}

function toggle(key: string, open: boolean): void {
  const next = new Set(expanded.value)
  if (open) {
    next.add(key)
  } else {
    next.delete(key)
  }
  expanded.value = next
}

async function focus(key: string): Promise<void> {
  focusKey.value = key
  await nextTick()
  treeElement.value?.querySelector<HTMLElement>(`[data-key="${key}"]`)?.focus()
}

function onKeydown(event: KeyboardEvent): void {
  const current = (event.target as HTMLElement).closest<HTMLElement>('[role="treeitem"]')
  const list = items.value
  const index = list.findIndex((candidate) => candidate.key === current?.dataset.key)
  const item = list[index]
  if (!item) {
    return
  }
  const go = (to: number): void => {
    event.preventDefault()
    const target = list[Math.max(0, Math.min(to, list.length - 1))]
    if (target) {
      void focus(target.key)
    }
  }
  switch (event.key) {
    case 'ArrowDown':
      go(index + 1)
      break
    case 'ArrowUp':
      go(index - 1)
      break
    case 'Home':
      go(0)
      break
    case 'End':
      go(list.length - 1)
      break
    case 'ArrowRight':
      if (item.node && hasChildren(item)) {
        event.preventDefault()
        if (expanded.value.has(item.key)) {
          void focus(item.node.children[0]?.package.id ?? item.key)
        } else {
          toggle(item.key, true)
        }
      }
      break
    case 'ArrowLeft':
      if (item.node) {
        event.preventDefault()
        const parent = item.node.package.parent_id
        if (hasChildren(item) && expanded.value.has(item.key)) {
          toggle(item.key, false)
        } else if (parent && list.some((candidate) => candidate.key === parent)) {
          void focus(parent)
        }
      }
      break
    case 'Enter':
    case ' ':
      event.preventDefault()
      emit('select', item.value)
      break
  }
}

function acceptsDrops(item: Item): boolean {
  return props.canDrop && item.value !== ALL
}

function onDragOver(event: DragEvent, item: Item): void {
  if (!acceptsDrops(item) || !event.dataTransfer?.types.includes(ELEMENT_DRAG_TYPE)) {
    return
  }
  event.preventDefault()
  dropTarget.value = item.key
}

function onDrop(event: DragEvent, item: Item): void {
  dropTarget.value = ''
  if (!acceptsDrops(item)) {
    return
  }
  const elementId = event.dataTransfer?.getData(ELEMENT_DRAG_TYPE)
  if (elementId) {
    event.preventDefault()
    emit('drop', elementId, item.value)
  }
}
</script>

<template>
  <ul ref="treeElement" class="tree" role="tree" aria-label="Paquets" @keydown="onKeydown">
    <li
      v-for="item in items"
      :key="item.key"
      role="treeitem"
      class="node"
      :class="{
        'node--selected': selected === item.value,
        'node--drop': dropTarget === item.key,
      }"
      :data-key="item.key"
      :aria-level="item.depth + 1"
      :aria-selected="selected === item.value"
      :aria-expanded="hasChildren(item) ? expanded.has(item.key) : undefined"
      :tabindex="focusKey === item.key ? 0 : -1"
      :style="{ paddingLeft: `${0.4 + item.depth * 1.1}rem` }"
      @click="emit('select', item.value)"
      @focus="focusKey = item.key"
      @dragover="onDragOver($event, item)"
      @dragleave="dropTarget = ''"
      @drop="onDrop($event, item)"
    >
      <!-- The keyboard has the arrows; this is the mouse's way to the same place. -->
      <span
        v-if="hasChildren(item)"
        class="twisty"
        aria-hidden="true"
        @click.stop="toggle(item.key, !expanded.has(item.key))"
      >
        {{ expanded.has(item.key) ? '▾' : '▸' }}
      </span>
      <span v-else class="twisty" aria-hidden="true" />
      <span class="label">{{ item.label }}</span>
      <span v-if="item.count !== null" class="count">{{ item.count }}</span>
    </li>
  </ul>
</template>

<style scoped>
.tree {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 1px;
}
.node {
  display: flex;
  align-items: baseline;
  gap: 0.3rem;
  padding-block: 0.25rem;
  padding-right: 0.4rem;
  border-radius: 6px;
  cursor: pointer;
}
.node:hover {
  background: color-mix(in srgb, currentColor 8%, transparent);
}
.node--selected {
  font-weight: 600;
  background: color-mix(in srgb, currentColor 14%, transparent);
}
.node--drop {
  outline: 2px dashed currentColor;
}
.node:focus-visible {
  outline: 2px solid currentColor;
}
.twisty {
  display: inline-block;
  width: 1rem;
  flex: none;
  text-align: center;
}
.label {
  flex: 1;
  overflow-wrap: anywhere;
}
.count {
  opacity: 0.65;
  font-size: 0.85em;
}
</style>
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/PackageTree.spec.ts && npm run typecheck && npm run lint`
Expected: PASS. Si `tsconfig` active `noUncheckedIndexedAccess`, les gardes `list[index]` / `children[0]?` ci-dessus sont déjà écrites pour passer.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/packages/PackageTree.vue frontend/tests/PackageTree.spec.ts
git commit -m "feat(spa): add the package tree component" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Le gestionnaire de paquets (`PackageManager.vue`)

**Files:**
- Create: `frontend/src/features/packages/PackageManager.vue`
- Test: `frontend/tests/PackageManager.spec.ts`

**Interfaces:**
- Consumes: `usePackages()` (Task 10) ; `useMe().canWrite` (`src/lib/me.ts`, doublé par `tests/setup.ts`) ; `ROOT`, `PackageRead` ; `messageOf` (`src/lib/api.ts`).
- Produces: composant `PackageManager` — props `{ store: ReturnType<typeof usePackages>; selected: string }` ; événement `select(value: string)` (le paquet créé, ou `''` après une suppression) ; rend **rien** pour un lecteur ; `role="toolbar"` « Gérer les paquets », boutons « Nouveau paquet », « Renommer », « Déplacer », « Supprimer » (les trois derniers désactivés sans paquet sélectionné), un `<form>` à la fois, les refus de l'API dans un `role="alert"`.

- [ ] **Step 1: Écrire le test qui échoue** — `frontend/tests/PackageManager.spec.ts`

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import PackageManager from '../src/features/packages/PackageManager.vue'
import { usePackages } from '../src/features/packages/usePackages'
import { useMe } from '../src/lib/me'
import { aPackage, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f', path: 'Finance / Budget' })
const LIST: Route = { path: '/packages', body: [FINANCE, BUDGET] }

async function show(selected: string, routes: Route[] = []) {
  const calls = stubApi([...routes, LIST])
  const store = usePackages()
  await store.load()
  const view = render(PackageManager, { props: { store, selected } })
  return { calls, view }
}

describe('PackageManager', () => {
  it('offers a reader nothing at all', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    await show('f')

    expect(screen.queryByRole('toolbar')).toBeNull()
    expect(screen.queryByRole('button', { name: /nouveau paquet/i })).toBeNull()
  })

  it('disables rename, move and delete while nothing is selected — or the package is gone', async () => {
    await show('disparu')

    expect(screen.getByRole('button', { name: /nouveau paquet/i })).toBeEnabled()
    for (const name of [/renommer/i, /déplacer/i, /supprimer/i]) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
  })

  it('creates a package at the root when nothing is selected, and selects it', async () => {
    const { calls, view } = await show('', [
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ id: 'n', name: 'RH' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /nouveau paquet/i }))
    await fireEvent.update(screen.getByLabelText(/nom du paquet/i), 'RH')
    await fireEvent.submit(screen.getByRole('form', { name: /nouveau paquet/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'RH',
        parent_id: null,
      }),
    )
    await waitFor(() => expect(view.emitted().select).toEqual([['n']]))
  })

  it('creates inside the selected package', async () => {
    const { calls } = await show('f', [
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ id: 'n' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /nouveau paquet/i }))
    await fireEvent.update(screen.getByLabelText(/nom du paquet/i), 'Achats')
    await fireEvent.submit(screen.getByRole('form', { name: /nouveau paquet/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'Achats',
        parent_id: 'f',
      }),
    )
  })

  it('renames, prefilled with the current name', async () => {
    const { calls } = await show('b', [
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /renommer/i }))
    const field = screen.getByLabelText(/nom du paquet/i)
    expect(field).toHaveValue('Budget')
    await fireEvent.update(field, 'Prévisions')
    await fireEvent.submit(screen.getByRole('form', { name: /renommer/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ name: 'Prévisions' }),
    )
  })

  it('moves to the root with an explicit null, and never offers the package itself as parent', async () => {
    const { calls } = await show('b', [
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /déplacer/i }))
    const parent = screen.getByLabelText(/nouveau parent/i)
    expect(Array.from(parent.querySelectorAll('option')).map((o) => o.textContent)).toEqual([
      'Racine',
      'Finance',
    ])
    await fireEvent.update(parent, 'root')
    await fireEvent.submit(screen.getByRole('form', { name: /déplacer/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ parent_id: null }),
    )
  })

  it('shows why a non-empty package was refused and deletes once told where its contents go', async () => {
    const { calls, view } = await show('f', [
      {
        method: 'DELETE',
        path: '/packages/f',
        status: 409,
        body: { error: 'package_not_empty', detail: 'Finance contient 2 éléments.' },
      },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /supprimer/i }))
    await fireEvent.submit(screen.getByRole('form', { name: /supprimer/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Finance contient 2 éléments.')

    const second = stubApi([{ method: 'DELETE', path: '/packages/f', status: 204 }, LIST])
    await fireEvent.update(screen.getByLabelText(/contenu/i), 'root')
    await fireEvent.submit(screen.getByRole('form', { name: /supprimer/i }))

    await waitFor(() => expect(view.emitted().select).toEqual([['']]))
    const refused = calls.find((call) => call.method === 'DELETE')
    const accepted = second.find((call) => call.method === 'DELETE')
    expect(refused?.url.searchParams.has('move_contents_to')).toBe(false)
    expect(accepted?.url.searchParams.get('move_contents_to')).toBe('root')
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/PackageManager.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/packages/PackageManager.vue"`.

- [ ] **Step 3: Implémenter** — `frontend/src/features/packages/PackageManager.vue`

```vue
<script setup lang="ts">
// The editor's controls for the tree: create, rename, move, delete a package.
//
// A reader gets nothing — the API refuses their writes anyway (docs/adr/0032);
// this only spares them controls that could not work. Every rule (a taken
// name, a cycle, a tree too deep, a package that still holds something) is the
// API's: its refusal is shown as it comes, and the package is left as it was.
import { computed, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { ROOT, type PackageRead } from './packageTree'
import type { usePackages } from './usePackages'

const props = defineProps<{ store: ReturnType<typeof usePackages>; selected: string }>()
const emit = defineEmits<{ select: [value: string] }>()

const { canWrite } = useMe()

type Mode = 'create' | 'rename' | 'move' | 'delete'

const mode = ref<Mode | null>(null)
const name = ref('')
/** The parent chosen by "Déplacer", or what to do with the contents on "Supprimer". */
const target = ref('')
const failure = ref('')
const busy = ref(false)

/** The selected package — or nothing, for `''`, the root, and an id that no longer exists. */
const current = computed<PackageRead | null>(
  () => props.store.byId.value.get(props.selected) ?? null,
)

const candidates = computed(() =>
  props.store.packages.value
    .filter((entry) => entry.id !== current.value?.id)
    .sort((one, other) => one.path.localeCompare(other.path, 'fr', { sensitivity: 'base' })),
)

const LABELS: Record<Mode, string> = {
  create: 'Nouveau paquet',
  rename: 'Renommer',
  move: 'Déplacer',
  delete: 'Supprimer',
}

function open(next: Mode): void {
  failure.value = ''
  mode.value = next
  name.value = next === 'rename' ? (current.value?.name ?? '') : ''
  // "Déplacer" starts on the current parent; "Supprimer" starts on "refuse if not empty".
  target.value = next === 'move' ? (current.value?.parent_id ?? ROOT) : ''
}

async function submit(): Promise<void> {
  const here = current.value
  busy.value = true
  failure.value = ''
  try {
    if (mode.value === 'create') {
      const created = await props.store.create({ name: name.value, parent_id: here?.id ?? null })
      emit('select', created.id)
    } else if (here && mode.value === 'rename') {
      await props.store.update(here.id, { name: name.value })
    } else if (here && mode.value === 'move') {
      await props.store.update(here.id, { parent_id: target.value === ROOT ? null : target.value })
    } else if (here && mode.value === 'delete') {
      await props.store.remove(here.id, target.value || undefined)
      emit('select', '')
    }
    mode.value = null
  } catch (caught) {
    // Kept open, with what was typed: the person fixes the name or picks a target.
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="canWrite" class="manager">
    <div class="bar" role="toolbar" aria-label="Gérer les paquets">
      <button type="button" @click="open('create')">Nouveau paquet</button>
      <button type="button" :disabled="!current" @click="open('rename')">Renommer</button>
      <button type="button" :disabled="!current" @click="open('move')">Déplacer</button>
      <button type="button" class="secondary" :disabled="!current" @click="open('delete')">
        Supprimer
      </button>
    </div>

    <form v-if="mode" class="form" :aria-label="LABELS[mode]" @submit.prevent="submit">
      <div v-if="mode === 'create' || mode === 'rename'" class="field">
        <label for="package-name">Nom du paquet</label>
        <input id="package-name" v-model="name" maxlength="200" autocomplete="off" />
        <small v-if="mode === 'create'">
          {{ current ? `Dans « ${current.path} ».` : 'À la racine.' }}
        </small>
      </div>

      <div v-if="mode === 'move'" class="field">
        <label for="package-parent">Nouveau parent</label>
        <select id="package-parent" v-model="target">
          <option :value="ROOT">Racine</option>
          <option v-for="entry in candidates" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
      </div>

      <template v-if="mode === 'delete'">
        <p>Supprimer « {{ current?.path }} » ? Les éléments et diagrammes ne sont jamais supprimés.</p>
        <div class="field">
          <label for="package-contents">Contenu à déplacer vers…</label>
          <select id="package-contents" v-model="target">
            <option value="">Nulle part (refusé si le paquet n'est pas vide)</option>
            <option :value="ROOT">Racine</option>
            <option v-for="entry in candidates" :key="entry.id" :value="entry.id">
              {{ entry.path }}
            </option>
          </select>
        </div>
      </template>

      <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

      <div class="actions">
        <button type="submit" :disabled="busy">Confirmer</button>
        <button type="button" class="secondary" @click="mode = null">Renoncer</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.manager {
  display: grid;
  gap: 0.6rem;
}
.bar,
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.form {
  display: grid;
  gap: 0.6rem;
  padding: 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
input,
select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
</style>
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/PackageManager.spec.ts && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/packages/PackageManager.vue frontend/tests/PackageManager.spec.ts
git commit -m "feat(spa): add the editor controls of the package tree" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: L'arbre dans la section Éléments (`?package=`, déplacement, glisser-déposer)

**Files:**
- Modify: `frontend/src/features/elements/useElementCatalogue.ts` (`Filters`, `load`)
- Modify: `frontend/src/features/elements/ElementCatalogue.vue` (script, template, styles)
- Test: `frontend/tests/useElementCatalogue.spec.ts` (ajout), `frontend/tests/ElementCatalogue.spec.ts` (adaptation du helper + ajouts)

**Interfaces:**
- Consumes: `PackageTree`, `PackageManager`, `usePackages`, `ROOT`, `ELEMENT_DRAG_TYPE` (Tasks 10-12) ; `GET /elements?package=&include_subpackages=&root_only=`, `POST /elements/move` (Tasks 7-8).
- Produces: `Filters` gagne `package: string` (`''` | `'root'` | id) et `includeSubpackages: boolean` ; URL `?package=<id|root>` (absent = tous) et `?subpackages=0` ; sélection d'un nœud = `router.push`, case « Inclure les sous-paquets » = `router.replace` ; lignes `draggable` pour un éditeur ; bouton « Déplacer vers… » par ligne (éditeur).

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `frontend/tests/useElementCatalogue.spec.ts`, ajouter :

```ts
  it('asks for one package, subpackages included unless told otherwise', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([]) }])
    const catalogue = useElementCatalogue()

    catalogue.filters.package = 'f'
    await catalogue.load()
    catalogue.filters.includeSubpackages = false
    await catalogue.load()

    expect(calls[0].url.searchParams.get('package')).toBe('f')
    expect(calls[0].url.searchParams.has('include_subpackages')).toBe(false)
    expect(calls[1].url.searchParams.get('include_subpackages')).toBe('false')
  })

  it('asks for the unfiled elements with root_only, and no package', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([]) }])
    const catalogue = useElementCatalogue()

    catalogue.filters.package = 'root'
    await catalogue.load()

    const query = calls[0].url.searchParams
    expect(query.get('root_only')).toBe('true')
    expect(query.has('package')).toBe(false)
  })
```

Dans `frontend/tests/ElementCatalogue.spec.ts` : importer `fireEvent` (déjà), `aPackage` depuis `./support/api`, `ELEMENT_DRAG_TYPE` ; modifier le helper pour répondre à `/packages` par défaut (en dernier, pour que les routes d'un test gagnent) :

```ts
const NO_PACKAGES: Route = { path: '/packages', body: [] }

async function renderCatalogue(routes: Route[], query = '') {
  const calls = stubApi([PALETTE, ...routes, NO_PACKAGES])
```

puis ajouter :

```ts
const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })

describe('ElementCatalogue — the package tree', () => {
  it('reads ?package= and ?subpackages=0 into the query', async () => {
    const { calls } = await renderCatalogue(
      [{ path: '/elements', body: aPage([anElement()]) }, { path: '/packages', body: [FINANCE] }],
      '?package=f&subpackages=0',
    )
    await screen.findByText('Facturation')

    const query = calls.find((call) => call.url.pathname === '/elements')?.url.searchParams
    expect(query?.get('package')).toBe('f')
    expect(query?.get('include_subpackages')).toBe('false')
  })

  it('asks for the unfiled elements when the URL says root', async () => {
    const { calls } = await renderCatalogue(
      [{ path: '/elements', body: aPage([]) }],
      '?package=root',
    )

    await waitFor(() =>
      expect(
        calls.find((call) => call.url.pathname === '/elements')?.url.searchParams.get('root_only'),
      ).toBe('true'),
    )
  })

  it('selecting a node pushes it into the URL and refetches', async () => {
    const { calls, router } = await renderCatalogue([
      { path: '/elements', body: aPage([anElement()]) },
      { path: '/packages', body: [FINANCE] },
    ])
    await screen.findByText('Facturation')

    await fireEvent.click(await screen.findByRole('treeitem', { name: /Finance/ }))

    await waitFor(() => expect(router.currentRoute.value.query.package).toBe('f'))
    await waitFor(() =>
      expect(calls.filter((call) => call.url.pathname === '/elements').at(-1)?.url.searchParams.get('package')).toBe('f'),
    )
  })

  it('turns the subpackages off with the checkbox, by replacing the history entry', async () => {
    const { router } = await renderCatalogue(
      [{ path: '/elements', body: aPage([]) }, { path: '/packages', body: [FINANCE] }],
      '?package=f',
    )

    await fireEvent.click(await screen.findByRole('checkbox', { name: /sous-paquets/i }))

    await waitFor(() => expect(router.currentRoute.value.query.subpackages).toBe('0'))
  })

  it('files a dragged row into the package it is dropped on, then reloads', async () => {
    const element = anElement({ name: 'Facturation' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: '/packages', body: [FINANCE] },
      { method: 'POST', path: '/elements/move', status: 204 },
    ])
    await screen.findByText('Facturation')

    await fireEvent.drop(await screen.findByRole('treeitem', { name: /Finance/ }), {
      dataTransfer: { getData: () => element.id, types: [ELEMENT_DRAG_TYPE] },
    })

    await waitFor(() =>
      expect(calls.find((call) => call.url.pathname === '/elements/move')?.body).toEqual({
        element_ids: [element.id],
        package_id: 'f',
      }),
    )
  })

  it('files a row by keyboard with "Déplacer vers…", the root included', async () => {
    const element = anElement({ name: 'Facturation' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: '/packages', body: [FINANCE] },
      { method: 'POST', path: '/elements/move', status: 204 },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /déplacer facturation/i }))
    await fireEvent.update(await screen.findByLabelText(/déplacer vers/i), 'root')
    await fireEvent.click(screen.getByRole('button', { name: /^ranger$/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.url.pathname === '/elements/move')?.body).toEqual({
        element_ids: [element.id],
        package_id: null,
      }),
    )
  })

  it('shows a refused move and leaves the list alone', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: '/packages', body: [FINANCE] },
      {
        method: 'POST',
        path: '/elements/move',
        status: 404,
        body: { error: 'not_found', detail: 'no package with id f' },
      },
    ])
    await screen.findByText('Facturation')

    await fireEvent.drop(await screen.findByRole('treeitem', { name: /Finance/ }), {
      dataTransfer: { getData: () => element.id, types: [ELEMENT_DRAG_TYPE] },
    })

    expect(await screen.findByRole('alert')).toHaveTextContent('no package with id f')
  })

  it('shows an empty list, not an error, for a package that no longer exists', async () => {
    await renderCatalogue(
      [{ path: '/elements', body: aPage([]) }, { path: '/packages', body: [FINANCE] }],
      '?package=disparu',
    )

    expect(await screen.findByText(/aucun élément/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryAllByRole('treeitem', { selected: true })).toHaveLength(0)
  })

  it('gives a reader the tree to read, but nothing to move or to drag', async () => {
    const reader = {
      me: ref({ username: 'reader', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    }
    // Once for the screen, once for `PackageManager`, which asks for itself.
    vi.mocked(useMe).mockReturnValueOnce(reader).mockReturnValueOnce(reader)
    await renderCatalogue([
      { path: '/elements', body: aPage([anElement({ name: 'Facturation' })]) },
      { path: '/packages', body: [FINANCE] },
    ])

    const row = await screen.findByRole('row', { name: /Facturation/ })
    expect(await screen.findByRole('treeitem', { name: /Finance/ })).toBeInTheDocument()
    expect(row).not.toHaveAttribute('draggable', 'true')
    expect(screen.queryByRole('button', { name: /déplacer/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /nouveau paquet/i })).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/useElementCatalogue.spec.ts tests/ElementCatalogue.spec.ts`
Expected: FAIL — `catalogue.filters.package` n'existe pas (TypeScript) / `expected null to be 'f'`.

- [ ] **Step 3: Le composable** — `useElementCatalogue.ts`

Importer `import { ROOT } from '../packages/packageTree'`. Étendre `Filters` et son initialisation :

```ts
export type Filters = {
  search: string
  elementType: ElementType | ''
  layer: Layer | ''
  /** `''` everything, `'root'` the unfiled, otherwise the id of a package. */
  package: string
  includeSubpackages: boolean
}
```

`const filters = reactive<Filters>({ search: '', elementType: '', layer: '', package: '', includeSubpackages: true })`, et dans `load`, après la ligne `layer` de l'objet `query` :

```ts
      ...(filters.package === ROOT ? { root_only: true } : {}),
      ...(filters.package && filters.package !== ROOT ? { package: filters.package } : {}),
      // Sent only when it differs from the server's default, like every other filter.
      ...(filters.package && filters.package !== ROOT && !filters.includeSubpackages
        ? { include_subpackages: false }
        : {}),
```

- [ ] **Step 4: L'écran** — `ElementCatalogue.vue`

Imports :

```ts
import PackageManager from '../packages/PackageManager.vue'
import PackageTree from '../packages/PackageTree.vue'
import { ALL, ELEMENT_DRAG_TYPE, ROOT } from '../packages/packageTree'
import { usePackages } from '../packages/usePackages'
```

Dans `<script setup>`, après `const detail = useElementDetail()` :

```ts
const packages = usePackages()

/** `?package=` — `''` all, `'root'` the unfiled, else a package id (known or not). */
const packageParam = computed(() =>
  typeof route.query.package === 'string' ? route.query.package : ALL,
)
const withSubpackages = computed(() => route.query.subpackages !== '0')
/** The package named by the URL, if the tree still holds it. */
const selectedPackage = computed(() => packages.byId.value.get(packageParam.value) ?? null)

function applyUrlFilters(): void {
  catalogue.filters.package = packageParam.value
  catalogue.filters.includeSubpackages = withSubpackages.value
}

watch([packageParam, withSubpackages], async () => {
  applyUrlFilters()
  await catalogue.search()
})

/** A node of the tree was chosen: a new question, so a history entry. */
async function selectPackage(value: string): Promise<void> {
  const { package: _previous, ...rest } = route.query
  await router.push({ query: value === ALL ? rest : { ...rest, package: value } })
}

async function setSubpackages(include: boolean): Promise<void> {
  const { subpackages: _previous, ...rest } = route.query
  await router.replace({ query: include ? rest : { ...rest, subpackages: '0' } })
}

/** Filing an element changes nothing about it, only where it sits: reload both counts and rows. */
async function file(elementIds: string[], target: string): Promise<void> {
  failure.value = ''
  try {
    await packages.moveElements(elementIds, target === ROOT ? null : target)
    await catalogue.load()
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

/** The row being filed by keyboard, and where to. */
const filing = ref<ElementRead | null>(null)
const filingTarget = ref(ROOT)

async function confirmFiling(): Promise<void> {
  const element = filing.value
  if (element) {
    await file([element.id], filingTarget.value)
    filing.value = null
  }
}

function openFiling(element: ElementRead): void {
  closeEverything()
  filing.value = element
  filingTarget.value = element.package_id ?? ROOT
}

function onDragStart(event: DragEvent, element: ElementRead): void {
  event.dataTransfer?.setData(ELEMENT_DRAG_TYPE, element.id)
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
  }
}

const packagePath = computed(() => {
  const id = detail.element.value?.package_id
  return id ? (packages.byId.value.get(id)?.path ?? '') : ''
})
```

`closeEverything()` doit aussi fermer le panneau : ajouter `filing.value = null`. `onMounted` devient :

```ts
onMounted(async () => {
  applyUrlFilters()
  await Promise.all([metamodel.load(), packages.load(), catalogue.load()])
})
```

Template : envelopper le contenu existant. Remplacer `<template>\n  <section class="catalogue">` … `</section>\n</template>` par une racine `<div class="browser">` qui contient l'aside puis la `<section class="catalogue">` inchangée :

```vue
<template>
  <div class="browser">
    <aside class="browser__tree" aria-label="Rangement">
      <PackageManager :store="packages" :selected="packageParam" @select="selectPackage" />
      <PackageTree
        :packages="packages.packages.value"
        :selected="packageParam"
        :can-drop="canWrite"
        @select="selectPackage"
        @drop="(elementId, target) => file([elementId], target)"
      />
      <label v-if="selectedPackage" class="subpackages">
        <input
          type="checkbox"
          :checked="withSubpackages"
          @change="setSubpackages(($event.target as HTMLInputElement).checked)"
        />
        Inclure les sous-paquets
      </label>
    </aside>

    <section class="catalogue">
      <!-- le contenu actuel de la section (en-tête, filtres, formulaire, panneaux, table, pagination) reste tel quel, ré-indenté d'un niveau ; seuls les ajouts listés ci-dessous y entrent -->
```

(il faut ré-indenter le contenu existant d'un niveau — `eslint`/`vue` ne l'exige pas, mais le faire pour la lisibilité.) Ajouts dans le contenu existant :

- sous le `<p v-if="detail.status.value === 'loading'" …>`, passer le chemin au détail : `<ElementDetail … :package-path="packagePath" @close="closeDetail" />` ;
- avant la `<table>`, le panneau de rangement :

```vue
      <form
        v-if="filing"
        class="banner banner--confirm"
        :aria-label="`Déplacer ${filing.name}`"
        @submit.prevent="confirmFiling"
      >
        <label for="filing-target">Déplacer vers</label>
        <select id="filing-target" v-model="filingTarget">
          <option :value="ROOT">Racine (sans paquet)</option>
          <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
        <button type="submit">Ranger</button>
        <button type="button" class="secondary" @click="filing = null">Renoncer</button>
      </form>
```

- la ligne de la table devient `<tr v-for … :key="element.id" :draggable="canWrite" @dragstart="onDragStart($event, element)">` ;
- dans `.row-actions`, après le bouton « Modifier » : 

```vue
            <button
              v-if="canWrite"
              type="button"
              :aria-label="`Déplacer ${element.name}`"
              @click="openFiling(element)"
            >
              Déplacer vers…
            </button>
```

Styles à ajouter :

```css
.browser {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr);
  gap: 1.2rem;
  align-items: start;
}
.browser__tree {
  display: grid;
  gap: 0.8rem;
}
.subpackages {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.9rem;
}
@media (max-width: 48rem) {
  .browser {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 5: Relancer**

Run: `cd frontend && npm test -- --run tests/useElementCatalogue.spec.ts tests/ElementCatalogue.spec.ts && npm run typecheck && npm run lint`
Expected: PASS, les anciens tests du catalogue compris (le helper répond à `/packages` par un arbre vide). Le nom accessible « Déplacer facturation » (`aria-label`) ne contient pas le texte « Déplacer vers… » : c'est voulu (`getByRole('button', {name: /déplacer facturation/i})`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/elements frontend/tests/useElementCatalogue.spec.ts frontend/tests/ElementCatalogue.spec.ts
git commit -m "feat(spa): browse and file elements through the package tree" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Le chemin du paquet dans le détail d'un élément

**Files:**
- Modify: `frontend/src/features/elements/ElementDetail.vue` (props, template)
- Test: `frontend/tests/ElementDetail.spec.ts` (ajout)

**Interfaces:**
- Consumes: `ElementRead.package_id` (client généré) ; le chemin est calculé par l'écran appelant (`packagePath` dans `ElementCatalogue.vue`, Task 13).
- Produces: `ElementDetail` accepte la prop optionnelle `packagePath?: string` ; affiche « Paquet » : un lien `/elements?package=<id>` libellé du chemin, « Rangé dans un paquet » si l'id est connu sans chemin, « À la racine » sinon.

- [ ] **Step 1: Écrire le test qui échoue** — ajouter à `frontend/tests/ElementDetail.spec.ts` (le helper `show` accepte un 3e argument ; l'étendre : `async function show(element = anElement(), typeLabel = 'Application Component', packagePath?: string)` et `props: { element, typeLabel, packagePath }`)

```ts
  it('names the package as a link that lists what is filed there', async () => {
    await show(anElement({ package_id: 'f' }), 'Application Component', 'Finance / Budget')

    const link = within(screen.getByRole('region', { name: /détail de l'élément/i })).getByRole('link', {
      name: 'Finance / Budget',
    })
    expect(link.getAttribute('href')).toContain('package=f')
  })

  it('says an element at the root is at the root', async () => {
    await show(anElement({ package_id: null }))

    expect(screen.getByText('À la racine')).toBeInTheDocument()
  })

  it('does not invent a path it was not given', async () => {
    await show(anElement({ package_id: 'f' }))

    expect(screen.getByText('Rangé dans un paquet')).toBeInTheDocument()
  })
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/ElementDetail.spec.ts`
Expected: FAIL — `Unable to find an accessible element with the role "link" and name "Finance / Budget"`.

- [ ] **Step 3: Implémenter** — `ElementDetail.vue`

`const props = defineProps<{ element: ElementRead; typeLabel: string; packagePath?: string }>()` ; dans le template, après le champ « Aspect » :

```vue
      <div class="field field--wide">
        <dt>Paquet</dt>
        <dd v-if="element.package_id && packagePath">
          <RouterLink :to="{ path: '/elements', query: { package: element.package_id } }">
            {{ packagePath }}
          </RouterLink>
        </dd>
        <dd v-else-if="element.package_id">Rangé dans un paquet</dd>
        <dd v-else class="empty">À la racine</dd>
      </div>
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/ElementDetail.spec.ts tests/ElementCatalogue.spec.ts && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/elements/ElementDetail.vue frontend/tests/ElementDetail.spec.ts
git commit -m "feat(spa): show the package of an element in its detail" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 15: L'arbre dans la section Diagrammes

**Files:**
- Modify: `frontend/src/features/diagrams/useDiagrams.ts`
- Modify: `frontend/src/features/diagrams/DiagramsSection.vue` (script, vue « liste », styles)
- Test: `frontend/tests/DiagramsSection.spec.ts` (adaptation de `ROUTES` + ajouts)

**Interfaces:**
- Consumes: `PackageTree`, `usePackages`, `ROOT`, `ALL` (Tasks 10-11) ; `GET /diagrams?package=&include_subpackages=&root_only=`, `POST /diagrams` avec `package_id`, `PATCH /diagrams/{id}` avec `package_id` (Task 8).
- Produces: `useDiagrams()` gagne `filters: { package: string; includeSubpackages: boolean }` (réactif, lu par `load()`), `create(name: string, packageId?: string)`, `move(id: string, packageId: string | null)` ; la liste des diagrammes est filtrée par `?package=` / `?subpackages=0` (mêmes clés d'URL que le catalogue) ; chaque ligne d'un éditeur porte un `<select>` « Ranger dans… » ; le formulaire de création porte un `<select>` « Paquet ».

- [ ] **Step 1: Écrire les tests qui échouent** — dans `frontend/tests/DiagramsSection.spec.ts`, ajouter `import { aPackage }` aux imports de `./support/api`, ajouter `const PACKAGES: Route = { path: '/packages', body: [FINANCE] }` avec `const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })`, **ajouter `PACKAGES` à `ROUTES`** (`const ROUTES = [LIST, READ, PALETTE, METAMODEL_ROUTE, DETAIL, SAVE, PACKAGES]`), puis :

```ts
describe('DiagramsSection — the package tree', () => {
  it('filters the list by the package the URL names', async () => {
    const calls = stubApi(ROUTES)
    await open('?package=f&subpackages=0')

    await screen.findByText('Vue applicative')
    const query = calls.find((call) => call.url.pathname === '/diagrams')?.url.searchParams
    expect(query?.get('package')).toBe('f')
    expect(query?.get('include_subpackages')).toBe('false')
  })

  it('files a new diagram in the chosen package, and sends nothing when none is chosen', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/diagrams', status: 201, body: SUMMARY },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/nom du diagramme/i), 'Compta')
    await fireEvent.update(screen.getByLabelText(/^paquet$/i), 'f')
    await fireEvent.click(screen.getByRole('button', { name: 'Créer' }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'Compta',
        package_id: 'f',
      }),
    )
  })

  it('moves a diagram to another package from its row, the root included', async () => {
    const calls = stubApi([
      { method: 'PATCH', path: `/diagrams/${DIAGRAM_ID}`, body: SUMMARY },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/ranger vue applicative dans/i), 'root')

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ package_id: null }),
    )
  })

  it('lets a reader read the tree and filter by it, but not file anything', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    stubApi(ROUTES)
    await open()

    expect(await screen.findByRole('treeitem', { name: /Finance/ })).toBeInTheDocument()
    expect(screen.queryByLabelText(/ranger vue applicative dans/i)).toBeNull()
    expect(screen.queryByLabelText(/^paquet$/i)).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/DiagramsSection.spec.ts`
Expected: FAIL — `Unable to find a label with the text of: /^paquet$/i`.

- [ ] **Step 3: Le composable** — `useDiagrams.ts` (remplace le fichier)

```ts
// The saved diagrams: the list, the filter that produced it, and the writes.
import { reactive, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { ROOT } from '../packages/packageTree'

export type DiagramSummaryRead = components['schemas']['DiagramSummaryRead']

export function useDiagrams() {
  const diagrams = ref<DiagramSummaryRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing
  /** `package`: `''` all, `'root'` the unfiled, else a package id — the catalogue's convention. */
  const filters = reactive({ package: '', includeSubpackages: true })

  async function load(): Promise<void> {
    const query = {
      ...(filters.package === ROOT ? { root_only: true } : {}),
      ...(filters.package && filters.package !== ROOT ? { package: filters.package } : {}),
      ...(filters.package && filters.package !== ROOT && !filters.includeSubpackages
        ? { include_subpackages: false }
        : {}),
    }
    await listing.run(
      async (signal) => unwrap(await api.GET('/diagrams', { params: { query }, signal })),
      (answer) => {
        diagrams.value = answer
      },
    )
  }

  /** A refusal — a name already taken — is thrown, for the form to show. */
  async function create(name: string, packageId?: string): Promise<DiagramSummaryRead> {
    const created = unwrap(
      await api.POST('/diagrams', {
        body: { name, ...(packageId ? { package_id: packageId } : {}) },
      }),
    )
    await load()
    return created
  }

  /** `packageId: null` puts the diagram at the root; the name is left alone. */
  async function move(id: string, packageId: string | null): Promise<void> {
    unwrap(
      await api.PATCH('/diagrams/{diagram_id}', {
        params: { path: { diagram_id: id } },
        body: { package_id: packageId },
      }),
    )
    await load()
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/diagrams/{diagram_id}', { params: { path: { diagram_id: id } } }))
    await load()
  }

  return { diagrams, status, error, filters, load, create, move, remove }
}
```

- [ ] **Step 4: L'écran** — `DiagramsSection.vue`

Imports : `import PackageTree from '../packages/PackageTree.vue'`, `import { ALL, ROOT } from '../packages/packageTree'`, `import { usePackages } from '../packages/usePackages'`. Après `const detail = useElementDetail()` :

```ts
const packages = usePackages()

const packageParam = computed(() => one(route.query.package))
const withSubpackages = computed(() => route.query.subpackages !== '0')
/** The package chosen for a new diagram: the one being browsed, if it is a real package. */
const newPackage = ref('')

function applyFilters(): void {
  list.filters.package = packageParam.value
  list.filters.includeSubpackages = withSubpackages.value
}

async function selectPackage(value: string): Promise<void> {
  const { package: _previous, ...rest } = route.query
  await router.push({ query: value === ALL ? rest : { ...rest, package: value } })
}

async function moveDiagram(diagram: DiagramSummaryRead, value: string): Promise<void> {
  listFailure.value = ''
  try {
    await list.move(diagram.id, value === ROOT ? null : value)
  } catch (caught) {
    listFailure.value = messageOf(caught)
  }
}

function pathOf(packageId: string | null): string {
  return packageId ? (packages.byId.value.get(packageId)?.path ?? '') : ''
}
```

`onMounted` : `applyFilters(); await Promise.all([metamodel.load(), packages.load()])` — attention : le `watch(diagramId, …, { immediate: true })` appelle `list.load()` **avant** `onMounted` ; pour que le premier chargement porte déjà le filtre, appeler `applyFilters()` en haut de `<script setup>` (synchrone, avant le `watch`) plutôt que dans `onMounted`. Ajouter :

```ts
watch([packageParam, withSubpackages], async () => {
  applyFilters()
  if (!diagramId.value) {
    await list.load()
  }
})
```

Dans `create()` : `const created = await list.create(name, newPackage.value || undefined)` puis `newPackage.value = ''`. Template de la vue liste (`<section v-if="!diagramId" …>`) : remplacer par une grille `<div class="browser">` : `<aside>` avec `<PackageTree :packages="packages.packages.value" :selected="packageParam" :can-drop="false" @select="selectPackage" />` puis le contenu actuel de la section. Dans le formulaire de création, après le champ nom :

```vue
      <div class="field">
        <label for="diagram-package">Paquet</label>
        <select id="diagram-package" v-model="newPackage">
          <option value="">Racine (sans paquet)</option>
          <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
      </div>
```

et dans chaque `<li>` de la liste, avant le bouton « Supprimer » :

```vue
        <span v-if="pathOf(diagram.package_id)" class="hint">{{ pathOf(diagram.package_id) }}</span>
        <select
          v-if="canWrite"
          :aria-label="`Ranger ${diagram.name} dans…`"
          :value="diagram.package_id ?? ROOT"
          @change="moveDiagram(diagram, ($event.target as HTMLSelectElement).value)"
        >
          <option :value="ROOT">Racine (sans paquet)</option>
          <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
```

Styles : réutiliser `.browser` (copier le bloc de `ElementCatalogue.vue`, Task 13) dans le `<style scoped>` de ce composant.

- [ ] **Step 5: Relancer**

Run: `cd frontend && npm test -- --run tests/DiagramsSection.spec.ts tests/useDiagram.spec.ts && npm run typecheck && npm run lint`
Expected: PASS ; les anciens tests de l'éditeur (`?diagram=`) passent car `/packages` est dans `ROUTES`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/diagrams frontend/tests/DiagramsSection.spec.ts
git commit -m "feat(spa): file diagrams in the package tree" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Clôture du lot 2

**Files:** aucun nouveau fichier de code ; pas d'ADR (la décision est celle de l'ADR des paquets, Task 9).

- [ ] **Step 1: La porte qualité complète**

Run: `make check`
Expected: vert (`test-fe` inclut les neuf specs ajoutées ou étendues ; `openapi-check` confirme que le client committé à la Task 9 est toujours celui du schéma).

- [ ] **Step 2: Parcours manuel dans un navigateur** (`make run`, puis `http://localhost:5173/elements`)

Vérifier, connecté en éditeur : créer un paquet « Finance », un sous-paquet « Budget » ; glisser une ligne du catalogue sur « Budget » (le compteur passe à 1) ; « Déplacer vers… » une autre ligne à la « Racine » ; décocher « Inclure les sous-paquets » sur « Finance » (l'URL gagne `?subpackages=0`) ; recharger la page (la sélection et les filtres survivent) ; tenter de supprimer « Finance » (refus lisible) puis le supprimer en choisissant « Racine » ; coller `/elements?package=00000000-0000-0000-0000-000000000000` (liste vide, pas d'erreur). Connecté en lecteur (ou `ea-editor` retiré) : l'arbre se lit mais aucune commande de rangement n'apparaît. Noter tout écart dans la PR plutôt que de corriger à la volée.

- [ ] **Step 3: Commit si le parcours a imposé un correctif**, sinon rien à committer.

---

# LOT 3 — `ElementQuery` et `POST /elements/search`

### Task 17: L'objet de domaine `ElementQuery`

**Files:**
- Create: `backend/src/ea/domain/query.py`
- Modify: `backend/src/ea/domain/errors.py` (ajout de `InvalidQueryError`), `backend/src/ea/domain/ports.py` (`ElementFilter` : champs `properties`, `relations`, `order` et `from_query` ; imports)
- Test: `backend/tests/unit/test_element_query.py`

**Interfaces:**
- Consumes: `ElementType`, `Layer`, `RelationshipType` (`ea.domain.archimate`) ; `PROPERTY_KEY` (Task 2) ; `ElementFilter` (Task 2).
- Produces (`ea.domain.query`) :
  - `QUERY_VERSION = 1`, `MAX_PROPERTY_TESTS = 10`, `MAX_RELATION_TESTS = 5`, `MAX_TEXT = 200`, `MAX_PROPERTY_VALUE = 200`, `MAX_STORED_QUERY_BYTES = 16 * 1024`.
  - `PropertyOp(StrEnum)`: `EQ="eq"`, `CONTAINS="contains"`, `EXISTS="exists"`, `ABSENT="absent"` ; `Direction(StrEnum)`: `OUT="out"`, `IN="in"`, `ANY="any"` ; `QueryOrder(StrEnum)`: `NAME="name"`, `CREATED_AT="created_at"`, `UPDATED_AT="updated_at"`.
  - `PropertyTest(key: str, op: PropertyOp, value: str | None = None)` ; `RelationTest(relationship_type: RelationshipType, direction: Direction = Direction.ANY, present: bool = True, other_type: ElementType | None = None)`.
  - `ElementQuery(element_types=(), layers=(), text=None, package_id=None, include_subpackages=True, root_only=False, properties=(), relations=(), order=QueryOrder.NAME, limit=50, offset=0)` (frozen, validé à la construction : `ValueError`).
  - `QueryResult(elements: tuple[Element, ...], total: int)`.
  - `to_json(query: ElementQuery) -> dict[str, Any]`, `from_json(data: Mapping[str, Any]) -> ElementQuery` (lève `InvalidQueryError`), `check_storable(query: ElementQuery) -> None` (lève `InvalidQueryError` au-delà de `MAX_STORED_QUERY_BYTES`).
  - `InvalidQueryError(DomainError)` ; `ElementFilter.from_query(query: ElementQuery) -> ElementFilter`, `ElementFilter.properties`, `.relations`, `.order`.

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/unit/test_element_query.py`

```python
"""The structured filter: its bounds, its normalisation, and its stored form."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain import query as query_module
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import Layer
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import InvalidQueryError
from ea.domain.ports import ElementFilter
from ea.domain.query import (
    MAX_PROPERTY_TESTS,
    MAX_RELATION_TESTS,
    MAX_TEXT,
    QUERY_VERSION,
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
    check_storable,
    from_json,
    to_json,
)


def a_full_query() -> ElementQuery:
    return ElementQuery(
        element_types=(E.APPLICATION_COMPONENT, E.NODE),
        layers=(Layer.APPLICATION,),
        text="factu",
        package_id=uuid4(),
        include_subpackages=False,
        properties=(
            PropertyTest("owner", PropertyOp.EQ, "finance"),
            PropertyTest("note", PropertyOp.CONTAINS, "urgent"),
            PropertyTest("cidr", PropertyOp.EXISTS),
            PropertyTest("retired", PropertyOp.ABSENT),
        ),
        relations=(
            RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_PROCESS),
            RelationTest(R.ASSOCIATION, Direction.ANY, False),
        ),
        order=QueryOrder.UPDATED_AT,
        limit=25,
        offset=50,
    )


class TestNormalisation:
    def test_the_defaults_filter_nothing(self) -> None:
        query = ElementQuery()

        assert (query.element_types, query.layers, query.text, query.package_id) == ((), (), None, None)
        assert (query.include_subpackages, query.root_only) == (True, False)
        assert (query.order, query.limit, query.offset) == (QueryOrder.NAME, 50, 0)

    def test_the_text_is_trimmed_and_blank_means_none(self) -> None:
        assert ElementQuery(text="  billing ").text == "billing"
        assert ElementQuery(text="   ").text is None

    def test_a_repeated_type_or_layer_is_kept_once_in_order(self) -> None:
        query = ElementQuery(
            element_types=(E.NODE, E.APPLICATION_COMPONENT, E.NODE),
            layers=(Layer.BUSINESS, Layer.BUSINESS),
        )

        assert query.element_types == (E.NODE, E.APPLICATION_COMPONENT)
        assert query.layers == (Layer.BUSINESS,)


class TestBounds:
    def test_the_text_is_bounded(self) -> None:
        assert ElementQuery(text="x" * MAX_TEXT).text
        with pytest.raises(ValueError, match="text"):
            ElementQuery(text="x" * (MAX_TEXT + 1))

    def test_the_number_of_property_tests_is_bounded(self) -> None:
        tests = tuple(PropertyTest(f"k{i}", PropertyOp.EXISTS) for i in range(MAX_PROPERTY_TESTS))
        assert len(ElementQuery(properties=tests).properties) == MAX_PROPERTY_TESTS
        with pytest.raises(ValueError, match="property"):
            ElementQuery(properties=(*tests, PropertyTest("one_more", PropertyOp.EXISTS)))

    def test_the_number_of_relation_tests_is_bounded(self) -> None:
        tests = tuple(RelationTest(R.ASSOCIATION) for _ in range(MAX_RELATION_TESTS))
        assert len(ElementQuery(relations=tests).relations) == MAX_RELATION_TESTS
        with pytest.raises(ValueError, match="relation"):
            ElementQuery(relations=(*tests, RelationTest(R.ASSOCIATION)))

    @pytest.mark.parametrize("limit", [0, 201, -1])
    def test_a_page_is_between_1_and_200(self, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            ElementQuery(limit=limit)

    def test_a_negative_offset_is_refused(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            ElementQuery(offset=-1)

    def test_the_root_and_a_package_at_once_contradict_each_other(self) -> None:
        with pytest.raises(ValueError, match="root_only"):
            ElementQuery(root_only=True, package_id=uuid4())


class TestPropertyTests:
    @pytest.mark.parametrize("key", ["", "1abc", "a-b", "a b", "é", "x" * 64])
    def test_a_key_follows_the_rule_of_an_element_attribute(self, key: str) -> None:
        with pytest.raises(ValueError, match="plain identifier"):
            PropertyTest(key, PropertyOp.EXISTS)

    @pytest.mark.parametrize("op", [PropertyOp.EQ, PropertyOp.CONTAINS])
    def test_eq_and_contains_need_a_value(self, op: PropertyOp) -> None:
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", op)

    def test_contains_refuses_an_empty_fragment_but_eq_allows_an_empty_value(self) -> None:
        PropertyTest("owner", PropertyOp.EQ, "")
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", PropertyOp.CONTAINS, "")

    @pytest.mark.parametrize("op", [PropertyOp.EXISTS, PropertyOp.ABSENT])
    def test_exists_and_absent_take_no_value(self, op: PropertyOp) -> None:
        with pytest.raises(ValueError, match="no value"):
            PropertyTest("owner", op, "x")

    def test_a_value_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", PropertyOp.EQ, "x" * 201)

    def test_a_value_with_sql_in_it_is_just_a_value(self) -> None:
        assert PropertyTest("owner", PropertyOp.EQ, "x'; DROP TABLE elements; --").value


class TestRelationTests:
    def test_a_relation_no_filtered_type_can_have_is_accepted_not_refused(self) -> None:
        """The metamodel stays the only judge of what is possible: the answer is empty, not an error."""
        query = ElementQuery(
            element_types=(E.DATA_OBJECT,),
            relations=(RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_ACTOR),),
        )

        assert len(query.relations) == 1


class TestTheStoredForm:
    def test_it_round_trips_exactly(self) -> None:
        query = a_full_query()

        assert from_json(to_json(query)) == query

    def test_the_stored_form_is_versioned_and_plain_json(self) -> None:
        stored = to_json(a_full_query())

        assert stored["version"] == QUERY_VERSION
        assert stored["order"] == "updated_at"
        assert stored["relations"][0] == {
            "relationship_type": "serving",
            "direction": "out",
            "present": True,
            "other_type": "business_process",
        }

    def test_an_unknown_version_is_refused(self) -> None:
        stored = {**to_json(a_full_query()), "version": 2}

        with pytest.raises(InvalidQueryError, match="version"):
            from_json(stored)

    def test_a_value_the_enumerations_no_longer_know_is_refused(self) -> None:
        stored = to_json(a_full_query())
        stored["element_types"] = ["application_component", "flux_capacitor"]

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    @pytest.mark.parametrize("garbage", ["oops", None, 3, [1, 2]])
    def test_something_that_is_not_an_object_is_refused(self, garbage: object) -> None:
        with pytest.raises(InvalidQueryError, match="object"):
            from_json(garbage)

    def test_a_missing_key_is_refused_not_a_key_error(self) -> None:
        stored = to_json(a_full_query())
        del stored["relations"]

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    def test_a_stored_value_that_breaks_a_bound_is_refused(self) -> None:
        stored = to_json(a_full_query())
        stored["limit"] = 9999

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    def test_a_stored_query_larger_than_the_ceiling_cannot_be_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        check_storable(a_full_query())
        monkeypatch.setattr(query_module, "MAX_STORED_QUERY_BYTES", 100)

        with pytest.raises(InvalidQueryError, match="bytes"):
            check_storable(a_full_query())


class TestTheFilterItBecomes:
    def test_every_criterion_reaches_the_repository_filter(self) -> None:
        query = a_full_query()

        criteria = ElementFilter.from_query(query)

        assert criteria.element_types == query.element_types
        assert criteria.search == "factu"
        assert criteria.package_id == query.package_id
        assert criteria.include_subpackages is False
        assert criteria.properties == query.properties
        assert criteria.relations == query.relations
        assert (criteria.order, criteria.limit, criteria.offset) == (QueryOrder.UPDATED_AT, 25, 50)
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_element_query.py -x`
Expected: FAIL — `ImportError: cannot import name 'query' from 'ea.domain'` / `ModuleNotFoundError: ea.domain.query`.

- [ ] **Step 3: L'erreur** — fin de `backend/src/ea/domain/errors.py`

```python


class InvalidQueryError(DomainError):
    """A stored query that cannot be read back: unknown version, a value the
    enumerations no longer know, or a size beyond what is kept.

    A query is data: it was valid when it was written and the code moved since.
    It is refused with a message, never trusted and never a 500.
    """
```

- [ ] **Step 4: Le module** — `backend/src/ea/domain/query.py`

```python
"""The structured filter of the catalogue — one object for every door.

`GET /elements`, `POST /elements/search`, the saved queries and the MCP tools
all build an `ElementQuery`; the repository compiles it into SQL in one place
(`architecture_store._matching`). It is a tree of data, never a string of SQL:
the keys and values of a property test are bound parameters downstream.

It restates no rule of ArchiMate. The element and relationship types it names
are the metamodel's enumerations, and a relationship test that no filtered type
can ever satisfy is *accepted* — the answer is empty, and the metamodel stays
the only judge of what is possible.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.errors import InvalidQueryError
from ea.domain.model import PROPERTY_KEY, Element

#: The version written into a stored query. A reader that meets another one
#: refuses it rather than guess what the fields meant.
QUERY_VERSION: Final = 1
MAX_PROPERTY_TESTS: Final = 10
MAX_RELATION_TESTS: Final = 5
MAX_TEXT: Final = 200
#: Properties are text (spec §11, Q7): a bound on the value tested, like the one on `text`.
MAX_PROPERTY_VALUE: Final = 200
MAX_STORED_QUERY_BYTES: Final = 16 * 1024
MAX_PAGE: Final = 200


class PropertyOp(StrEnum):
    EQ = "eq"
    CONTAINS = "contains"
    EXISTS = "exists"
    ABSENT = "absent"


class Direction(StrEnum):
    OUT = "out"
    IN = "in"
    ANY = "any"


class QueryOrder(StrEnum):
    NAME = "name"
    #: Newest first.
    CREATED_AT = "created_at"
    #: Most recently changed first.
    UPDATED_AT = "updated_at"


@dataclass(frozen=True, slots=True)
class PropertyTest:
    """One test on an element's user-defined attributes."""

    key: str
    op: PropertyOp
    value: str | None = None

    def __post_init__(self) -> None:
        if not PROPERTY_KEY.match(self.key):
            msg = (
                f"property name {self.key!r} is not a plain identifier "
                "(letters, digits and underscores, starting with a letter)"
            )
            raise ValueError(msg)
        if self.op in (PropertyOp.EQ, PropertyOp.CONTAINS):
            if self.value is None or (self.op is PropertyOp.CONTAINS and self.value == ""):
                msg = f"the {self.op.value} test on {self.key!r} needs a value"
                raise ValueError(msg)
            if len(self.value) > MAX_PROPERTY_VALUE:
                msg = f"a property value is at most {MAX_PROPERTY_VALUE} characters"
                raise ValueError(msg)
        elif self.value is not None:
            msg = f"the {self.op.value} test on {self.key!r} takes no value"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RelationTest:
    """Whether an element has (or has no) relationship of one type, optionally to one type."""

    relationship_type: RelationshipType
    direction: Direction = Direction.ANY
    present: bool = True
    other_type: ElementType | None = None


@dataclass(frozen=True, slots=True)
class ElementQuery:
    """Every criterion is combined with AND."""

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    text: str | None = None
    package_id: UUID | None = None
    include_subpackages: bool = True
    root_only: bool = False
    properties: tuple[PropertyTest, ...] = ()
    relations: tuple[RelationTest, ...] = ()
    order: QueryOrder = QueryOrder.NAME
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        text = (self.text or "").strip()
        if len(text) > MAX_TEXT:
            msg = f"text is at most {MAX_TEXT} characters"
            raise ValueError(msg)
        # Frozen, so normalised through `object.__setattr__`: the stored form and
        # the compared form are then the same whoever built the query.
        object.__setattr__(self, "text", text or None)
        object.__setattr__(self, "element_types", tuple(dict.fromkeys(self.element_types)))
        object.__setattr__(self, "layers", tuple(dict.fromkeys(self.layers)))
        if len(self.properties) > MAX_PROPERTY_TESTS:
            msg = f"a query holds at most {MAX_PROPERTY_TESTS} property tests"
            raise ValueError(msg)
        if len(self.relations) > MAX_RELATION_TESTS:
            msg = f"a query holds at most {MAX_RELATION_TESTS} relation tests"
            raise ValueError(msg)
        if self.root_only and self.package_id is not None:
            msg = "root_only and package_id contradict each other: an element is either filed or not"
            raise ValueError(msg)
        if not 1 <= self.limit <= MAX_PAGE:
            msg = f"limit is between 1 and {MAX_PAGE}"
            raise ValueError(msg)
        if self.offset < 0:
            msg = "offset cannot be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class QueryResult:
    """One page of what a query matches, and how many it matches in all."""

    elements: tuple[Element, ...]
    total: int


def to_json(query: ElementQuery) -> dict[str, Any]:
    """The stored form: plain JSON, versioned, the very shape the API's schema dumps."""
    return {
        "version": QUERY_VERSION,
        "element_types": [element_type.value for element_type in query.element_types],
        "layers": [layer.value for layer in query.layers],
        "text": query.text,
        "package_id": str(query.package_id) if query.package_id is not None else None,
        "include_subpackages": query.include_subpackages,
        "root_only": query.root_only,
        "properties": [
            {"key": test.key, "op": test.op.value, "value": test.value}
            for test in query.properties
        ],
        "relations": [
            {
                "relationship_type": test.relationship_type.value,
                "direction": test.direction.value,
                "present": test.present,
                "other_type": test.other_type.value if test.other_type is not None else None,
            }
            for test in query.relations
        ],
        "order": query.order.value,
        "limit": query.limit,
        "offset": query.offset,
    }


def from_json(data: object) -> ElementQuery:
    """Read a stored query back, refusing what it can no longer vouch for.

    `object` and not `Mapping`: what comes out of a `jsonb` column is whatever
    was once written there, a string or `null` included.
    """
    if not isinstance(data, Mapping):
        msg = "the stored query is not an object"
        raise InvalidQueryError(msg)
    if data.get("version") != QUERY_VERSION:
        msg = f"unsupported query version: {data.get('version')!r}"
        raise InvalidQueryError(msg)
    try:
        package = data["package_id"]
        return ElementQuery(
            element_types=tuple(ElementType(value) for value in data["element_types"]),
            layers=tuple(Layer(value) for value in data["layers"]),
            text=data["text"],
            package_id=UUID(package) if package else None,
            include_subpackages=bool(data["include_subpackages"]),
            root_only=bool(data["root_only"]),
            properties=tuple(
                PropertyTest(item["key"], PropertyOp(item["op"]), item["value"])
                for item in data["properties"]
            ),
            relations=tuple(
                RelationTest(
                    RelationshipType(item["relationship_type"]),
                    Direction(item["direction"]),
                    bool(item["present"]),
                    ElementType(item["other_type"]) if item["other_type"] else None,
                )
                for item in data["relations"]
            ),
            order=QueryOrder(data["order"]),
            limit=int(data["limit"]),
            offset=int(data["offset"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        msg = f"the stored query cannot be read: {error}"
        raise InvalidQueryError(msg) from error


def check_storable(query: ElementQuery) -> None:
    """A filter is not a denial of service: the JSON kept is bounded in bytes too."""
    size = len(json.dumps(to_json(query), ensure_ascii=False).encode())
    if size > MAX_STORED_QUERY_BYTES:
        msg = f"a stored query is at most {MAX_STORED_QUERY_BYTES} bytes, this one is {size}"
        raise InvalidQueryError(msg)
```

Dans `domain/ports.py` : importer `from ea.domain.query import ElementQuery, PropertyTest, QueryOrder, RelationTest` et étendre `ElementFilter` :

```python
@dataclass(frozen=True, slots=True)
class ElementFilter:
    """The ways the catalogue can be narrowed. All criteria are combined with AND.

    What a repository compiles. `ElementQuery` is the validated form every
    adapter builds; this narrow struct is what it becomes, and the one place
    the SQL reads it is `architecture_store._matching`.
    """

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    package_id: UUID | None = None
    include_subpackages: bool = True
    root_only: bool = False
    properties: tuple[PropertyTest, ...] = ()
    relations: tuple[RelationTest, ...] = ()
    order: QueryOrder = QueryOrder.NAME
    limit: int = 50
    offset: int = 0

    @classmethod
    def from_query(cls, query: ElementQuery) -> ElementFilter:
        return cls(
            element_types=query.element_types,
            layers=query.layers,
            search=query.text,
            package_id=query.package_id,
            include_subpackages=query.include_subpackages,
            root_only=query.root_only,
            properties=query.properties,
            relations=query.relations,
            order=query.order,
            limit=query.limit,
            offset=query.offset,
        )
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_element_query.py -x && uv run mypy src && uv run ruff check src tests`
Expected: PASS. (`test_error_statuses.py` reste rouge pour `InvalidQueryError` jusqu'à la Task 19 — c'est attendu.)

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain backend/tests/unit/test_element_query.py
git commit -m "feat(domain): add the structured element query" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Compilation SQL de la requête (`_matching` étendu) et tri

**Files:**
- Modify: `backend/src/ea/repositories/architecture_store.py` (imports ; `_matching` ≈ l.197-207 ; `list_elements` ≈ l.338-347 ; nouvelles fonctions `_property_clause`, `_relation_clause`, `_ordering`)
- Test: `backend/tests/integration/test_element_search.py`

**Interfaces:**
- Consumes: `ElementFilter.properties/relations/order` (Task 17) ; `_has`, `_text_of` (existants dans `architecture_store.py`).
- Produces: `_matching(criteria)` honore les tests de propriétés et de relations ; `list_elements` trie selon `criteria.order` (`name` croissant ; `created_at` et `updated_at` du plus récent au plus ancien ; `id` en départage).

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/integration/test_element_search.py`

```python
"""The structured search against a real PostgreSQL: the SQL is what is under test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.domain.packages import Package
from ea.domain.ports import ElementFilter
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.package_store import PostgresPackageRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, engine: AsyncEngine) -> None:
        sessions = create_session_factory(engine)
        self.graph = PostgresArchitectureRepository(sessions)
        self.packages = PostgresPackageRepository(sessions)

    async def element(
        self,
        name: str,
        element_type: E = E.APPLICATION_COMPONENT,
        properties: dict[str, str] | None = None,
        at: datetime = NOW,
    ) -> Element:
        return await self.graph.add_element(
            Element.create(element_type=element_type, name=name, now=at, properties=properties)
        )

    async def link(self, kind: R, source: Element, target: Element) -> None:
        await self.graph.add_relationship(Relationship.between(kind, source, target, now=NOW))

    async def names(self, **query: Any) -> list[str]:
        criteria = ElementFilter.from_query(ElementQuery(**query))
        found = await self.graph.list_elements(criteria)
        return [element.name for element in found]

    async def total(self, **query: Any) -> int:
        return await self.graph.count_elements(ElementFilter.from_query(ElementQuery(**query)))


@pytest.fixture
def world(engine_at_head: AsyncEngine) -> World:
    return World(engine_at_head)


def only(*tests: PropertyTest) -> dict[str, Any]:
    return {"properties": tests}


class TestPropertyTests:
    async def seed(self, world: World) -> None:
        await world.element("billing", properties={"owner": "Finance", "note": "100% urgent_fix"})
        await world.element("ledger", properties={"owner": "Compta"})
        await world.element("crm")

    async def test_eq_is_exact(self, world: World) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "Finance"))) == ["billing"]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "finance"))) == []

    async def test_contains_ignores_case_and_treats_wildcards_as_characters(
        self, world: World
    ) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.CONTAINS, "FINAN"))) == ["billing"]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "100%"))) == ["billing"]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "_fix"))) == ["billing"]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "%"))) == ["billing"]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.CONTAINS, "_"))) == []

    async def test_exists_and_absent_split_the_catalogue(self, world: World) -> None:
        await self.seed(world)

        assert sorted(await world.names(**only(PropertyTest("owner", PropertyOp.EXISTS)))) == [
            "billing",
            "ledger",
        ]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.ABSENT))) == ["crm"]

    async def test_two_tests_are_combined_with_and(self, world: World) -> None:
        await self.seed(world)

        assert await world.names(
            properties=(
                PropertyTest("owner", PropertyOp.EXISTS),
                PropertyTest("note", PropertyOp.ABSENT),
            )
        ) == ["ledger"]

    async def test_a_value_carrying_sql_matches_nothing_and_breaks_nothing(
        self, world: World
    ) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "x' OR '1'='1"))) == []
        assert await world.total() == 3


class TestRelationTests:
    async def seed(self, world: World) -> None:
        self.service = await world.element("api", E.APPLICATION_SERVICE)
        self.process = await world.element("order to cash", E.BUSINESS_PROCESS)
        self.component = await world.element("billing", E.APPLICATION_COMPONENT)
        self.node = await world.element("db-01", E.NODE)
        self.lonely = await world.element("lonely", E.APPLICATION_COMPONENT)
        await world.link(R.SERVING, self.service, self.process)
        await world.link(R.ASSOCIATION, self.component, self.node)

    async def test_out_in_and_any(self, world: World) -> None:
        await self.seed(world)

        def serving(direction: Direction, **rest: Any) -> dict[str, Any]:
            return {"relations": (RelationTest(R.SERVING, direction, **rest),)}

        assert await world.names(**serving(Direction.OUT)) == ["api"]
        assert await world.names(**serving(Direction.IN)) == ["order to cash"]
        assert sorted(await world.names(**serving(Direction.ANY))) == ["api", "order to cash"]

    async def test_other_type_narrows_the_far_end(self, world: World) -> None:
        await self.seed(world)

        def to(other: E) -> dict[str, Any]:
            return {"relations": (RelationTest(R.SERVING, Direction.OUT, True, other),)}

        assert await world.names(**to(E.BUSINESS_PROCESS)) == ["api"]
        assert await world.names(**to(E.NODE)) == []

    async def test_absent_finds_what_has_no_such_link(self, world: World) -> None:
        await self.seed(world)

        no_association = RelationTest(R.ASSOCIATION, Direction.ANY, present=False)
        assert sorted(await world.names(relations=(no_association,))) == [
            "api",
            "lonely",
            "order to cash",
        ]

    async def test_absent_with_a_type_ignores_links_to_other_types(self, world: World) -> None:
        await self.seed(world)

        # `billing` is associated with a node, so it has no association with a process.
        no_association_to_a_process = RelationTest(
            R.ASSOCIATION, Direction.OUT, present=False, other_type=E.BUSINESS_PROCESS
        )
        found = await world.names(
            element_types=(E.APPLICATION_COMPONENT,), relations=(no_association_to_a_process,)
        )

        assert sorted(found) == ["billing", "lonely"]

    async def test_a_relation_no_type_can_have_is_an_empty_answer_not_an_error(
        self, world: World
    ) -> None:
        await self.seed(world)

        impossible = RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_ACTOR)
        assert await world.names(element_types=(E.DATA_OBJECT,), relations=(impossible,)) == []

    async def test_relations_combine_with_types_and_properties(self, world: World) -> None:
        await self.seed(world)
        await world.element("tagged service", E.APPLICATION_SERVICE, {"owner": "x"})

        found = await world.names(
            element_types=(E.APPLICATION_SERVICE,),
            properties=(PropertyTest("owner", PropertyOp.ABSENT),),
            relations=(RelationTest(R.SERVING, Direction.OUT),),
        )

        assert found == ["api"]


class TestCombinations:
    async def test_a_package_subtree_a_type_and_a_property_together(self, world: World) -> None:
        finance = await world.packages.add(Package.create(name="Finance", now=NOW))
        budget = await world.packages.add(
            Package.create(name="Budget", parent_id=finance.id, now=NOW)
        )
        inside = await world.element("in", properties={"owner": "x"})
        also_inside = await world.element("also in", E.NODE, {"owner": "x"})
        outside = await world.element("out", properties={"owner": "x"})
        await world.packages.move_elements([inside.id, also_inside.id], budget.id)
        assert outside.package_id is None

        found = await world.names(
            package_id=finance.id,
            element_types=(E.APPLICATION_COMPONENT,),
            properties=(PropertyTest("owner", PropertyOp.EQ, "x"),),
        )

        assert found == ["in"]

    async def test_the_text_still_matches_a_name_fragment_literally(self, world: World) -> None:
        await world.element("db_01")
        await world.element("dbx01")

        assert await world.names(text="db_") == ["db_01"]


class TestPagingAndOrder:
    async def test_a_page_and_the_total_of_the_whole(self, world: World) -> None:
        for name in ("a", "b", "c", "d", "e"):
            await world.element(name)

        assert await world.names(limit=2, offset=2) == ["c", "d"]
        assert await world.total(limit=2, offset=2) == 5

    async def test_by_date_the_newest_comes_first_and_by_name_the_alphabet(
        self, world: World
    ) -> None:
        await world.element("zulu", at=NOW)
        await world.element("alpha", at=NOW + timedelta(days=1))

        assert await world.names(order=QueryOrder.NAME) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.CREATED_AT) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.UPDATED_AT) == ["alpha", "zulu"]

    async def test_created_at_is_not_the_name_order(self, world: World) -> None:
        await world.element("alpha", at=NOW)
        await world.element("zulu", at=NOW + timedelta(days=1))

        assert await world.names(order=QueryOrder.NAME) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.CREATED_AT) == ["zulu", "alpha"]

    async def test_ids_break_ties_so_a_page_never_repeats(self, world: World) -> None:
        for index in range(4):
            await world.element(f"n{index}", at=NOW)

        first = await world.names(order=QueryOrder.CREATED_AT, limit=2, offset=0)
        second = await world.names(order=QueryOrder.CREATED_AT, limit=2, offset=2)

        assert len({*first, *second}) == 4
```


- [ ] **Step 2: Lancer**

Run: `make pg-up && cd backend && uv run pytest tests/integration/test_element_search.py -x`
Expected: FAIL — les tests de propriétés renvoient tous les éléments (`assert ['billing', 'crm', 'ledger'] == ['billing']`) : `_matching` ignore encore `properties`.

- [ ] **Step 3: Implémenter** — `backend/src/ea/repositories/architecture_store.py`

Imports : `from sqlalchemy import exists` (dans la liste existante) et `from sqlalchemy.orm import aliased` ; `from ea.domain.query import Direction, PropertyOp, PropertyTest, QueryOrder, RelationTest`. Ajouter avant `_matching` :

```python
def _property_clause(test: PropertyTest) -> ColumnElement[bool]:
    """One test on `properties`. The key and the value are bound parameters, never text."""
    if test.op is PropertyOp.EXISTS:
        return _has(test.key)
    if test.op is PropertyOp.ABSENT:
        return not_(_has(test.key))
    value = test.value or ""
    if test.op is PropertyOp.EQ:
        return _text_of(test.key) == value
    # `strpos` like the name search: `%` and `_` are characters, not wildcards.
    return func.strpos(func.lower(_text_of(test.key)), func.lower(value)) > 0


def _relation_clause(test: RelationTest) -> ColumnElement[bool]:
    """A correlated `EXISTS` on `relationships`: the element has — or has not — such a link."""
    link = aliased(RelationshipRecord)
    far = test.other_type.value if test.other_type is not None else None
    leaving = and_(
        link.source_id == ElementRecord.id,
        *([link.target_type == far] if far is not None else []),
    )
    arriving = and_(
        link.target_id == ElementRecord.id,
        *([link.source_type == far] if far is not None else []),
    )
    side = {
        Direction.OUT: leaving,
        Direction.IN: arriving,
        Direction.ANY: or_(leaving, arriving),
    }[test.direction]
    found = exists().where(link.relationship_type == test.relationship_type.value, side)
    return found if test.present else not_(found)


def _ordering(order: QueryOrder) -> tuple[ColumnElement[Any], ...]:
    """The id breaks every tie, so two pages of one query never share a row."""
    if order is QueryOrder.CREATED_AT:
        return (ElementRecord.created_at.desc(), ElementRecord.id)
    if order is QueryOrder.UPDATED_AT:
        return (ElementRecord.updated_at.desc(), ElementRecord.id)
    return (ElementRecord.name, ElementRecord.id)
```

Dans `_matching`, avant `return clauses`, après les clauses de paquet de la Task 4 :

```python
    clauses.extend(_property_clause(test) for test in criteria.properties)
    clauses.extend(_relation_clause(test) for test in criteria.relations)
```

Dans `list_elements`, remplacer `.order_by(ElementRecord.name, ElementRecord.id)` par `.order_by(*_ordering(criteria.order))`.

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/integration/test_element_search.py tests/integration/test_package_store.py tests/integration/test_archimate_graph.py -x && uv run mypy src && uv run ruff check src tests`
Expected: PASS. Les tests existants de `test_archimate_graph.py` (`ElementFilter(search="db_")`, etc.) prouvent que le comportement historique est intact.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/architecture_store.py backend/tests/integration/test_element_search.py
git commit -m "feat(store): compile property and relation tests and the order of a query" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 19: `ElementQuerySchema` et `POST /elements/search`

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (imports ; nouvelle section « Structured search » en fin de fichier)
- Modify: `backend/src/ea/api/errors.py` (`InvalidQueryError` → `422 invalid_query`)
- Create: `backend/src/ea/api/queries.py`
- Modify: `backend/src/ea/main.py` (routeur)
- Test: `backend/tests/unit/test_query_schema.py`, `backend/tests/e2e/test_query_api.py`

**Interfaces:**
- Consumes: `ElementQuery`, `PropertyTest`, `RelationTest`, `PropertyOp`, `Direction`, `QueryOrder`, `QUERY_VERSION`, `MAX_PROPERTY_TESTS`, `MAX_RELATION_TESTS`, `MAX_PROPERTY_VALUE`, `to_json` (Task 17) ; `Search`, `Limit`, `Offset`, `ElementPage` (existants) ; `Architecture` (dépendance).
- Produces:
  - `PropertyKey`, `PropertyValue` (types contraints) ; `PropertyTestSchema{key, op, value}` ; `RelationTestSchema{relationship_type, direction, present, other_type}` ; `ElementQuerySchema{version: Literal[1], element_types, layers, text, package_id, include_subpackages, root_only, properties, relations, order, limit, offset}` — **tous requis, aucun défaut** — avec `ElementQuerySchema.to_domain() -> ElementQuery` et `ElementQuerySchema.of(query: ElementQuery) -> ElementQuerySchema`.
  - `POST /elements/search` → `ElementPage` ; `422` pour un corps invalide ; `422 invalid_input` pour une contradiction de domaine.

- [ ] **Step 1: Écrire les tests qui échouent**

`backend/tests/unit/test_query_schema.py` :

```python
"""The strict schema of a query: the one door every adapter and every stored query uses."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ea.api.schemas import ElementQuerySchema
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
    to_json,
)


def a_query() -> ElementQuery:
    return ElementQuery(
        element_types=(E.NODE,),
        text="db",
        package_id=uuid4(),
        include_subpackages=False,
        properties=(PropertyTest("owner", PropertyOp.EQ, "x"), PropertyTest("cidr", PropertyOp.EXISTS)),
        relations=(RelationTest(R.SERVING, Direction.IN, False, E.BUSINESS_PROCESS),),
        order=QueryOrder.CREATED_AT,
        limit=10,
        offset=20,
    )


def payload(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


class TestTheRoundTrip:
    def test_domain_schema_domain(self) -> None:
        query = a_query()

        assert ElementQuerySchema.of(query).to_domain() == query

    def test_the_schema_dumps_exactly_the_stored_form(self) -> None:
        query = a_query()

        assert ElementQuerySchema.of(query).model_dump(mode="json") == to_json(query)

    def test_the_stored_form_is_accepted_back_by_the_schema(self) -> None:
        query = a_query()

        assert ElementQuerySchema.model_validate(to_json(query)).to_domain() == query


class TestWhatItRefuses:
    def test_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ElementQuerySchema.model_validate(payload(colour="red"))

    def test_a_missing_field_there_are_no_defaults(self) -> None:
        body = payload()
        del body["order"]

        with pytest.raises(ValidationError, match="order"):
            ElementQuerySchema.model_validate(body)

    def test_a_version_that_is_not_1(self) -> None:
        with pytest.raises(ValidationError, match="version"):
            ElementQuerySchema.model_validate(payload(version=2))

    def test_an_operator_outside_the_four(self) -> None:
        with pytest.raises(ValidationError, match="op"):
            ElementQuerySchema.model_validate(
                payload(properties=[{"key": "a", "op": "gt", "value": "1"}])
            )

    def test_a_property_name_that_is_not_an_identifier(self) -> None:
        with pytest.raises(ValidationError, match="key"):
            ElementQuerySchema.model_validate(
                payload(properties=[{"key": "a-b", "op": "exists", "value": None}])
            )

    def test_more_than_ten_property_tests_or_five_relation_tests(self) -> None:
        many = [{"key": f"k{i}", "op": "exists", "value": None} for i in range(11)]
        with pytest.raises(ValidationError, match="properties"):
            ElementQuerySchema.model_validate(payload(properties=many))
        relations = [
            {"relationship_type": "serving", "direction": "any", "present": True, "other_type": None}
        ] * 6
        with pytest.raises(ValidationError, match="relations"):
            ElementQuerySchema.model_validate(payload(relations=relations))

    def test_a_type_the_metamodel_does_not_have(self) -> None:
        with pytest.raises(ValidationError, match="element_types"):
            ElementQuerySchema.model_validate(payload(element_types=["flux_capacitor"]))

    def test_a_contradiction_passes_the_schema_and_fails_in_the_domain(self) -> None:
        schema = ElementQuerySchema.model_validate(payload(root_only=True, package_id=str(uuid4())))

        with pytest.raises(ValueError, match="root_only"):
            schema.to_domain()
```

`backend/tests/e2e/test_query_api.py` :

```python
"""`POST /elements/search` — the contract, against a doubled graph.

The double does not filter (tests/conftest.py), so what is proved here is the
door: the body, the page, the refusals. What the SQL does with a query is
`tests/integration/test_element_search.py`'s.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(service: ArchitectureService) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def body(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


async def test_a_query_answers_a_page_of_elements(
    client: httpx.AsyncClient, service: ArchitectureService
) -> None:
    await service.create_element(element_type="application_component", name="Billing")  # type: ignore[arg-type]

    response = await client.post("/elements/search", json=body(limit=10, offset=0))

    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (1, 10, 0)
    assert page["items"][0]["name"] == "Billing"
    assert "package_id" in page["items"][0]


@pytest.mark.parametrize(
    "changes",
    [
        {"properties": [{"key": "a", "op": "gt", "value": "1"}]},
        {"properties": [{"key": "a-b", "op": "exists", "value": None}]},
        {"element_types": ["flux_capacitor"]},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"version": 2},
        {"colour": "red"},
    ],
)
async def test_an_invalid_body_is_a_422(client: httpx.AsyncClient, changes: dict[str, Any]) -> None:
    assert (await client.post("/elements/search", json=body(**changes))).status_code == 422


async def test_a_missing_field_is_a_422(client: httpx.AsyncClient) -> None:
    incomplete = body()
    del incomplete["layers"]

    assert (await client.post("/elements/search", json=incomplete)).status_code == 422


async def test_the_root_and_a_package_at_once_is_a_422_from_the_domain(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/elements/search", json=body(root_only=True, package_id=str(uuid4()))
    )

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


async def test_a_property_test_that_lacks_its_value_is_a_422_from_the_domain(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/elements/search",
        json=body(properties=[{"key": "owner", "op": "eq", "value": None}]),
    )

    assert (response.status_code, response.json()["error"]) == (422, "invalid_input")


async def test_a_reader_may_search_and_nobody_may_search_without_a_token(
    service: ArchitectureService, nobody_calling: None
) -> None:
    verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        as_reader = await client.post(
            "/elements/search", json=body(), headers={"Authorization": "Bearer rd"}
        )
        anonymous = await client.post("/elements/search", json=body())

    assert as_reader.status_code == 200
    assert anonymous.status_code == 401
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_query_schema.py tests/e2e/test_query_api.py -x`
Expected: FAIL — `ImportError: cannot import name 'ElementQuerySchema' from 'ea.api.schemas'`.

- [ ] **Step 3: Schémas** — `backend/src/ea/api/schemas.py`

Imports : `from typing import Annotated, Final, Literal` (ajouter `Literal`) ; `from ea.domain.query import MAX_PROPERTY_TESTS, MAX_PROPERTY_VALUE, MAX_RELATION_TESTS, QUERY_VERSION, Direction, ElementQuery, PropertyOp, PropertyTest, QueryOrder, RelationTest`. Section à ajouter en fin de fichier :

```python
# --- Structured search ------------------------------------------------------
# `ElementQuerySchema` is the strict translation of `domain.query.ElementQuery`,
# and its dump is the form a saved query is stored in. Every field is required
# and none has a default, on purpose: it is both a request body and (in a saved
# query) a response, and a default would split the component into an input and
# an output type in the OpenAPI document and rename what the SPA imports — the
# reason `DiagramNode` is built the same way. The SPA and the MCP tools always
# build the whole object.

PropertyKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,62}$",
        description="The name of a user-defined attribute: letters, digits, underscores.",
    ),
]
PropertyValue = Annotated[str, Field(max_length=MAX_PROPERTY_VALUE)]


class PropertyTestSchema(_Input):
    """One test on a user-defined attribute. `value` is required by `eq` and `contains`, refused by the others."""

    key: PropertyKey
    op: PropertyOp
    value: PropertyValue | None

    def to_domain(self) -> PropertyTest:
        return PropertyTest(key=self.key, op=self.op, value=self.value)


class RelationTestSchema(_Input):
    """Whether an element has (or has no) relationship of one type, optionally to one type."""

    relationship_type: RelationshipType
    direction: Direction
    present: bool = Field(description="`false` finds the elements that have no such link.")
    other_type: ElementType | None = Field(description="The type at the far end, if it matters.")

    def to_domain(self) -> RelationTest:
        return RelationTest(
            relationship_type=self.relationship_type,
            direction=self.direction,
            present=self.present,
            other_type=self.other_type,
        )


class ElementQuerySchema(_Input):
    """A structured search of the catalogue; every criterion is combined with AND."""

    version: Literal[1]
    element_types: Annotated[list[ElementType], Field(max_length=len(ElementType))]
    layers: Annotated[list[Layer], Field(max_length=len(Layer))]
    text: Search | None
    package_id: UUID | None
    include_subpackages: bool
    root_only: bool
    properties: Annotated[list[PropertyTestSchema], Field(max_length=MAX_PROPERTY_TESTS)]
    relations: Annotated[list[RelationTestSchema], Field(max_length=MAX_RELATION_TESTS)]
    order: QueryOrder
    limit: Limit
    offset: Offset

    def to_domain(self) -> ElementQuery:
        """The validated domain object — where `root_only` + `package_id` is refused."""
        return ElementQuery(
            element_types=tuple(self.element_types),
            layers=tuple(self.layers),
            text=self.text,
            package_id=self.package_id,
            include_subpackages=self.include_subpackages,
            root_only=self.root_only,
            properties=tuple(test.to_domain() for test in self.properties),
            relations=tuple(test.to_domain() for test in self.relations),
            order=self.order,
            limit=self.limit,
            offset=self.offset,
        )

    @classmethod
    def of(cls, query: ElementQuery) -> ElementQuerySchema:
        return cls(
            version=QUERY_VERSION,
            element_types=list(query.element_types),
            layers=list(query.layers),
            text=query.text,
            package_id=query.package_id,
            include_subpackages=query.include_subpackages,
            root_only=query.root_only,
            properties=[
                PropertyTestSchema(key=test.key, op=test.op, value=test.value)
                for test in query.properties
            ],
            relations=[
                RelationTestSchema(
                    relationship_type=test.relationship_type,
                    direction=test.direction,
                    present=test.present,
                    other_type=test.other_type,
                )
                for test in query.relations
            ],
            order=query.order,
            limit=query.limit,
            offset=query.offset,
        )
```

(`Literal[1]` doit valoir `QUERY_VERSION` : le test `test_a_version_that_is_not_1` et le round-trip le gardent ; `version=QUERY_VERSION` est typé `Final = 1`, accepté par mypy comme `Literal[1]`.)

- [ ] **Step 4: Statut d'erreur** — `api/errors.py` : importer `InvalidQueryError` et ajouter dans `_STATUS` :

```python
    # --- Structured search and saved queries ---
    InvalidQueryError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_query"),
```

- [ ] **Step 5: La route** — `backend/src/ea/api/queries.py`

```python
"""Structured search of the catalogue, and the saved queries that keep it.

A query is a tree of data (`ElementQuerySchema`), never a string of SQL: the
repository compiles it, every key and value bound. It is built into an
`ElementQuery` here — where a contradiction is refused with a 422 — and the
service neither knows nor cares which adapter it came from.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from ea.api.dependencies import Architecture
from ea.api.schemas import ElementPage, ElementQuerySchema, ElementRead, ErrorResponse
from ea.domain.ports import ElementFilter

router = APIRouter(tags=["search"])

_ERROR = {"model": ErrorResponse}
INVALID: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR
}


@router.post("/elements/search", response_model=ElementPage, responses=INVALID)
async def search_elements(body: ElementQuerySchema, service: Architecture) -> ElementPage:
    """Types, layers, name fragment, package, attributes and links present or absent.

    Every criterion is combined with AND. `total` counts everything the query
    matches, not the page. Reading needs no more than a login — it is a read.
    """
    query = body.to_domain()
    criteria = ElementFilter.from_query(query)
    elements = await service.list_elements(criteria)
    return ElementPage(
        items=[ElementRead.of(element) for element in elements],
        total=await service.count_elements(criteria),
        limit=query.limit,
        offset=query.offset,
    )
```

`main.py` : `from ea.api.queries import router as queries_router` et `app.include_router(queries_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)` à côté du routeur `architecture`.

- [ ] **Step 6: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q && uv run mypy src`
Expected: PASS — y compris `tests/unit/test_error_statuses.py` : toutes les erreurs de domaine du lot 1 et `InvalidQueryError` sont mappées (les erreurs de requêtes enregistrées arrivent au lot 4 avec leur ligne).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/api backend/src/ea/main.py backend/tests/unit/test_query_schema.py backend/tests/e2e/test_query_api.py
git commit -m "feat(api): search the catalogue with a structured query" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Clôture du lot 3 — contrat, intégration, ADR du filtre

**Files:**
- Modify (générés) : `backend/openapi.json`, `frontend/src/api/schema.d.ts`
- Create: `docs/adr/00NN-le-filtre-d-elements-objet-de-domaine-pur.md` (numéro suivant celui des paquets)

- [ ] **Step 1: Régénérer le contrat**

Run: `make openapi && grep -c "ElementQuerySchema" backend/openapi.json`
Expected: les deux fichiers changent ; `ElementQuerySchema` y figure ; **aucun** composant `ElementQuerySchema-Input` / `-Output` (`grep -c "ElementQuerySchema-" backend/openapi.json` vaut 0).

- [ ] **Step 2: La porte qualité et l'intégration**

Run: `make check && make test-integration`
Expected: vert.

- [ ] **Step 3: Rédiger l'ADR** — `docs/adr/00NN-le-filtre-d-elements-objet-de-domaine-pur.md`

```markdown
---
titre: Le filtre d'éléments — un objet de domaine pur, partagé par le catalogue, MCP et les vues
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/query.py, backend/src/ea/domain/ports.py, backend/src/ea/domain/errors.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/api/schemas.py, backend/src/ea/api/queries.py, backend/src/ea/mcp/server.py, frontend/src/features/search
---

# NN. Le filtre d'éléments — un objet de domaine pur, partagé par le catalogue, MCP et les vues

Date : 2026-10-10
Statut : Proposition

Supersède en partie le rôle d'`ElementFilter` (`backend/src/ea/domain/ports.py`),
qui devient la forme étroite que le dépôt compile.

## Contexte

`GET /elements` sait filtrer par type, par couche et par fragment de nom, et
rien d'autre. Or les questions qu'une équipe d'architecture pose au catalogue
sont d'un autre ordre : « les composants applicatifs du paquet Finance dont le
propriétaire est renseigné et qui ne servent aucun processus ». Les obtenir
aujourd'hui, c'est lire le catalogue page à page et filtrer côté client — pour
le SPA comme pour un agent MCP, qui paie chaque page en jetons. Et ce besoin va
se répéter : les vues générées d'un sous-projet suivant voudront exactement le
même filtre.

Écrire un filtre par consommateur, c'est écrire trois fois (HTTP, MCP, vues) la
même règle de combinaison, et la voir diverger.

## Décision

Un objet de domaine pur, `ElementQuery` (`domain/query.py`), est **le** filtre :
types, couches, fragment de nom, paquet (avec ou sans sous-paquets, ou « à la
racine »), tests de propriétés (`eq`, `contains`, `exists`, `absent`), tests de
relations (type, sens `out|in|any`, présente ou absente, type de l'autre bout),
tri (`name`, `created_at`, `updated_at`), page. Tous les critères se combinent
par ET.

| Point | Choix |
|---|---|
| Un seul chemin SQL | `ElementQuery` devient un `ElementFilter` (`from_query`) que `architecture_store._matching` compile ; `GET /elements`, `POST /elements/search`, les requêtes enregistrées et les outils MCP passent par là. |
| Aucun SQL libre | Le filtre est un arbre de données. Les clés et valeurs de propriétés sont des paramètres liés (`->>` avec clé liée) ; le texte passe par `strpos`, jamais `ILIKE`. |
| Pas de règle ArchiMate recopiée | Les types et relations sont les énumérations du métamodèle. Un test de relation qu'aucun type filtré ne peut satisfaire est accepté : la réponse est vide, le métamodèle reste le seul juge. |
| Bornes | 10 tests de propriétés, 5 tests de relations, 200 caractères de texte et de valeur, page de 1 à 200. Les contradictions (`root_only` avec un paquet) sont refusées en `422`. |
| Valeurs textuelles | Les propriétés sont du texte (comme dans le modèle) : pas de comparaison numérique. |
| Forme sérialisée | JSON versionné (`version: 1`) ; un schéma Pydantic strict (`extra="forbid"`, **sans valeur par défaut** pour ne pas scinder le composant OpenAPI en entrée et sortie) en est la porte HTTP, et `to_json`/`from_json` la porte du domaine. |
| Tri | Nom croissant ; dates du plus récent au plus ancien ; l'identifiant départage toujours, pour qu'une pagination ne répète ni ne perde une ligne. |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Étendre `GET /elements` de paramètres de requête | Pas de nouvelle route | Les propriétés et relations en paramètres plats sont illisibles et non bornables ; rien à enregistrer | Écartée |
| SQL libre ou petit langage de requête | Expressif | Surface d'injection, parseur à maintenir, aucune garantie de borne | Écartée |
| Filtrer en Python après lecture | Aucun SQL à écrire | Lit tout le catalogue ; `total` et pagination faux | Écartée |
| Un filtre par adaptateur | Chacun à son rythme | Trois règles de combinaison qui divergent | Écartée |
| Schéma avec valeurs par défaut | Corps de requête plus court | Scinde le composant OpenAPI (`-Input`/`-Output`) | Écartée |

## Conséquences

- Tout critère nouveau s'ajoute à `ElementQuery`, à `ElementFilter`, à
  `_matching`, au schéma et à `to_json` — et chaque lieu a son test. Un
  critère qui change le sens d'un champ existant demande `version: 2` et un
  lecteur qui refuse l'ancienne.
- Une requête relue après coup repasse par `from_json` : version inconnue ou
  valeur que les énumérations ne connaissent plus → `422 invalid_query`, jamais
  une erreur de serveur.
- `EXISTS` corrélé par test de relation : correct à quelques milliers
  d'éléments ; à mesurer avant tout index dédié (`relationships` a déjà ses
  index sur les deux extrémités).
- Le filtre ne sait pas comparer des nombres : une propriété numérique se teste
  en texte.

## Références

- ADR liés : [0033](0033-postgresql-seul-pour-le-graphe.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), l'ADR des paquets
- Spec : `docs/superpowers/specs/2026-10-10-01-referentiel-structure-design.md`
- Code concerné : `backend/src/ea/domain/query.py`, `backend/src/ea/repositories/architecture_store.py` (`_matching`)
```

- [ ] **Step 4: Commit**

```bash
git add backend/openapi.json frontend/src/api/schema.d.ts docs/adr
git commit -m "docs(adr): record the structured element query and regenerate the client" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# LOT 4 — Requêtes enregistrées

### Task 21: Domaine des requêtes enregistrées et port

**Files:**
- Create: `backend/src/ea/domain/saved_queries.py`
- Modify: `backend/src/ea/domain/errors.py` (ajout), `backend/src/ea/domain/ports.py` (`SavedQueryRepository`)
- Test: `backend/tests/unit/test_saved_queries.py`

**Interfaces:**
- Consumes: `ElementQuery`, `check_storable` (Task 17).
- Produces:
  - `SavedQueryNotFoundError`, `DuplicateSavedQueryError` (`DomainError`).
  - `ea.domain.saved_queries` : `MAX_SAVED_QUERY_NAME_LENGTH = 200`, `MAX_SAVED_QUERY_DESCRIPTION = 2000`, `SavedQuerySummary(id, name, description, created_by, created_at, updated_at)`, `SavedQuery(id, name, description, query, created_by, created_at, updated_at)` avec `SavedQuery.create(*, name, query, created_by, now, description="") -> SavedQuery`, `.revise(*, now, name=None, description=None, query=None) -> SavedQuery`, `.summary -> SavedQuerySummary`.
  - `SavedQueryRepository` (Protocol) : `list_all() -> tuple[SavedQuerySummary, ...]` (jamais de lecture des requêtes), `get(query_id) -> SavedQuery | None` (lève `InvalidQueryError` si le JSON stocké est illisible), `add(saved) -> SavedQuery`, `save(saved) -> SavedQuery`, `delete(query_id) -> bool`.

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/unit/test_saved_queries.py`

```python
"""A saved query: a named filter, shared, with whoever wrote it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ea.domain import query as query_module
from ea.domain.archimate import ElementType as E
from ea.domain.errors import InvalidQueryError
from ea.domain.query import ElementQuery
from ea.domain.saved_queries import SavedQuery

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_saved_query(**overrides: object) -> SavedQuery:
    fields: dict[str, object] = {
        "name": "Les noeuds",
        "query": ElementQuery(element_types=(E.NODE,)),
        "created_by": "editor",
        "now": NOW,
    }
    return SavedQuery.create(**{**fields, **overrides})  # type: ignore[arg-type]


class TestCreating:
    def test_the_name_and_description_are_trimmed_and_the_author_recorded(self) -> None:
        saved = a_saved_query(name="  Les noeuds ", description=" Tous ")

        assert (saved.name, saved.description, saved.created_by) == ("Les noeuds", "Tous", "editor")
        assert saved.created_at == saved.updated_at == NOW

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_a_blank_or_long_name_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="name"):
            a_saved_query(name=name)

    def test_a_long_description_is_refused(self) -> None:
        with pytest.raises(ValueError, match="description"):
            a_saved_query(description="x" * 2001)

    def test_a_query_too_large_to_keep_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(query_module, "MAX_STORED_QUERY_BYTES", 50)

        with pytest.raises(InvalidQueryError):
            a_saved_query()


class TestRevising:
    def test_an_omitted_field_is_kept(self) -> None:
        saved = a_saved_query()
        later = NOW + timedelta(hours=1)

        revised = saved.revise(now=later, description="neuf")

        assert (revised.name, revised.query, revised.created_by) == (
            saved.name,
            saved.query,
            saved.created_by,
        )
        assert (revised.description, revised.updated_at, revised.created_at) == ("neuf", later, NOW)

    def test_the_query_can_be_replaced(self) -> None:
        replacement = ElementQuery(text="db")

        assert a_saved_query().revise(now=NOW, query=replacement).query == replacement

    def test_the_author_is_never_rewritten(self) -> None:
        assert a_saved_query().revise(now=NOW, name="Autre").created_by == "editor"

    def test_the_summary_leaves_the_query_out(self) -> None:
        saved = a_saved_query()

        assert not hasattr(saved.summary, "query")
        assert saved.summary.id == saved.id
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_saved_queries.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.saved_queries'`.

- [ ] **Step 3: Erreurs** — fin de `backend/src/ea/domain/errors.py`

```python


class SavedQueryNotFoundError(DomainError):
    """A saved query referenced by id is absent from the store."""


class DuplicateSavedQueryError(DomainError):
    """A saved query name is already taken."""
```

- [ ] **Step 4: Implémenter** — `backend/src/ea/domain/saved_queries.py`

```python
"""A saved query: a named `ElementQuery`, shared by every reader of the catalogue.

Shared on purpose (spec §1): there is no private owner, `created_by` only says
who wrote it, and any editor may change it. A query is data, not behaviour — it
names a package by id with no foreign key, so a package that goes away leaves
the query intact and the run says so (`UnknownPackageError`).

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from ea.domain.query import ElementQuery, check_storable

MAX_SAVED_QUERY_NAME_LENGTH: Final = 200
MAX_SAVED_QUERY_DESCRIPTION: Final = 2000


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_SAVED_QUERY_NAME_LENGTH:
        msg = f"a saved query name is 1 to {MAX_SAVED_QUERY_NAME_LENGTH} characters"
        raise ValueError(msg)
    return cleaned


def _clean_description(description: str) -> str:
    cleaned = description.strip()
    if len(cleaned) > MAX_SAVED_QUERY_DESCRIPTION:
        msg = f"a saved query description is at most {MAX_SAVED_QUERY_DESCRIPTION} characters"
        raise ValueError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class SavedQuerySummary:
    """What a listing shows: everything but the query, which is never parsed for a list."""

    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SavedQuery:
    id: UUID
    name: str
    description: str
    query: ElementQuery
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        name: str,
        query: ElementQuery,
        created_by: str,
        now: datetime,
        description: str = "",
    ) -> SavedQuery:
        check_storable(query)
        return cls(
            id=uuid4(),
            name=_clean_name(name),
            description=_clean_description(description),
            query=query,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def revise(
        self,
        *,
        now: datetime,
        name: str | None = None,
        description: str | None = None,
        query: ElementQuery | None = None,
    ) -> SavedQuery:
        """The same saved query with something changed; an omitted field is kept."""
        if query is not None:
            check_storable(query)
        return replace(
            self,
            name=self.name if name is None else _clean_name(name),
            description=(
                self.description if description is None else _clean_description(description)
            ),
            query=self.query if query is None else query,
            updated_at=now,
        )

    @property
    def summary(self) -> SavedQuerySummary:
        return SavedQuerySummary(
            id=self.id,
            name=self.name,
            description=self.description,
            created_by=self.created_by,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
```

Dans `domain/ports.py` (avec `from ea.domain.saved_queries import SavedQuery, SavedQuerySummary` aux imports) :

```python
class SavedQueryRepository(Protocol):
    """Persistence for the named, shared filters of the catalogue."""

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        """Every saved query, sorted by lower-cased name, without reading a stored query.

        A list that parsed every query would be unreadable the day one of them
        is not: one stale row must not hide the others.
        """
        ...

    async def get(self, query_id: UUID) -> SavedQuery | None:
        """One saved query; raises `InvalidQueryError` when its stored JSON cannot be read."""
        ...

    async def add(self, saved: SavedQuery) -> SavedQuery:
        """Store a new saved query, or refuse a name already taken."""
        ...

    async def save(self, saved: SavedQuery) -> SavedQuery:
        """Overwrite name, description, query and update time; never the author."""
        ...

    async def delete(self, query_id: UUID) -> bool: ...
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_saved_queries.py tests/unit/test_element_query.py -x && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain backend/tests/unit/test_saved_queries.py
git commit -m "feat(domain): add the saved query and its port" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 22: Table `saved_queries`, migration 0010 et dépôt

**Files:**
- Create: `backend/src/ea/db/models/saved_query.py`, `backend/migrations/versions/0010_saved_queries.py` (à renuméroter si une autre migration est entrée avant), `backend/src/ea/repositories/saved_query_store.py`
- Modify: `backend/src/ea/db/models/__init__.py`
- Test: `backend/tests/unit/test_migrations.py` (ajout), `backend/tests/integration/test_saved_query_store.py`

**Interfaces:**
- Consumes: `SavedQueryRepository` (Task 21), `to_json`/`from_json` (Task 17), `constraint_of`.
- Produces: `SavedQueryRecord` ; `PostgresSavedQueryRepository(sessions)`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajout à `backend/tests/unit/test_migrations.py` :

```python
def test_revision_0010_follows_0009() -> None:
    revision = ScriptDirectory.from_config(_config()).get_revision("0010")

    assert revision is not None
    assert revision.down_revision == "0009"


def test_the_saved_queries_table_has_a_unique_name_and_a_jsonb_query() -> None:
    table = Base.metadata.tables["saved_queries"]

    assert {c.name for c in table.constraints if c.name} >= {"uq_saved_queries_name"}
    assert table.c.query.type.__class__.__name__ == "JSONB"
    assert not table.c.query.nullable
```

`backend/tests/integration/test_saved_query_store.py` :

```python
"""The saved-query repository against a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateSavedQueryError, InvalidQueryError, SavedQueryNotFoundError
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.domain.saved_queries import SavedQuery
from ea.repositories.saved_query_store import PostgresSavedQueryRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_saved(name: str = "Les noeuds", **query: object) -> SavedQuery:
    return SavedQuery.create(
        name=name,
        query=ElementQuery(**query),  # type: ignore[arg-type]
        created_by="editor",
        now=NOW,
        description="d",
    )


@pytest.fixture
def store(engine_at_head: AsyncEngine) -> PostgresSavedQueryRepository:
    return PostgresSavedQueryRepository(create_session_factory(engine_at_head))


async def corrupt(engine: AsyncEngine, stored: str) -> str:
    """A row whose query the code can no longer read, written behind the repository's back."""
    identifier = str(uuid4())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO saved_queries (id, name, description, query, created_by, "
                "created_at, updated_at) VALUES (:id, :name, '', CAST(:query AS jsonb), "
                "'someone', now(), now())"
            ),
            {"id": identifier, "name": f"corrompue {identifier[:4]}", "query": stored},
        )
    return identifier


async def test_a_full_query_comes_back_equal(store: PostgresSavedQueryRepository) -> None:
    saved = a_saved(
        element_types=(E.NODE,),
        text="db",
        properties=(PropertyTest("owner", PropertyOp.EQ, "é'\"x"),),
        relations=(RelationTest(R.SERVING, Direction.IN, False, E.BUSINESS_PROCESS),),
        order=QueryOrder.CREATED_AT,
        limit=10,
    )

    await store.add(saved)

    assert await store.get(saved.id) == saved


async def test_a_taken_name_is_refused_on_add_and_on_save(
    store: PostgresSavedQueryRepository,
) -> None:
    await store.add(a_saved("A"))
    other = await store.add(a_saved("B"))

    with pytest.raises(DuplicateSavedQueryError):
        await store.add(a_saved("A"))
    with pytest.raises(DuplicateSavedQueryError):
        await store.save(other.revise(now=NOW, name="A"))


async def test_save_replaces_the_query_and_never_the_author(
    store: PostgresSavedQueryRepository,
) -> None:
    saved = await store.add(a_saved())

    await store.save(saved.revise(now=NOW, query=ElementQuery(text="neuf")))

    reread = await store.get(saved.id)
    assert reread is not None
    assert (reread.query.text, reread.created_by) == ("neuf", "editor")


async def test_saving_what_is_gone_is_a_not_found(store: PostgresSavedQueryRepository) -> None:
    with pytest.raises(SavedQueryNotFoundError):
        await store.save(a_saved())


async def test_delete_says_whether_it_removed_something(
    store: PostgresSavedQueryRepository,
) -> None:
    saved = await store.add(a_saved())

    assert await store.delete(saved.id) is True
    assert await store.delete(saved.id) is False
    assert await store.get(saved.id) is None


async def test_the_list_is_sorted_and_survives_a_row_it_cannot_read(
    engine_at_head: AsyncEngine, store: PostgresSavedQueryRepository
) -> None:
    await store.add(a_saved("beta"))
    await store.add(a_saved("Alpha"))
    broken = await corrupt(engine_at_head, '{"version": 2}')

    listed = await store.list_all()

    assert [s.name for s in listed][:2] == ["Alpha", "beta"]
    assert len(listed) == 3
    with pytest.raises(InvalidQueryError, match="version"):
        await store.get(UUID(broken))


@pytest.mark.parametrize("stored", ['"oops"', "null", "[1]", '{"version": 1}'])
async def test_whatever_was_stored_is_refused_cleanly(
    engine_at_head: AsyncEngine, store: PostgresSavedQueryRepository, stored: str
) -> None:
    broken = await corrupt(engine_at_head, stored)

    with pytest.raises(InvalidQueryError):
        await store.get(UUID(broken))
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x -k "0010 or saved_queries"`
Expected: FAIL — `KeyError: 'saved_queries'`.

- [ ] **Step 3: Modèle** — `backend/src/ea/db/models/saved_query.py`

```python
"""The named, shared filters of the catalogue.

`query` is the versioned JSON of `domain.query.to_json`. It carries no foreign
key into the rest of the schema — a package is named by id inside the JSON — so
that a package or a metamodel entry going away leaves the row readable and the
run explains what went stale, instead of a constraint deleting someone's query.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class SavedQueryRecord(Base):
    __tablename__ = "saved_queries"
    # The constraint rather than a read-then-insert: two editors saving the same
    # name at once cannot both find it free.
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`db/models/__init__.py` : `from ea.db.models.saved_query import SavedQueryRecord` et `"SavedQueryRecord"` dans `__all__`.

- [ ] **Step 4: Migration** — `backend/migrations/versions/0010_saved_queries.py`

```python
"""les requêtes enregistrées : des filtres nommés, partagés

Revision ID: 0010
Revises: 0009
Date: 2026-10-10

`query` est le JSON versionné d'`ElementQuery`. Aucune clé étrangère : un paquet
y est désigné par son identifiant, et sa disparition laisse la requête intacte
— l'exécution répond `unknown_package`, la requête reste modifiable. Le nom est
unique (sensible à la casse) et c'est une contrainte, pas une lecture suivie
d'une insertion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_queries")),
        sa.UniqueConstraint("name", name=op.f("uq_saved_queries_name")),
    )


def downgrade() -> None:
    op.drop_table("saved_queries")
```

- [ ] **Step 5: Dépôt** — `backend/src/ea/repositories/saved_query_store.py`

```python
"""The PostgreSQL implementation of `SavedQueryRepository`.

A listing selects the plain columns and never touches `query`: one stale row
must not make the others unreadable. `get` reads the JSON back through
`from_json`, which refuses what the code can no longer vouch for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.saved_query import SavedQueryRecord
from ea.domain.errors import DuplicateSavedQueryError, SavedQueryNotFoundError
from ea.domain.query import from_json, to_json
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.repositories.architecture_store import constraint_of

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NAME_TAKEN: Final = "uq_saved_queries_name"


def _taken(saved: SavedQuery) -> DuplicateSavedQueryError:
    return DuplicateSavedQueryError(f"a saved query is already named {saved.name!r}")


class PostgresSavedQueryRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        statement = select(
            SavedQueryRecord.id,
            SavedQueryRecord.name,
            SavedQueryRecord.description,
            SavedQueryRecord.created_by,
            SavedQueryRecord.created_at,
            SavedQueryRecord.updated_at,
        ).order_by(func.lower(SavedQueryRecord.name), SavedQueryRecord.id)
        async with self._sessions() as session:
            return tuple(
                SavedQuerySummary(*row) for row in (await session.execute(statement)).tuples()
            )

    async def get(self, query_id: UUID) -> SavedQuery | None:
        async with self._sessions() as session:
            row = await session.get(SavedQueryRecord, query_id)
            if row is None:
                return None
            return SavedQuery(
                id=row.id,
                name=row.name,
                description=row.description,
                query=from_json(row.query),
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def add(self, saved: SavedQuery) -> SavedQuery:
        async with self._sessions.begin() as session:
            session.add(
                SavedQueryRecord(
                    id=saved.id,
                    name=saved.name,
                    description=saved.description,
                    query=to_json(saved.query),
                    created_by=saved.created_by,
                    created_at=saved.created_at,
                    updated_at=saved.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != NAME_TAKEN:
                    raise
                raise _taken(saved) from error
        return saved

    async def save(self, saved: SavedQuery) -> SavedQuery:
        async with self._sessions.begin() as session:
            row = await session.get(SavedQueryRecord, saved.id)
            if row is None:
                msg = f"no saved query with id {saved.id}"
                raise SavedQueryNotFoundError(msg)
            row.name = saved.name
            row.description = saved.description
            row.query = to_json(saved.query)
            row.updated_at = saved.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != NAME_TAKEN:
                    raise
                raise _taken(saved) from error
        return saved

    async def delete(self, query_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(SavedQueryRecord).where(SavedQueryRecord.id == query_id)
            )
        return bool(cast("CursorResult[Any]", deleted).rowcount)
```

- [ ] **Step 6: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x && uv run mypy src migrations && uv run pytest tests/integration/test_saved_query_store.py -x`
Expected: PASS (l'intégration sur conteneur jetable : `make test-integration` pour qu'elle ne soit pas *skippée*).

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/db backend/migrations/versions/0010_saved_queries.py backend/src/ea/repositories/saved_query_store.py backend/tests/unit/test_migrations.py backend/tests/integration/test_saved_query_store.py
git commit -m "feat(db): store saved queries as versioned JSON (0010)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 23: `SavedQueryService`

**Files:**
- Create: `backend/src/ea/services/saved_queries.py`
- Modify: `backend/tests/conftest.py` (classe `InMemorySavedQueries`, fixtures `saved_queries`, `saved_query_service`), `backend/tests/unit/test_service_guards.py` (`WRITES`)
- Test: `backend/tests/unit/test_saved_query_service.py`

**Interfaces:**
- Consumes: `SavedQueryRepository` (Task 21), `PackageRepository` (Task 2), `ArchitectureService.list_elements/count_elements`, `ElementFilter.from_query`, `QueryResult`, `UnknownPackageError`, erreurs de la Task 21.
- Produces: `SavedQueryService(repository, packages: PackageRepository | None, architecture, *, clock=...)` avec :
  - `async list_queries() -> tuple[SavedQuerySummary, ...]` (lecture)
  - `async get(query_id: UUID) -> SavedQuery` (lecture)
  - `async create(*, name: str, query: ElementQuery, description: str = "") -> SavedQuery` (écriture ; `created_by` = nom de l'appelant)
  - `async update(query_id: UUID, *, name: str | None = None, description: str | None = None, query: ElementQuery | None = None) -> SavedQuery` (écriture)
  - `async delete(query_id: UUID) -> None` (écriture)
  - `async run(query_id: UUID, *, limit: int = 50, offset: int = 0) -> QueryResult` (lecture ; `UnknownPackageError` si le paquet visé a disparu)

- [ ] **Step 1: Écrire le test qui échoue** — `backend/tests/unit/test_saved_query_service.py`

```python
"""The saved-query use cases: who wrote it, who may change it, and what a run says."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    DuplicateSavedQueryError,
    InvalidQueryError,
    NotAuthorisedError,
    SavedQueryNotFoundError,
    UnknownPackageError,
)
from ea.domain.packages import Package
from ea.domain.query import ElementQuery
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.saved_queries import SavedQueryService
from tests.conftest import FIXED_NOW, InMemoryPackages, InMemorySavedQueries, a_reader, an_editor

pytestmark = pytest.mark.asyncio


class TestWritingAndReading:
    async def test_the_author_is_the_caller_not_a_parameter(
        self, saved_query_service: SavedQueryService
    ) -> None:
        with acting_as(an_editor(username="nicolas")):
            saved = await saved_query_service.create(name="Les noeuds", query=ElementQuery())

        assert saved.created_by == "nicolas"
        assert saved.created_at == FIXED_NOW

    async def test_a_taken_name_is_refused(self, saved_query_service: SavedQueryService) -> None:
        await saved_query_service.create(name="A", query=ElementQuery())

        with pytest.raises(DuplicateSavedQueryError):
            await saved_query_service.create(name="A", query=ElementQuery())

    async def test_any_editor_may_change_what_another_wrote_and_the_author_stays(
        self, saved_query_service: SavedQueryService
    ) -> None:
        with acting_as(an_editor(username="alice")):
            saved = await saved_query_service.create(name="A", query=ElementQuery())

        with acting_as(an_editor(username="bob")):
            updated = await saved_query_service.update(
                saved.id, name="B", query=ElementQuery(text="db")
            )

        assert (updated.name, updated.query.text, updated.created_by) == ("B", "db", "alice")

    async def test_an_omitted_field_is_kept(self, saved_query_service: SavedQueryService) -> None:
        saved = await saved_query_service.create(
            name="A", query=ElementQuery(text="db"), description="d"
        )

        updated = await saved_query_service.update(saved.id, description="neuf")

        assert (updated.name, updated.query.text, updated.description) == ("A", "db", "neuf")

    async def test_the_listing_gives_summaries_sorted_by_name(
        self, saved_query_service: SavedQueryService
    ) -> None:
        await saved_query_service.create(name="beta", query=ElementQuery())
        await saved_query_service.create(name="Alpha", query=ElementQuery())

        assert [s.name for s in await saved_query_service.list_queries()] == ["Alpha", "beta"]

    async def test_a_missing_query_is_a_not_found_to_read_change_or_delete(
        self, saved_query_service: SavedQueryService
    ) -> None:
        for call in (
            saved_query_service.get(uuid4()),
            saved_query_service.update(uuid4(), name="x"),
            saved_query_service.delete(uuid4()),
            saved_query_service.run(uuid4()),
        ):
            with pytest.raises(SavedQueryNotFoundError):
                await call

    async def test_delete_removes_the_query_and_only_the_query(
        self, saved_query_service: SavedQueryService, service: ArchitectureService
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        await saved_query_service.delete(saved.id)

        assert await saved_query_service.list_queries() == ()
        assert (await service.get_element(element.id)).name == "db-01"

    async def test_a_reader_reads_runs_and_lists_but_never_writes(
        self, saved_query_service: SavedQueryService
    ) -> None:
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        with acting_as(a_reader()):
            assert len(await saved_query_service.list_queries()) == 1
            assert (await saved_query_service.get(saved.id)).name == "A"
            await saved_query_service.run(saved.id)
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.create(name="B", query=ElementQuery())
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.update(saved.id, name="C")
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.delete(saved.id)


class TestRunning:
    async def test_a_run_pages_over_the_stored_filter_and_counts_the_whole(
        self, saved_query_service: SavedQueryService, service: ArchitectureService
    ) -> None:
        for name in ("a", "b", "c"):
            await service.create_element(element_type=E.NODE, name=name)
        saved = await saved_query_service.create(name="Tous", query=ElementQuery(limit=200))

        result = await saved_query_service.run(saved.id, limit=2, offset=0)

        assert (len(result.elements), result.total) == (2, 3)

    async def test_a_page_outside_the_bounds_is_a_value_error(
        self, saved_query_service: SavedQueryService
    ) -> None:
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        with pytest.raises(ValueError, match="limit"):
            await saved_query_service.run(saved.id, limit=0)

    async def test_a_package_that_is_gone_is_an_unknown_package_and_the_query_stays_editable(
        self,
        saved_query_service: SavedQueryService,
        packages: InMemoryPackages,
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        saved = await saved_query_service.create(
            name="Compta", query=ElementQuery(package_id=finance.id)
        )
        await packages.delete(finance.id, into=None)

        with pytest.raises(UnknownPackageError, match=str(finance.id)):
            await saved_query_service.run(saved.id)

        fixed = await saved_query_service.update(saved.id, query=ElementQuery(root_only=True))
        assert (await saved_query_service.run(fixed.id)).total == 0

    async def test_a_package_that_exists_runs(
        self, saved_query_service: SavedQueryService, packages: InMemoryPackages
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        saved = await saved_query_service.create(
            name="Compta", query=ElementQuery(package_id=finance.id)
        )

        assert (await saved_query_service.run(saved.id)).total == 0

    async def test_a_stored_query_the_code_cannot_read_is_an_invalid_query_and_does_not_hide_the_list(
        self, saved_query_service: SavedQueryService, saved_queries: InMemorySavedQueries
    ) -> None:
        good = await saved_query_service.create(name="Bonne", query=ElementQuery())
        broken = await saved_query_service.create(name="Cassée", query=ElementQuery())
        saved_queries.corrupt.add(broken.id)

        with pytest.raises(InvalidQueryError):
            await saved_query_service.run(broken.id)

        assert [s.name for s in await saved_query_service.list_queries()] == ["Bonne", "Cassée"]
        assert (await saved_query_service.run(good.id)).total == 0


class TestTheAuditTrail:
    async def test_each_write_leaves_one_line_with_the_id(
        self, saved_query_service: SavedQueryService, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="ea.services"):
            saved = await saved_query_service.create(name="A", query=ElementQuery())
            await saved_query_service.update(saved.id, name="B")
            await saved_query_service.delete(saved.id)

        lines = [r for r in caplog.records if getattr(r, "action", "").startswith("query_")]
        assert [r.action for r in lines] == ["query_saved", "query_updated", "query_deleted"]  # type: ignore[attr-defined]
        assert all(r.query_id == str(saved.id) for r in lines)  # type: ignore[attr-defined]
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_saved_query_service.py -x`
Expected: FAIL — `ImportError: cannot import name 'InMemorySavedQueries' from 'tests.conftest'`.

- [ ] **Step 3: Doubles et fixtures** — `backend/tests/conftest.py`

Imports : `from ea.domain.errors import ... DuplicateSavedQueryError, InvalidQueryError, SavedQueryNotFoundError` ; `from ea.domain.saved_queries import SavedQuery, SavedQuerySummary` ; `from ea.services.saved_queries import SavedQueryService`. Classe, après `InMemoryPackages` :

```python
class InMemorySavedQueries:
    """A dictionary pretending to be `saved_queries`, with its unique name.

    `corrupt` holds ids whose stored JSON can no longer be read, so a test can
    prove a listing survives a row `get` refuses — as the real table does.
    """

    def __init__(self) -> None:
        self.queries: dict[UUID, SavedQuery] = {}
        self.corrupt: set[UUID] = set()

    def _refuse_a_taken_name(self, saved: SavedQuery) -> None:
        if any(s.name == saved.name and s.id != saved.id for s in self.queries.values()):
            msg = f"a saved query is already named {saved.name!r}"
            raise DuplicateSavedQueryError(msg)

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        ordered = sorted(self.queries.values(), key=lambda s: (s.name.lower(), s.id))
        return tuple(s.summary for s in ordered)

    async def get(self, query_id: UUID) -> SavedQuery | None:
        if query_id in self.corrupt:
            msg = "the stored query cannot be read: unsupported query version: 2"
            raise InvalidQueryError(msg)
        return self.queries.get(query_id)

    async def add(self, saved: SavedQuery) -> SavedQuery:
        self._refuse_a_taken_name(saved)
        self.queries[saved.id] = saved
        return saved

    async def save(self, saved: SavedQuery) -> SavedQuery:
        if saved.id not in self.queries:
            raise SavedQueryNotFoundError(str(saved.id))
        self._refuse_a_taken_name(saved)
        self.queries[saved.id] = saved
        return saved

    async def delete(self, query_id: UUID) -> bool:
        self.corrupt.discard(query_id)
        return self.queries.pop(query_id, None) is not None
```

Fixtures :

```python
@pytest.fixture
def saved_queries() -> InMemorySavedQueries:
    return InMemorySavedQueries()


@pytest.fixture
def saved_query_service(
    saved_queries: InMemorySavedQueries,
    packages: InMemoryPackages,
    service: ArchitectureService,
) -> SavedQueryService:
    return SavedQueryService(saved_queries, packages, service, clock=lambda: FIXED_NOW)
```

- [ ] **Step 4: Implémenter** — `backend/src/ea/services/saved_queries.py`

```python
"""Use cases over the saved queries — named, shared filters of the catalogue.

Shared on purpose: any reader may run one, any editor may change one, and the
author is only recorded — from the caller, never from a body. A run is the
only use case that needs another store: the package a query names is looked up
*then*, so a package deleted since leaves the query intact and says which one
went stale (`UnknownPackageError`) instead of silently answering nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import SavedQueryNotFoundError, UnknownPackageError
from ea.domain.ports import ElementFilter
from ea.domain.query import ElementQuery, QueryResult
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import PackageRepository, SavedQueryRepository
    from ea.services.architecture import ArchitectureService

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _not_found(query_id: UUID) -> SavedQueryNotFoundError:
    return SavedQueryNotFoundError(f"no saved query with id {query_id}")


class SavedQueryService:
    def __init__(
        self,
        repository: SavedQueryRepository,
        packages: PackageRepository | None,
        architecture: ArchitectureService,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._packages = packages
        self._architecture = architecture
        self._now = clock

    async def list_queries(self) -> tuple[SavedQuerySummary, ...]:
        require_caller()
        return await self._repository.list_all()

    async def get(self, query_id: UUID) -> SavedQuery:
        require_caller()
        saved = await self._repository.get(query_id)
        if saved is None:
            raise _not_found(query_id)
        return saved

    async def create(
        self, *, name: str, query: ElementQuery, description: str = ""
    ) -> SavedQuery:
        caller = require_editor()
        stored = await self._repository.add(
            SavedQuery.create(
                name=name,
                query=query,
                created_by=caller.username,
                description=description,
                now=self._now(),
            )
        )
        self._audit("query saved: %r", stored, "query_saved")
        return stored

    async def update(
        self,
        query_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        query: ElementQuery | None = None,
    ) -> SavedQuery:
        """Rename, describe or replace the filter. The author is never rewritten."""
        require_editor()
        current = await self.get(query_id)
        stored = await self._repository.save(
            current.revise(name=name, description=description, query=query, now=self._now())
        )
        self._audit("query updated: %r", stored, "query_updated")
        return stored

    async def delete(self, query_id: UUID) -> None:
        """Delete the query — not the elements it finds."""
        require_editor()
        if not await self._repository.delete(query_id):
            raise _not_found(query_id)
        logger.info(
            "saved query deleted",
            extra={"action": "query_deleted", "query_id": str(query_id)},
        )

    async def run(self, query_id: UUID, *, limit: int = 50, offset: int = 0) -> QueryResult:
        """Run a saved query, one page at a time (the page is the caller's, not the stored one)."""
        require_caller()
        saved = await self.get(query_id)
        wanted = saved.query.package_id
        if wanted is not None and (self._packages is None or await self._packages.get(wanted) is None):
            msg = f"the query {saved.name!r} names the package {wanted}, which no longer exists"
            raise UnknownPackageError(msg)
        criteria = ElementFilter.from_query(replace(saved.query, limit=limit, offset=offset))
        elements = await self._architecture.list_elements(criteria)
        return QueryResult(
            elements=elements, total=await self._architecture.count_elements(criteria)
        )

    @staticmethod
    def _audit(message: str, saved: SavedQuery, action: str) -> None:
        logger.info(message, saved.name, extra={"action": action, "query_id": str(saved.id)})
```

`replace(saved.query, limit=limit, offset=offset)` rejoue `ElementQuery.__post_init__` : un `limit` hors bornes lève `ValueError` ✓ (test).

`tests/unit/test_service_guards.py` : importer `SavedQueryService` et ajouter à `WRITES` `SavedQueryService: {"create", "update", "delete"},`.

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_saved_query_service.py tests/unit/test_service_guards.py -x -q && uv run mypy src`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/services/saved_queries.py backend/tests/conftest.py backend/tests/unit/test_saved_query_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(services): add the saved query use cases" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 24: L'API `/queries` et le câblage

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (section « Saved queries »), `backend/src/ea/api/errors.py`, `backend/src/ea/api/dependencies.py`, `backend/src/ea/api/queries.py`, `backend/src/ea/main.py`
- Test: `backend/tests/e2e/test_query_api.py` (ajout), `backend/tests/unit/test_query_schema.py` (ajout)

**Interfaces:**
- Consumes: `SavedQueryService` (Task 23), `ElementQuerySchema` (Task 19).
- Produces:
  - `SavedQueryCreate{name, description="", query: ElementQuerySchema}`, `SavedQueryUpdate{name=None, description=None, query=None}`, `SavedQuerySummaryRead{id, name, description, created_by, created_at, updated_at}`, `SavedQueryRead` (idem + `query: ElementQuerySchema`) avec `.of(...)`.
  - Routes : `GET/POST /queries`, `GET/PATCH/DELETE /queries/{query_id}`, `GET /queries/{query_id}/elements?limit&offset` → `ElementPage`.
  - `dependencies.saved_query_service_of(app)`, `SavedQueries` ; `create_app(..., queries: SavedQueryRepository | None = None)` ; `app.state.saved_query_service`.
  - `_STATUS` : `SavedQueryNotFoundError` 404 `not_found`, `DuplicateSavedQueryError` 409 `duplicate`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `backend/tests/unit/test_query_schema.py` :

```python
def test_the_query_is_one_openapi_component_for_requests_and_responses() -> None:
    """A default anywhere would split it into `-Input` and `-Output` and rename what the SPA imports."""
    from ea.openapi import openapi_document

    schemas = openapi_document()["components"]["schemas"]

    assert "ElementQuerySchema" in schemas
    assert not [name for name in schemas if name.startswith("ElementQuerySchema-")]
    assert "SavedQueryRead" in schemas
```

Dans `backend/tests/e2e/test_query_api.py`, compléter les imports de tête (`from uuid import UUID, uuid4`, `from ea.domain.packages import Package`, et `FIXED_NOW, InMemoryPackages, InMemorySavedQueries` dans l'import existant de `tests.conftest`), puis ajouter une fixture `saved_client` et les tests :

```python
@pytest_asyncio.fixture
async def saved_client(
    service: ArchitectureService,
    packages: InMemoryPackages,
    saved_queries: InMemorySavedQueries,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        packages=packages,  # type: ignore[arg-type]
        queries=saved_queries,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def save(client: httpx.AsyncClient, name: str = "Les noeuds", **query: Any) -> dict[str, Any]:
    response = await client.post(
        "/queries", json={"name": name, "description": "d", "query": body(**query)}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestSavingAndReading:
    async def test_a_saved_query_is_a_201_with_its_author_and_its_query(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, text="db")

        assert created["created_by"] == "local-developer"
        assert created["query"]["text"] == "db"
        assert set(created) == {
            "id", "name", "description", "created_by", "created_at", "updated_at", "query",
        }  # fmt: skip

    async def test_the_author_cannot_be_written_from_the_body(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post(
            "/queries",
            json={"name": "A", "created_by": "mallory", "query": body()},
        )

        assert response.status_code == 422

    async def test_a_taken_name_is_a_409(self, saved_client: httpx.AsyncClient) -> None:
        await save(saved_client, "A")

        response = await saved_client.post("/queries", json={"name": "A", "query": body()})

        assert (response.status_code, response.json()["error"]) == (409, "duplicate")

    async def test_a_contradictory_query_is_refused_at_the_door(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        response = await saved_client.post(
            "/queries",
            json={"name": "A", "query": body(root_only=True, package_id=str(uuid4()))},
        )

        assert (response.status_code, response.json()["error"]) == (422, "invalid_input")

    async def test_the_list_is_summaries_without_the_query(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        await save(saved_client, "beta")
        await save(saved_client, "Alpha")

        listed = (await saved_client.get("/queries")).json()

        assert [q["name"] for q in listed] == ["Alpha", "beta"]
        assert all("query" not in q for q in listed)

    async def test_one_query_comes_back_as_it_was_sent(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, limit=10, order="created_at")

        read = (await saved_client.get(f"/queries/{created['id']}")).json()

        assert read["query"] == body(limit=10, order="created_at")

    async def test_an_unknown_id_is_a_404(self, saved_client: httpx.AsyncClient) -> None:
        for request in (
            saved_client.get(f"/queries/{uuid4()}"),
            saved_client.patch(f"/queries/{uuid4()}", json={"name": "x"}),
            saved_client.delete(f"/queries/{uuid4()}"),
            saved_client.get(f"/queries/{uuid4()}/elements"),
        ):
            assert (await request).status_code == 404


class TestChangingAndDeleting:
    async def test_patch_renames_and_replaces_the_query_and_keeps_the_rest(
        self, saved_client: httpx.AsyncClient
    ) -> None:
        created = await save(saved_client, "A", text="db")

        renamed = await saved_client.patch(f"/queries/{created['id']}", json={"name": "B"})
        replaced = await saved_client.patch(
            f"/queries/{created['id']}", json={"query": body(text="neuf")}
        )

        assert renamed.json()["query"]["text"] == "db"
        assert (replaced.json()["name"], replaced.json()["query"]["text"]) == ("B", "neuf")
        assert replaced.json()["created_by"] == "local-developer"

    async def test_delete_is_a_204_and_the_elements_it_found_stay(
        self, saved_client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        element = await service.create_element(element_type="node", name="db-01")  # type: ignore[arg-type]
        created = await save(saved_client)

        assert (await saved_client.delete(f"/queries/{created['id']}")).status_code == 204
        assert (await saved_client.get(f"/queries/{created['id']}")).status_code == 404
        assert (await service.get_element(element.id)).name == "db-01"


class TestRunning:
    async def test_a_run_is_a_page_of_elements(
        self, saved_client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type="node", name="a")  # type: ignore[arg-type]
        await service.create_element(element_type="node", name="b")  # type: ignore[arg-type]
        created = await save(saved_client)

        page = (
            await saved_client.get(f"/queries/{created['id']}/elements", params={"limit": 1})
        ).json()

        assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (2, 1, 0, 1)

    async def test_a_package_that_is_gone_is_a_422_unknown_package_and_the_query_stays(
        self, saved_client: httpx.AsyncClient, packages: InMemoryPackages
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        created = await save(saved_client, package_id=str(finance.id))
        await packages.delete(finance.id, into=None)

        response = await saved_client.get(f"/queries/{created['id']}/elements")

        assert (response.status_code, response.json()["error"]) == (422, "unknown_package")
        assert (await saved_client.get(f"/queries/{created['id']}")).status_code == 200

    async def test_a_stored_query_that_cannot_be_read_is_a_422_invalid_query_and_the_list_survives(
        self, saved_client: httpx.AsyncClient, saved_queries: InMemorySavedQueries
    ) -> None:
        created = await save(saved_client)
        saved_queries.corrupt.add(UUID(created["id"]))

        run = await saved_client.get(f"/queries/{created['id']}/elements")

        assert (run.status_code, run.json()["error"]) == (422, "invalid_query")
        assert (await saved_client.get("/queries")).status_code == 200

    async def test_a_page_outside_the_bounds_is_a_422(self, saved_client: httpx.AsyncClient) -> None:
        created = await save(saved_client)

        response = await saved_client.get(
            f"/queries/{created['id']}/elements", params={"limit": 0}
        )

        assert response.status_code == 422


class TestWhoMayDoWhat:
    async def test_a_reader_lists_reads_and_runs_but_cannot_write(
        self,
        service: ArchitectureService,
        packages: InMemoryPackages,
        saved_queries: InMemorySavedQueries,
        nobody_calling: None,
    ) -> None:
        verifier = StaticVerifier({"ed": an_editor(), "rd": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            packages=packages,  # type: ignore[arg-type]
            queries=saved_queries,  # type: ignore[arg-type]
            verifier=verifier,
        )
        editor, reader = {"Authorization": "Bearer ed"}, {"Authorization": "Bearer rd"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = (
                await client.post("/queries", json={"name": "A", "query": body()}, headers=editor)
            ).json()

            for path in ("/queries", f"/queries/{created['id']}", f"/queries/{created['id']}/elements"):
                assert (await client.get(path, headers=reader)).status_code == 200
            for request in (
                client.post("/queries", json={"name": "B", "query": body()}, headers=reader),
                client.patch(f"/queries/{created['id']}", json={"name": "C"}, headers=reader),
                client.delete(f"/queries/{created['id']}", headers=reader),
            ):
                response = await request
                assert (response.status_code, response.json()["error"]) == (403, "forbidden")
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/e2e/test_query_api.py tests/unit/test_query_schema.py -x`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'queries'`.

- [ ] **Step 3: Schémas** — `api/schemas.py`, à la suite de la section « Structured search » :

```python
# --- Saved queries -------------------------------------------------------------
# Shared by every reader; `created_by` is read from the caller, so the body has
# no such field (and `extra="forbid"` turns an attempt to send one into a 422).


class SavedQueryCreate(_Input):
    name: Name
    description: Description = ""
    query: ElementQuerySchema


class SavedQueryUpdate(_Input):
    """A partial update. An omitted field keeps its stored value."""

    name: Name | None = None
    description: Description | None = None
    query: ElementQuerySchema | None = None


class SavedQuerySummaryRead(BaseModel):
    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: SavedQuerySummary) -> SavedQuerySummaryRead:
        return cls(
            id=summary.id,
            name=summary.name,
            description=summary.description,
            created_by=summary.created_by,
            created_at=summary.created_at,
            updated_at=summary.updated_at,
        )


class SavedQueryRead(SavedQuerySummaryRead):
    query: ElementQuerySchema

    @classmethod
    def read(cls, saved: SavedQuery) -> SavedQueryRead:
        return cls(**SavedQuerySummaryRead.of(saved.summary).model_dump(), query=ElementQuerySchema.of(saved.query))
```

(`from ea.domain.saved_queries import SavedQuery, SavedQuerySummary` aux imports ; la méthode de construction de `SavedQueryRead` s'appelle `read` pour ne pas masquer `SavedQuerySummaryRead.of` avec une signature incompatible — mypy l'exige.)

- [ ] **Step 4: Erreurs, dépendance, câblage**

`api/errors.py` : importer `DuplicateSavedQueryError, SavedQueryNotFoundError` ; dans `_STATUS` (sous `InvalidQueryError`) :

```python
    SavedQueryNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateSavedQueryError: (status.HTTP_409_CONFLICT, "duplicate"),
```

`api/dependencies.py` :

```python
def saved_query_service_of(app: FastAPI) -> SavedQueryService:
    """The saved-query use cases the lifespan attached, or a clear failure.

    Absent means the relational store is shut, and the queries live in it: a
    wiring fault and a 500, never an empty list a client could take for "none saved".
    """
    service: SavedQueryService | None = getattr(app.state, "saved_query_service", None)
    if service is None:
        msg = "no saved-query service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_saved_query_service(request: Request) -> SavedQueryService:
    return saved_query_service_of(request.app)


SavedQueries = Annotated[SavedQueryService, Depends(get_saved_query_service)]
```

(import `from ea.services.saved_queries import SavedQueryService`.)

`api/queries.py` : compléter les imports (`Annotated`, `UUID`, `Query`, `Response`, `SavedQueries`, les schémas, `Limit`, `Offset`) et ajouter :

```python
LimitQuery = Annotated[Limit, Query()]
OffsetQuery = Annotated[Offset, Query()]
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/queries", response_model=list[SavedQuerySummaryRead])
async def list_saved_queries(queries: SavedQueries) -> list[SavedQuerySummaryRead]:
    """The shared, named filters. The queries themselves are not read for a list."""
    return [SavedQuerySummaryRead.of(summary) for summary in await queries.list_queries()]


@router.post(
    "/queries", response_model=SavedQueryRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def create_saved_query(body: SavedQueryCreate, queries: SavedQueries) -> SavedQueryRead:
    """Keep a filter under a name, for the whole team. The author is the caller."""
    saved = await queries.create(
        name=body.name, description=body.description, query=body.query.to_domain()
    )
    return SavedQueryRead.read(saved)


@router.get("/queries/{query_id}", response_model=SavedQueryRead, responses=REJECTED)
async def read_saved_query(query_id: UUID, queries: SavedQueries) -> SavedQueryRead:
    return SavedQueryRead.read(await queries.get(query_id))


@router.patch("/queries/{query_id}", response_model=SavedQueryRead, responses=REJECTED)
async def update_saved_query(
    query_id: UUID, body: SavedQueryUpdate, queries: SavedQueries
) -> SavedQueryRead:
    saved = await queries.update(
        query_id,
        name=body.name,
        description=body.description,
        query=body.query.to_domain() if body.query is not None else None,
    )
    return SavedQueryRead.read(saved)


@router.delete("/queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
async def delete_saved_query(query_id: UUID, queries: SavedQueries) -> Response:
    """Delete the query — not the elements it finds."""
    await queries.delete(query_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/queries/{query_id}/elements", response_model=ElementPage, responses=REJECTED)
async def run_saved_query(
    query_id: UUID, queries: SavedQueries, limit: LimitQuery = 50, offset: OffsetQuery = 0
) -> ElementPage:
    """Run a saved query, paged. 422 `unknown_package` if its package has since been deleted."""
    result = await queries.run(query_id, limit=limit, offset=offset)
    return ElementPage(
        items=[ElementRead.of(element) for element in result.elements],
        total=result.total,
        limit=limit,
        offset=offset,
    )
```

`main.py` : imports `SavedQueryRepository` (ports), `PostgresSavedQueryRepository`, `SavedQueryService` ; `_lifespan(..., queries: SavedQueryRepository | None = None)` ; dans le lifespan `query_store = queries`, dans `if settings.postgres_enabled:` `if query_store is None: query_store = PostgresSavedQueryRepository(app.state.db_sessions)`, puis, à côté du service de paquets :

```python
            if query_store is not None:
                app.state.saved_query_service = SavedQueryService(
                    query_store, package_store, architecture_service_of(app)
                )
```

`create_app(..., queries: SavedQueryRepository | None = None)` → `_lifespan(..., queries=queries)` et, dans le bloc `if architecture_service is not None:` :

```python
        if queries is not None:
            app.state.saved_query_service = SavedQueryService(
                queries, packages, architecture_service
            )
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q && uv run mypy src && uv run ruff check src tests`
Expected: PASS ; `test_error_statuses.py` couvre les deux dernières erreurs.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/api backend/src/ea/main.py backend/tests/e2e/test_query_api.py backend/tests/unit/test_query_schema.py
git commit -m "feat(api): save, share and run named queries" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 25: Clôture du lot 4

**Files:**
- Modify (générés) : `backend/openapi.json`, `frontend/src/api/schema.d.ts`
- Modify: `docs/adr/00NN-le-filtre-d-elements-objet-de-domaine-pur.md` (précise `saved_queries`)

- [ ] **Step 1: Régénérer le contrat**

Run: `make openapi && grep -c "SavedQueryRead" backend/openapi.json && grep -c "ElementQuerySchema-" backend/openapi.json`
Expected: `SavedQueryRead` y figure ; le second `grep` affiche `0` (un seul composant `ElementQuerySchema`, jamais scindé).

- [ ] **Step 2: Porte qualité et intégration**

Run: `make check && make test-integration`
Expected: vert. L'intégration rejoue la chaîne complète `0001 → 0010` puis `base`.

- [ ] **Step 3: Compléter l'ADR du filtre** — ajouter, dans le tableau de la section « Décision » de l'ADR du filtre, ces deux lignes, et un paragraphe dans « Conséquences » :

```markdown
| Requêtes enregistrées | Table `saved_queries` (migration `0010`) : `query` est le JSON versionné de `to_json`, **sans clé étrangère** (un paquet y est désigné par son identifiant). Partagées : tout lecteur les exécute, tout éditeur les modifie, `created_by` est celui de l'appelant et n'est jamais lu d'un corps. |
| Relecture | Une requête relue repasse par `from_json` : version inconnue ou valeur que les énumérations ne connaissent plus → `422 invalid_query`. Un paquet disparu → `422 unknown_package` à l'exécution ; la requête reste lisible et modifiable. La liste ne lit jamais les requêtes : une ligne illisible ne cache pas les autres. |
```

```markdown
- Une requête enregistrée est de la donnée qui survit au code : toute évolution
  incompatible du filtre passe par `version: 2` et un lecteur qui sait refuser
  (ou migrer) la version 1 ; la table n'a pas besoin de migration pour cela.
- Les requêtes sont partagées et sans propriétaire privé : « privé » serait un
  ajout ultérieur (colonne et règle dans `SavedQueryService`).
```

- [ ] **Step 4: Commit**

```bash
git add backend/openapi.json frontend/src/api/schema.d.ts docs/adr
git commit -m "docs(adr): record the saved queries and regenerate the client" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# LOT 5 — La section Recherche

### Task 26: L'état d'une requête, la recherche et les requêtes enregistrées (composables)

**Files:**
- Create: `frontend/src/features/search/queryState.ts`, `frontend/src/features/search/useElementSearch.ts`, `frontend/src/features/search/useSavedQueries.ts`
- Modify: `frontend/tests/support/api.ts` (ajout de `aSavedQuery`, `aSavedQuerySummary`)
- Test: `frontend/tests/queryState.spec.ts`, `frontend/tests/useElementSearch.spec.ts`, `frontend/tests/useSavedQueries.spec.ts`

**Interfaces:**
- Consumes: client généré (`ElementQuerySchema`, `PropertyTestSchema`, `RelationTestSchema`, `ElementPage`, `ElementRead`, `SavedQueryRead`, `SavedQuerySummaryRead`, `SavedQueryUpdate`) ; `POST /elements/search`, `/queries…` (Tasks 19, 24) ; `useLatestRequest`.
- Produces:
  - `queryState.ts` : `type Query`, `type PropertyTest`, `type RelationTest`, `MAX_URL_QUERY = 2000`, `PAGE_SIZE = 25`, `emptyQuery(): Query`, `cloneQuery(query: Query): Query`, `encodeQuery(query: Query): string | null` (JSON avec `offset` à 0 ; `null` au-delà de 2 000 caractères), `decodeQuery(raw: string): Query | null` (`null` si ce n'est pas un objet `version: 1` ; les champs absents sont complétés par `emptyQuery()`).
  - `useElementSearch()` : `{ items: Ref<ElementRead[]>, total: Ref<number>, status, error, run(query: Query): Promise<void>, runSaved(id: string, limit: number, offset: number): Promise<void>, clear(): void }` — une défaillance (422…) atterrit dans `error`, elle n'est pas levée.
  - `useSavedQueries()` : `{ queries: Ref<SavedQuerySummaryRead[]>, opened: Ref<SavedQueryRead | null>, status, error, openStatus, openError, load(), open(id), close(), create(name: string, description: string, query: Query): Promise<SavedQueryRead>, update(id: string, body: SavedQueryUpdate): Promise<SavedQueryRead>, remove(id: string): Promise<void> }`.
  - Doublures : `aSavedQuerySummary(overrides?)`, `aSavedQuery(overrides?)`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `frontend/tests/support/api.ts` (types `SavedQueryRead`, `SavedQuerySummaryRead` en tête, et `emptyQuery` importé de `../../src/features/search/queryState`) :

```ts
/** A saved query as `GET /queries` lists it — no filter in it. */
export function aSavedQuerySummary(
  overrides: Partial<SavedQuerySummaryRead> = {},
): SavedQuerySummaryRead {
  return {
    id: 'qqqqqqqq-1111-4111-8111-111111111111',
    name: 'Les noeuds',
    description: '',
    created_by: 'nicolas',
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
    ...overrides,
  }
}

/** The same query opened — with its filter. */
export function aSavedQuery(overrides: Partial<SavedQueryRead> = {}): SavedQueryRead {
  return { ...aSavedQuerySummary(), query: emptyQuery(), ...overrides }
}
```

`frontend/tests/queryState.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import {
  MAX_URL_QUERY,
  cloneQuery,
  decodeQuery,
  emptyQuery,
  encodeQuery,
} from '../src/features/search/queryState'

describe('emptyQuery', () => {
  it('is a version-1 query that filters nothing, with every field the API requires', () => {
    const query = emptyQuery()

    expect(query.version).toBe(1)
    expect(Object.keys(query).sort()).toEqual(
      [
        'element_types', 'include_subpackages', 'layers', 'limit', 'offset', 'order',
        'package_id', 'properties', 'relations', 'root_only', 'text', 'version',
      ].sort(),
    ) // fmt: skip
    expect(query.limit).toBe(25)
  })

  it('is a new object each time, so editing one never edits another', () => {
    const one = emptyQuery()
    one.layers.push('business')

    expect(emptyQuery().layers).toEqual([])
  })
})

describe('the URL form', () => {
  it('round-trips a query, the page left out', () => {
    const query = { ...emptyQuery(), text: 'facturation', offset: 50 }

    const raw = encodeQuery(query)

    expect(raw).not.toBeNull()
    expect(decodeQuery(raw as string)).toEqual({ ...query, offset: 0 })
  })

  it('refuses to put more than the limit in an address, and says so with null', () => {
    const long = { ...emptyQuery(), text: 'x'.repeat(MAX_URL_QUERY) }

    expect(encodeQuery(long)).toBeNull()
  })

  it('completes a hand-written short query with the defaults', () => {
    const query = decodeQuery('{"version":1,"text":"db"}')

    expect(query).toEqual({ ...emptyQuery(), text: 'db' })
  })

  it.each(['', 'pas du json', '3', 'null', '[1]', '{"version":2}', '{"text":"db"}'])(
    'answers null for %j rather than throwing',
    (raw) => {
      expect(decodeQuery(raw)).toBeNull()
    },
  )

  it('clones without sharing', () => {
    const query = emptyQuery()
    const copy = cloneQuery(query)
    copy.layers.push('business')

    expect(query.layers).toEqual([])
  })
})
```

`frontend/tests/useElementSearch.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { emptyQuery } from '../src/features/search/queryState'
import { useElementSearch } from '../src/features/search/useElementSearch'
import { aPage, anElement, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useElementSearch', () => {
  it('posts the query as it is and keeps the page and the total', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/elements/search', body: aPage([anElement({ name: 'Billing' })], 42) },
    ])
    const search = useElementSearch()
    const query = { ...emptyQuery(), text: 'bill' }

    await search.run(query)

    expect(calls[0].body).toEqual(query)
    expect(search.items.value.map((e) => e.name)).toEqual(['Billing'])
    expect(search.total.value).toBe(42)
    expect(search.status.value).toBe('ready')
  })

  it('runs a saved query through its own endpoint, paged', async () => {
    const calls = stubApi([{ path: '/queries/q1/elements', body: aPage([anElement()], 3) }])
    const search = useElementSearch()

    await search.runSaved('q1', 25, 50)

    expect(calls[0].url.searchParams.get('limit')).toBe('25')
    expect(calls[0].url.searchParams.get('offset')).toBe('50')
    expect(search.total.value).toBe(3)
  })

  it('keeps a refusal as a message and forgets the previous results', async () => {
    stubApi([{ method: 'POST', path: '/elements/search', body: aPage([anElement()]) }])
    const search = useElementSearch()
    await search.run(emptyQuery())
    stubApi([
      {
        method: 'POST',
        path: '/elements/search',
        status: 422,
        body: { error: 'invalid_input', detail: 'limit is between 1 and 200' },
      },
    ])

    await search.run(emptyQuery())

    expect(search.error.value).toBe('limit is between 1 and 200')
    expect(search.status.value).toBe('error')
    expect(search.items.value).toEqual([])
  })

  it('shows the answer to the last question, whichever arrives first', async () => {
    const calls = deferApi()
    const search = useElementSearch()

    const first = search.run({ ...emptyQuery(), text: 'a' })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = search.run({ ...emptyQuery(), text: 'ab' })
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aPage([anElement({ name: 'Récent' })]))
    calls[0].answer(aPage([anElement({ name: 'Ancien' })], 9))
    await Promise.all([first, second])

    expect(search.items.value.map((e) => e.name)).toEqual(['Récent'])
    expect(search.total.value).toBe(1)
  })

  it('clears what it shows and drops the request in flight', async () => {
    const calls = deferApi()
    const search = useElementSearch()
    const pending = search.run(emptyQuery())
    await vi.waitFor(() => expect(calls).toHaveLength(1))

    search.clear()
    await pending

    expect(calls[0].aborted()).toBe(true)
    expect(search.status.value).toBe('idle')
  })
})
```

`frontend/tests/useSavedQueries.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { emptyQuery } from '../src/features/search/queryState'
import { useSavedQueries } from '../src/features/search/useSavedQueries'
import { aSavedQuery, aSavedQuerySummary, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const LIST = { path: '/queries', body: [aSavedQuerySummary()] }

describe('useSavedQueries', () => {
  it('lists the shared queries', async () => {
    stubApi([LIST])
    const store = useSavedQueries()

    await store.load()

    expect(store.queries.value.map((q) => q.name)).toEqual(['Les noeuds'])
  })

  it('saves the current filter under a name then reloads the list', async () => {
    const calls = stubApi([
      LIST,
      { method: 'POST', path: '/queries', status: 201, body: aSavedQuery({ id: 'new' }) },
    ])
    const query = { ...emptyQuery(), text: 'db' }

    const created = await useSavedQueries().create('Les noeuds', 'Tous', query)

    expect(created.id).toBe('new')
    expect(calls.map((c) => c.method)).toEqual(['POST', 'GET'])
    expect(calls[0].body).toEqual({ name: 'Les noeuds', description: 'Tous', query })
  })

  it('raises a refusal — a name already taken — for the form to show', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/queries',
        status: 409,
        body: { error: 'duplicate', detail: 'a saved query is already named Les noeuds' },
      },
    ])

    await expect(useSavedQueries().create('Les noeuds', '', emptyQuery())).rejects.toThrow(
      'already named',
    )
  })

  it('opens one query with its filter', async () => {
    stubApi([{ path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) }])
    const store = useSavedQueries()

    await store.open('q1')

    expect(store.opened.value?.id).toBe('q1')
    expect(store.openStatus.value).toBe('ready')
  })

  it('keeps an unreadable stored query as a message, not a crash', async () => {
    stubApi([
      {
        path: '/queries/q1',
        status: 422,
        body: { error: 'invalid_query', detail: 'unsupported query version: 2' },
      },
    ])
    const store = useSavedQueries()

    await store.open('q1')

    expect(store.openError.value).toBe('unsupported query version: 2')
    expect(store.opened.value).toBeNull()
  })

  it('renames, replaces the filter and deletes, reloading each time', async () => {
    const calls = stubApi([
      LIST,
      { method: 'PATCH', path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) },
      { method: 'DELETE', path: '/queries/q1', status: 204 },
    ])
    const store = useSavedQueries()

    await store.update('q1', { name: 'Autre' })
    await store.update('q1', { query: emptyQuery() })
    await store.remove('q1')

    expect(calls.filter((c) => c.method === 'PATCH').map((c) => Object.keys(c.body as object))).toEqual([
      ['name'],
      ['query'],
    ])
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(3)
  })

  it('opens the last one asked for, whichever answer arrives first', async () => {
    const calls = deferApi()
    const store = useSavedQueries()

    const first = store.open('a')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = store.open('b')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aSavedQuery({ id: 'b' }))
    calls[0].answer(aSavedQuery({ id: 'a' }))
    await Promise.all([first, second])

    expect(store.opened.value?.id).toBe('b')
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/queryState.spec.ts tests/useElementSearch.spec.ts tests/useSavedQueries.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/search/queryState"`.

- [ ] **Step 3: Implémenter** — `frontend/src/features/search/queryState.ts`

```ts
// A search, as the browser holds it and as an address carries it.
//
// The shape is the API's (`ElementQuerySchema`, generated); nothing is
// restated except the parts of the address: `?q=` carries the JSON, bounded,
// and a query that does not fit is the cue to save it instead.
import type { components } from '../../api/schema'

export type Query = components['schemas']['ElementQuerySchema']
export type PropertyTest = components['schemas']['PropertyTestSchema']
export type RelationTest = components['schemas']['RelationTestSchema']

/** The longest `?q=` worth sharing in a link; past it, "Enregistrer" is the way to share. */
export const MAX_URL_QUERY = 2000
export const PAGE_SIZE = 25

/** The query that filters nothing. Every field is required by the API, so every field is here. */
export function emptyQuery(): Query {
  return {
    version: 1,
    element_types: [],
    layers: [],
    text: null,
    package_id: null,
    include_subpackages: true,
    root_only: false,
    properties: [],
    relations: [],
    order: 'name',
    limit: PAGE_SIZE,
    offset: 0,
  }
}

/** A query is plain data: a JSON round trip is a deep copy. */
export function cloneQuery(query: Query): Query {
  return JSON.parse(JSON.stringify(query)) as Query
}

/**
 * The form a link carries, or `null` when it is too long for one.
 *
 * The page is left out: where in the results a reader was is not part of the
 * question they would send to a colleague.
 */
export function encodeQuery(query: Query): string | null {
  const json = JSON.stringify({ ...query, offset: 0 })
  return json.length <= MAX_URL_QUERY ? json : null
}

/**
 * A query read from an address, or `null` for anything that is not a version-1 object.
 *
 * Absent fields take their defaults, so a short hand-written link works. What a
 * value *means* is the API's to judge: a link carrying nonsense gets its 422.
 */
export function decodeQuery(raw: string): Query | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return null
    }
    if ((parsed as { version?: unknown }).version !== 1) {
      return null
    }
    return { ...emptyQuery(), ...(parsed as Partial<Query>) }
  } catch {
    return null
  }
}
```

`frontend/src/features/search/useElementSearch.ts`

```ts
// The results of a search: one page of elements, and the total of the whole.
//
// A failure — a filter the API refuses — lands in `error` rather than being
// thrown: the person fixes the filter on the same screen. Asking again drops
// the question in flight, so the table is the answer to the last one.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { Query } from './queryState'

export type ElementRead = components['schemas']['ElementRead']
type ElementPage = components['schemas']['ElementPage']

export function useElementSearch() {
  const items = ref<ElementRead[]>([])
  const total = ref(0)
  const asking = useLatestRequest()
  const { status, error } = asking

  function show(page: ElementPage): void {
    items.value = page.items
    total.value = page.total
  }

  function forget(): void {
    items.value = []
    total.value = 0
  }

  /** A filter built on screen or read from an address. */
  async function run(query: Query): Promise<void> {
    await asking.run(
      async (signal) => unwrap(await api.POST('/elements/search', { body: query, signal })),
      show,
      forget,
    )
  }

  /** A saved query, by its own endpoint: that is where a vanished package is explained. */
  async function runSaved(id: string, limit: number, offset: number): Promise<void> {
    await asking.run(
      async (signal) =>
        unwrap(
          await api.GET('/queries/{query_id}/elements', {
            params: { path: { query_id: id }, query: { limit, offset } },
            signal,
          }),
        ),
      show,
      forget,
    )
  }

  function clear(): void {
    asking.cancel()
    forget()
  }

  return { items, total, status, error, run, runSaved, clear }
}
```

`frontend/src/features/search/useSavedQueries.ts`

```ts
// The shared, named filters: the list, the one opened, and the writes.
//
// Two questions that must not cancel each other: the list, and the query being
// opened. An opened query the API cannot read (`invalid_query`) is a message in
// `openError`; the list, which never reads a query, stays usable around it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { Query } from './queryState'

export type SavedQuerySummaryRead = components['schemas']['SavedQuerySummaryRead']
export type SavedQueryRead = components['schemas']['SavedQueryRead']
export type SavedQueryUpdate = components['schemas']['SavedQueryUpdate']

export function useSavedQueries() {
  const queries = ref<SavedQuerySummaryRead[]>([])
  const opened = ref<SavedQueryRead | null>(null)
  const listing = useLatestRequest()
  const opening = useLatestRequest()
  const { status, error } = listing

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/queries', { signal })),
      (answer) => {
        queries.value = answer
      },
    )
  }

  async function open(id: string): Promise<void> {
    // The previous query goes first: showing it under another id would show the wrong filter.
    opened.value = null
    await opening.run(
      async (signal) =>
        unwrap(
          await api.GET('/queries/{query_id}', { params: { path: { query_id: id } }, signal }),
        ),
      (answer) => {
        opened.value = answer
      },
    )
  }

  function close(): void {
    opening.cancel()
    opened.value = null
  }

  /** A refusal — a name already taken — is thrown, for the form to show. */
  async function create(name: string, description: string, query: Query): Promise<SavedQueryRead> {
    const created = unwrap(await api.POST('/queries', { body: { name, description, query } }))
    await load()
    return created
  }

  async function update(id: string, body: SavedQueryUpdate): Promise<SavedQueryRead> {
    const updated = unwrap(
      await api.PATCH('/queries/{query_id}', { params: { path: { query_id: id } }, body }),
    )
    await load()
    return updated
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/queries/{query_id}', { params: { path: { query_id: id } } }))
    await load()
  }

  return {
    queries,
    opened,
    status,
    error,
    openStatus: opening.status,
    openError: opening.error,
    load,
    open,
    close,
    create,
    update,
    remove,
  }
}
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/queryState.spec.ts tests/useElementSearch.spec.ts tests/useSavedQueries.spec.ts && npm run typecheck && npm run lint`
Expected: PASS ; typecheck propre (le client généré de la Task 25 contient les composants utilisés).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search frontend/tests/queryState.spec.ts frontend/tests/useElementSearch.spec.ts frontend/tests/useSavedQueries.spec.ts frontend/tests/support/api.ts
git commit -m "feat(spa): add the query state and the search composables" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 27: Le constructeur de requête (`QueryBuilder.vue`)

**Files:**
- Create: `frontend/src/features/search/QueryBuilder.vue`
- Test: `frontend/tests/QueryBuilder.spec.ts`

**Interfaces:**
- Consumes: `Query`, `cloneQuery`, `emptyQuery` (Task 26) ; `useMetamodel()` (`byLayer`, `layers`, `byStrength`, `elementTypes`) ; `RELATIONSHIP_LABELS`, `LAYER_LABELS` ; `PackageRead`, `ROOT` ; `debounce`.
- Produces: composant `QueryBuilder` — props `{ modelValue: Query; metamodel: ReturnType<typeof useMetamodel>; packages: PackageRead[] }` ; événements `update:modelValue(query: Query)` (toujours un nouvel objet) et `submit()` ; `<form role="search" aria-label="Construire une recherche">`. Les types, couches et types de relation proposés viennent de `/metamodel` : aucune liste dans le composant.

- [ ] **Step 1: Écrire le test qui échoue** — `frontend/tests/QueryBuilder.spec.ts`

```ts
import { fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMetamodel } from '../src/features/metamodel/useMetamodel'
import { emptyQuery, type Query } from '../src/features/search/queryState'
import QueryBuilder from '../src/features/search/QueryBuilder.vue'
import { METAMODEL, aPackage, stubApi } from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })

async function show(query: Query = emptyQuery()) {
  stubApi([{ path: '/metamodel', body: METAMODEL }])
  const metamodel = useMetamodel()
  await metamodel.load()
  const view = render(QueryBuilder, {
    props: { modelValue: query, metamodel, packages: [FINANCE] },
  })
  const last = () => view.emitted()['update:modelValue'].at(-1)?.[0] as Query
  return { ...view, last }
}

describe('QueryBuilder — what it offers comes from the metamodel', () => {
  it('lists the element types by layer and the layers, as /metamodel gave them', async () => {
    await show()

    const types = screen.getByLabelText(/types d'éléments/i)
    expect(Array.from(types.querySelectorAll('option')).map((o) => o.textContent?.trim())).toEqual([
      'Business Process',
      'Application Component',
    ])
    expect(screen.getByRole('checkbox', { name: 'Métier' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Application' })).toBeInTheDocument()
  })

  it('emits a new query when a type or a layer is chosen, leaving the original alone', async () => {
    const original = emptyQuery()
    const { last } = await show(original)

    const select = screen.getByLabelText(/types d'éléments/i)
    const option = within(select).getByRole('option', { name: 'Application Component' })
    ;(option as HTMLOptionElement).selected = true
    await fireEvent.change(select)
    expect(last().element_types).toEqual(['application_component'])

    await fireEvent.click(screen.getByRole('checkbox', { name: 'Métier' }))
    expect(last().layers).toEqual(['business'])
    expect(original.element_types).toEqual([])
  })
})

describe('QueryBuilder — the text', () => {
  it('waits for a pause in the typing before it asks, and a blank text is none', async () => {
    // Faked only once the palette has loaded: the stubbed responses need real timers.
    const { emitted, last } = await show()
    vi.useFakeTimers()

    await fireEvent.update(screen.getByLabelText(/contient/i), 'bill')
    await fireEvent.update(screen.getByLabelText(/contient/i), 'billing')
    expect(emitted()['update:modelValue']).toBeUndefined()

    vi.advanceTimersByTime(300)
    expect(last().text).toBe('billing')

    await fireEvent.update(screen.getByLabelText(/contient/i), '   ')
    vi.advanceTimersByTime(300)
    expect(last().text).toBeNull()
  })
})

describe('QueryBuilder — the package', () => {
  it('chooses a package, the unfiled, or everything', async () => {
    const { last } = await show()
    const choice = screen.getByLabelText(/^paquet$/i)

    await fireEvent.update(choice, 'f')
    expect(last()).toMatchObject({ package_id: 'f', root_only: false })

    await fireEvent.update(choice, 'root')
    expect(last()).toMatchObject({ package_id: null, root_only: true })

    await fireEvent.update(choice, '')
    expect(last()).toMatchObject({ package_id: null, root_only: false })
  })

  it('offers the subpackages checkbox only once a package is chosen', async () => {
    const { last } = await show({ ...emptyQuery(), package_id: 'f' })

    await fireEvent.click(screen.getByRole('checkbox', { name: /sous-paquets/i }))

    expect(last().include_subpackages).toBe(false)
  })

  it('hides it when no package is chosen', async () => {
    await show()

    expect(screen.queryByRole('checkbox', { name: /sous-paquets/i })).toBeNull()
  })
})

describe('QueryBuilder — the attribute tests', () => {
  it('adds a test, changes its operator, and drops the value an exists test cannot have', async () => {
    const { last } = await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de propriété/i }))
    expect(last().properties).toEqual([{ key: '', op: 'eq', value: '' }])

    await fireEvent.update(screen.getByLabelText(/nom de la propriété 1/i), 'owner')
    await fireEvent.update(screen.getByLabelText(/valeur de la propriété 1/i), 'finance')
    expect(last().properties).toEqual([{ key: 'owner', op: 'eq', value: 'finance' }])

    await fireEvent.update(screen.getByLabelText(/opérateur de la propriété 1/i), 'exists')
    expect(last().properties).toEqual([{ key: 'owner', op: 'exists', value: null }])
  })

  it('removes a test', async () => {
    const query: Query = {
      ...emptyQuery(),
      properties: [{ key: 'owner', op: 'exists', value: null }],
    }
    const { last } = await show(query)

    await fireEvent.click(screen.getByRole('button', { name: /retirer le test de propriété 1/i }))

    expect(last().properties).toEqual([])
  })
})

describe('QueryBuilder — the relation tests', () => {
  it('adds one with the strongest relationship of the metamodel, either way, present', async () => {
    const { last } = await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de relation/i }))

    expect(last().relations).toEqual([
      { relationship_type: 'composition', direction: 'any', present: true, other_type: null },
    ])
  })

  it('offers the relationships of /metamodel, in the verbs of the SPA, and the types for the far end', async () => {
    await show({
      ...emptyQuery(),
      relations: [
        { relationship_type: 'serving', direction: 'out', present: true, other_type: null },
      ],
    })

    const kind = screen.getByLabelText('Relation 1')
    expect(Array.from(kind.querySelectorAll('option')).map((o) => o.textContent?.trim())).toEqual([
      'compose',
      'sert',
    ])
    const far = screen.getByLabelText(/type de l'autre bout de la relation 1/i)
    expect(within(far).getAllByRole('option').map((o) => o.textContent?.trim())).toEqual([
      'Peu importe',
      'Business Process',
      'Application Component',
    ])
  })

  it('says has-no-such-link with the presence select, and narrows by far type', async () => {
    const query: Query = {
      ...emptyQuery(),
      relations: [
        { relationship_type: 'serving', direction: 'any', present: true, other_type: null },
      ],
    }
    const { last } = await show(query)

    await fireEvent.update(screen.getByLabelText(/présence de la relation 1/i), 'absent')
    expect(last().relations[0].present).toBe(false)

    await fireEvent.update(
      screen.getByLabelText(/type de l'autre bout de la relation 1/i),
      'business_process',
    )
    expect(last().relations[0].other_type).toBe('business_process')
  })
})

describe('QueryBuilder — submitting', () => {
  it('asks to search when the form is submitted', async () => {
    const { emitted } = await show()

    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    expect(emitted().submit).toHaveLength(1)
  })

  it('resets to the query that filters nothing', async () => {
    const { last } = await show({ ...emptyQuery(), text: 'db', layers: ['business'] })

    await fireEvent.click(screen.getByRole('button', { name: /réinitialiser/i }))

    expect(last()).toEqual(emptyQuery())
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/QueryBuilder.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/search/QueryBuilder.vue"`.

- [ ] **Step 3: Implémenter** — `frontend/src/features/search/QueryBuilder.vue`

```vue
<script setup lang="ts">
// The form that builds a structured search.
//
// It owns no state of its own beyond the text being typed: every control
// computes the next query from the one it was given and emits it, so the
// screen decides what the query is. What it offers — the types, the layers,
// the relationships — is read off `/metamodel` through the composable it is
// handed, never listed here: a list in TypeScript would be a second
// metamodel (docs/adr/0012). The few words below only *translate* wire values.
import { computed, ref, watch } from 'vue'

import { debounce } from '../../lib/debounce'
import { LAYER_LABELS, type useMetamodel } from '../metamodel/useMetamodel'
import { ROOT, type PackageRead } from '../packages/packageTree'
import { RELATIONSHIP_LABELS } from '../relationships/labels'
import { cloneQuery, emptyQuery, type PropertyTest, type Query, type RelationTest } from './queryState'

const props = defineProps<{
  modelValue: Query
  metamodel: ReturnType<typeof useMetamodel>
  packages: PackageRead[]
}>()
const emit = defineEmits<{ 'update:modelValue': [query: Query]; submit: [] }>()

/** Translations of the wire values of the API's enumerations — labels, not rules. */
const OPERATORS: Record<PropertyTest['op'], string> = {
  eq: 'est égal à',
  contains: 'contient',
  exists: 'existe',
  absent: "n'existe pas",
}
const DIRECTIONS: Record<RelationTest['direction'], string> = {
  out: 'sortante',
  in: 'entrante',
  any: 'dans un sens ou l’autre',
}
const ORDERS: Record<Query['order'], string> = {
  name: 'Nom',
  created_at: 'Création, la plus récente d’abord',
  updated_at: 'Modification, la plus récente d’abord',
}

// The form edits its own copy, so what is on screen never waits for a parent to
// hand the new query back; the parent hears each change and may replace the
// whole query (a reset, a saved query opened), which the watcher takes in.
const draft = ref<Query>(cloneQuery(props.modelValue))
watch(
  () => props.modelValue,
  (value) => {
    draft.value = cloneQuery(value)
  },
  { deep: true },
)

/** The next query: a copy of the current one, changed. The prop is never mutated. */
function edit(change: (next: Query) => void): void {
  const next = cloneQuery(draft.value)
  change(next)
  draft.value = next
  emit('update:modelValue', cloneQuery(next))
}

function reset(): void {
  draft.value = emptyQuery()
  emit('update:modelValue', emptyQuery())
}

// --- Text: a search box waits for a pause (lib/debounce.ts) ---
const text = ref(props.modelValue.text ?? '')
watch(
  () => props.modelValue.text,
  (value) => {
    text.value = value ?? ''
  },
)
const typing = debounce((value: string) => {
  edit((draft) => {
    draft.text = value.trim() === '' ? null : value
  })
}, 300)

// --- Types, layers, package ---
const types = computed({
  get: () => draft.value.element_types,
  set: (value: Query['element_types']) =>
    edit((draft) => {
      draft.element_types = value
    }),
})

function toggleLayer(layer: Query['layers'][number], on: boolean): void {
  edit((draft) => {
    draft.layers = on
      ? [...draft.layers.filter((l) => l !== layer), layer]
      : draft.layers.filter((l) => l !== layer)
  })
}

const packageChoice = computed({
  get: () => (draft.value.root_only ? ROOT : (draft.value.package_id ?? '')),
  set: (value: string) =>
    edit((draft) => {
      draft.root_only = value === ROOT
      draft.package_id = value && value !== ROOT ? value : null
    }),
})

// --- Attribute tests ---
function addProperty(): void {
  edit((draft) => {
    draft.properties.push({ key: '', op: 'eq', value: '' })
  })
}

function setProperty(index: number, patch: Partial<PropertyTest>): void {
  edit((draft) => {
    const test = draft.properties[index]
    if (!test) {
      return
    }
    Object.assign(test, patch)
    // `exists` and `absent` take no value; `eq` and `contains` need one.
    if (test.op === 'exists' || test.op === 'absent') {
      test.value = null
    } else if (test.value === null) {
      test.value = ''
    }
  })
}

function removeProperty(index: number): void {
  edit((draft) => {
    draft.properties.splice(index, 1)
  })
}

// --- Relation tests ---
function addRelation(): void {
  const first = props.metamodel.byStrength.value[0]
  if (!first) {
    return
  }
  edit((draft) => {
    draft.relations.push({
      relationship_type: first.value,
      direction: 'any',
      present: true,
      other_type: null,
    })
  })
}

function setRelation(index: number, patch: Partial<RelationTest>): void {
  edit((draft) => {
    const test = draft.relations[index]
    if (test) {
      Object.assign(test, patch)
    }
  })
}

function removeRelation(index: number): void {
  edit((draft) => {
    draft.relations.splice(index, 1)
  })
}

const sortedPackages = computed(() =>
  [...props.packages].sort((one, other) =>
    one.path.localeCompare(other.path, 'fr', { sensitivity: 'base' }),
  ),
)

function valueOf(event: Event): string {
  return (event.target as HTMLInputElement | HTMLSelectElement).value
}
</script>

<template>
  <form class="builder" role="search" aria-label="Construire une recherche" @submit.prevent="emit('submit')">
    <div class="row">
      <div class="field">
        <label for="q-text">Nom contient</label>
        <input id="q-text" v-model="text" type="search" autocomplete="off" @input="typing.call(text)" />
      </div>

      <div class="field">
        <label for="q-types">Types d'éléments</label>
        <select id="q-types" v-model="types" multiple size="6">
          <optgroup
            v-for="group in metamodel.byLayer.value"
            :key="group.layer"
            :label="LAYER_LABELS[group.layer]"
          >
            <option v-for="type in group.types" :key="type.value" :value="type.value">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </div>

      <fieldset class="field">
        <legend>Couches</legend>
        <label v-for="layer in metamodel.layers.value" :key="layer" class="check">
          <input
            type="checkbox"
            :checked="draft.layers.includes(layer)"
            @change="toggleLayer(layer, ($event.target as HTMLInputElement).checked)"
          />
          {{ LAYER_LABELS[layer] }}
        </label>
      </fieldset>

      <div class="field">
        <label for="q-package">Paquet</label>
        <select id="q-package" v-model="packageChoice">
          <option value="">Tous</option>
          <option :value="ROOT">Racine (sans paquet)</option>
          <option v-for="entry in sortedPackages" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
        <label v-if="draft.package_id" class="check">
          <input
            type="checkbox"
            :checked="draft.include_subpackages"
            @change="edit((draft) => { draft.include_subpackages = ($event.target as HTMLInputElement).checked })"
          />
          Inclure les sous-paquets
        </label>
      </div>
    </div>

    <fieldset class="tests">
      <legend>Attributs</legend>
      <div v-for="(test, index) in draft.properties" :key="index" class="test">
        <input
          :aria-label="`Nom de la propriété ${index + 1}`"
          :value="test.key"
          placeholder="propriete"
          @input="setProperty(index, { key: valueOf($event) })"
        />
        <select
          :aria-label="`Opérateur de la propriété ${index + 1}`"
          :value="test.op"
          @change="setProperty(index, { op: valueOf($event) as PropertyTest['op'] })"
        >
          <option v-for="(label, op) in OPERATORS" :key="op" :value="op">{{ label }}</option>
        </select>
        <input
          v-if="test.op === 'eq' || test.op === 'contains'"
          :aria-label="`Valeur de la propriété ${index + 1}`"
          :value="test.value ?? ''"
          @input="setProperty(index, { value: valueOf($event) })"
        />
        <button
          type="button"
          class="secondary"
          :aria-label="`Retirer le test de propriété ${index + 1}`"
          @click="removeProperty(index)"
        >
          Retirer
        </button>
      </div>
      <button type="button" @click="addProperty">Ajouter un test de propriété</button>
    </fieldset>

    <fieldset class="tests">
      <legend>Relations</legend>
      <div v-for="(test, index) in draft.relations" :key="index" class="test">
        <select
          :aria-label="`Présence de la relation ${index + 1}`"
          :value="test.present ? 'present' : 'absent'"
          @change="setRelation(index, { present: valueOf($event) === 'present' })"
        >
          <option value="present">a une relation</option>
          <option value="absent">n'a pas de relation</option>
        </select>
        <select
          :aria-label="`Relation ${index + 1}`"
          :value="test.relationship_type"
          @change="setRelation(index, { relationship_type: valueOf($event) as RelationTest['relationship_type'] })"
        >
          <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
            {{ RELATIONSHIP_LABELS[type.value] }}
          </option>
        </select>
        <select
          :aria-label="`Sens de la relation ${index + 1}`"
          :value="test.direction"
          @change="setRelation(index, { direction: valueOf($event) as RelationTest['direction'] })"
        >
          <option v-for="(label, direction) in DIRECTIONS" :key="direction" :value="direction">
            {{ label }}
          </option>
        </select>
        <select
          :aria-label="`Type de l'autre bout de la relation ${index + 1}`"
          :value="test.other_type ?? ''"
          @change="setRelation(index, { other_type: (valueOf($event) || null) as RelationTest['other_type'] })"
        >
          <option value="">Peu importe</option>
          <optgroup
            v-for="group in metamodel.byLayer.value"
            :key="group.layer"
            :label="LAYER_LABELS[group.layer]"
          >
            <option v-for="type in group.types" :key="type.value" :value="type.value">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
        <button
          type="button"
          class="secondary"
          :aria-label="`Retirer le test de relation ${index + 1}`"
          @click="removeRelation(index)"
        >
          Retirer
        </button>
      </div>
      <button type="button" :disabled="metamodel.byStrength.value.length === 0" @click="addRelation">
        Ajouter un test de relation
      </button>
    </fieldset>

    <div class="row">
      <div class="field">
        <label for="q-order">Trier par</label>
        <select
          id="q-order"
          :value="draft.order"
          @change="edit((draft) => { draft.order = valueOf($event) as Query['order'] })"
        >
          <option v-for="(label, order) in ORDERS" :key="order" :value="order">{{ label }}</option>
        </select>
      </div>
      <button type="submit">Rechercher</button>
      <button type="button" class="secondary" @click="reset">
        Réinitialiser
      </button>
    </div>
  </form>
</template>

<style scoped>
.builder {
  display: grid;
  gap: 0.8rem;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.8rem;
}
.field {
  display: grid;
  gap: 0.25rem;
  border: none;
  padding: 0;
  margin: 0;
}
label,
legend {
  font-size: 0.85rem;
  font-weight: 600;
}
.check {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-weight: 400;
}
.tests {
  display: grid;
  gap: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.6rem;
}
.test {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
input,
select {
  font: inherit;
  padding: 0.35rem 0.55rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
</style>
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/QueryBuilder.spec.ts && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/search/QueryBuilder.vue frontend/tests/QueryBuilder.spec.ts
git commit -m "feat(spa): add the structured query builder" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 28: Les requêtes enregistrées, la section Recherche et son entrée de menu

**Files:**
- Create: `frontend/src/features/search/SavedQueries.vue`, `frontend/src/features/search/SearchSection.vue`
- Modify: `frontend/src/router/sections.ts` (nouvelle entrée du groupe `analysis`, après l'analyse d'impact)
- Test: `frontend/tests/SearchSection.spec.ts`, `frontend/tests/router.spec.ts` (ajout)

**Interfaces:**
- Consumes: `QueryBuilder` (Task 27), `useElementSearch`, `useSavedQueries`, `queryState` (Task 26), `usePackages` (Task 10), `useMetamodel`, `useMe`, `useLatestRequest`.
- Produces:
  - `SavedQueries` — props `{ store: ReturnType<typeof useSavedQueries>; current: Query; activeId: string }`, événement `open(id: string)` ; liste partagée visible de tous ; Enregistrer / Renommer / Remplacer par le filtre actuel / Supprimer pour un éditeur seulement.
  - `SearchSection` — route `/recherche` (`name: 'search'`, groupe `analysis`) ; URL : `?saved=<id>` ou `?q=<JSON ≤ 2000 caractères>` ; résultats paginés (25 par page) ; lignes liées au catalogue (`/elements?element=<id>`).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `frontend/tests/router.spec.ts`, dans le `describe('the application router', …)` :

```ts
  it('routes the search under /recherche, in the analysis group', async () => {
    const app = router()

    await app.push('/recherche')

    expect(app.currentRoute.value.name).toBe('search')
    expect(SECTIONS.find((section) => section.name === 'search')?.group).toBe('analysis')
  })
```

`frontend/tests/SearchSection.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import { decodeQuery, emptyQuery, type Query } from '../src/features/search/queryState'
import SearchSection from '../src/features/search/SearchSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import {
  METAMODEL,
  aPackage,
  aPage,
  aSavedQuery,
  aSavedQuerySummary,
  anElement,
  stubApi,
  type Route,
} from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const BILLING = anElement({ id: 'e1', name: 'Facturation' })
const SEARCH = (items = [BILLING], total = items.length): Route => ({
  method: 'POST',
  path: '/elements/search',
  body: aPage(items, total),
})
const BASE: Route[] = [
  { path: '/metamodel', body: METAMODEL },
  { path: '/packages', body: [aPackage({ id: 'f', name: 'Finance', path: 'Finance' })] },
  { path: '/queries', body: [aSavedQuerySummary({ id: 'q1', name: 'Les noeuds' })] },
]

async function open(routes: Route[], query = ''): Promise<{ router: Router; calls: ReturnType<typeof stubApi> }> {
  const calls = stubApi([...routes, ...BASE])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/recherche${query}`)
  await router.isReady()
  render(SearchSection, { global: { plugins: [router] } })
  return { router, calls }
}

function asAReader(): void {
  const reader = {
    me: ref({ username: 'reader', can_write: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  }
  // Only the saved-queries panel asks: the screen leaves writing to it.
  vi.mocked(useMe).mockReturnValueOnce(reader)
}

const q = (changes: Partial<Query>) => encodeURIComponent(JSON.stringify({ ...emptyQuery(), ...changes }))

describe('SearchSection — the address is the question', () => {
  it('asks nothing until there is a question', async () => {
    const { calls } = await open([])

    expect(await screen.findByText(/définis un filtre/i)).toBeInTheDocument()
    expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(false)
  })

  it('runs the query the address carries and lists what it finds', async () => {
    const { calls } = await open([SEARCH()], `?q=${q({ text: 'fact' })}`)

    const row = await screen.findByRole('row', { name: /Facturation/ })
    expect(within(row).getByRole('link', { name: 'Facturation' }).getAttribute('href')).toContain(
      'element=e1',
    )
    const sent = calls.find((call) => call.url.pathname === '/elements/search')
    expect((sent?.body as Query).text).toBe('fact')
    expect(screen.getByText(/1 résultat/i)).toBeInTheDocument()
  })

  it('says an unreadable address is unreadable and asks nothing', async () => {
    const { calls } = await open([SEARCH()], '?q=pas-du-json')

    expect(await screen.findByRole('alert')).toHaveTextContent(/adresse/i)
    expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(false)
  })

  it('writes the question into the address when it is submitted, and runs it', async () => {
    const { router, calls } = await open([SEARCH()])
    await screen.findByText(/définis un filtre/i)

    await fireEvent.click(screen.getByRole('checkbox', { name: 'Métier' }))
    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    await waitFor(() => expect(router.currentRoute.value.query.q).toBeDefined())
    const written = decodeQuery(String(router.currentRoute.value.query.q))
    expect(written?.layers).toEqual(['business'])
    await waitFor(() =>
      expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(true),
    )
  })

  it('does not put a query too long for a link in the address, and proposes to save it', async () => {
    const { router, calls } = await open([SEARCH()])
    await screen.findByText(/définis un filtre/i)

    // Typed text is debounced: wait the pause out for real rather than fake the clock.
    await fireEvent.update(screen.getByLabelText(/nom contient/i), 'x'.repeat(2100))
    await new Promise((resolve) => setTimeout(resolve, 350))
    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    expect(await screen.findByText(/trop long pour une adresse/i)).toBeInTheDocument()
    expect(router.currentRoute.value.query.q).toBeUndefined()
    expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(true)
  })

  it('pages through the results with the query unchanged', async () => {
    const many = Array.from({ length: 25 }, (_, i) => anElement({ id: `e${i}`, name: `Élément ${i}` }))
    const { calls } = await open([SEARCH(many, 60)], `?q=${q({})}`)
    await screen.findByText('Élément 0')

    await fireEvent.click(screen.getByRole('button', { name: /page suivante/i }))

    await waitFor(() => {
      const sent = calls.filter((call) => call.url.pathname === '/elements/search').at(-1)
      expect((sent?.body as Query).offset).toBe(25)
    })
    expect(screen.getByText(/page 2 sur 3/i)).toBeInTheDocument()
  })

  it('shows the API’s refusal of a filter and leaves the form to fix it', async () => {
    await open(
      [
        {
          method: 'POST',
          path: '/elements/search',
          status: 422,
          body: { error: 'invalid_input', detail: 'the contains test on owner needs a value' },
        },
      ],
      `?q=${q({})}`,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('needs a value')
    expect(screen.getByRole('search', { name: /construire une recherche/i })).toBeInTheDocument()
  })
})

describe('SearchSection — saved queries', () => {
  const SAVED = aSavedQuery({
    id: 'q1',
    name: 'Les noeuds',
    query: { ...emptyQuery(), text: 'db', element_types: ['node'] },
  })

  it('opens a saved query in the form and runs it by its own endpoint', async () => {
    const { calls } = await open(
      [
        { path: '/queries/q1', body: SAVED },
        { path: '/queries/q1/elements', body: aPage([anElement({ name: 'db-01' })]) },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('row', { name: /db-01/ })).toBeInTheDocument()
    expect(screen.getByLabelText(/nom contient/i)).toHaveValue('db')
    expect(calls.some((call) => call.url.pathname === '/queries/q1/elements')).toBe(true)
  })

  it('explains a vanished package and keeps the query editable', async () => {
    await open(
      [
        { path: '/queries/q1', body: SAVED },
        {
          path: '/queries/q1/elements',
          status: 422,
          body: {
            error: 'unknown_package',
            detail: "the query 'Les noeuds' names the package x, which no longer exists",
          },
        },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('no longer exists')
    expect(screen.getByLabelText(/nom contient/i)).toHaveValue('db')
  })

  it('says a stored query that cannot be read cannot be read, without hiding the list', async () => {
    await open(
      [
        {
          path: '/queries/q1',
          status: 422,
          body: { error: 'invalid_query', detail: 'unsupported query version: 2' },
        },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('unsupported query version')
    expect(screen.getByRole('button', { name: 'Les noeuds' })).toBeInTheDocument()
  })

  it('opening one from the list puts it in the address', async () => {
    const { router } = await open([
      { path: '/queries/q1', body: SAVED },
      { path: '/queries/q1/elements', body: aPage([]) },
    ])
    await screen.findByText(/définis un filtre/i)

    await fireEvent.click(await screen.findByRole('button', { name: 'Les noeuds' }))

    await waitFor(() => expect(router.currentRoute.value.query.saved).toBe('q1'))
    expect(router.currentRoute.value.query.q).toBeUndefined()
  })

  it('saves the current filter under a name and opens the new query', async () => {
    const { router, calls } = await open(
      [
        { method: 'POST', path: '/queries', status: 201, body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new', body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new/elements', body: aPage([]) },
        SEARCH([]),
      ],
      `?q=${q({ text: 'db' })}`,
    )
    await screen.findByLabelText(/nom de la requête/i)

    await fireEvent.update(screen.getByLabelText(/nom de la requête/i), 'Mes bases')
    await fireEvent.click(screen.getByRole('button', { name: /enregistrer/i }))

    await waitFor(() =>
      expect((calls.find((call) => call.method === 'POST' && call.url.pathname === '/queries')?.body as { query: Query }).query.text).toBe('db'),
    )
    await waitFor(() => expect(router.currentRoute.value.query.saved).toBe('new'))
  })

  it('shows why a name could not be kept', async () => {
    await open(
      [
        {
          method: 'POST',
          path: '/queries',
          status: 409,
          body: { error: 'duplicate', detail: 'a saved query is already named Mes bases' },
        },
        SEARCH([]),
      ],
      `?q=${q({})}`,
    )

    await fireEvent.update(await screen.findByLabelText(/nom de la requête/i), 'Mes bases')
    await fireEvent.click(screen.getByRole('button', { name: /enregistrer/i }))

    expect(await screen.findByText(/already named/)).toBeInTheDocument()
  })

  it('deletes a query after a confirmation', async () => {
    const { calls } = await open([{ method: 'DELETE', path: '/queries/q1', status: 204 }])

    await fireEvent.click(await screen.findByRole('button', { name: /supprimer les noeuds/i }))
    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('gives a reader the shared queries to open, and nothing to write', async () => {
    asAReader()
    await open([{ path: '/queries/q1', body: SAVED }, { path: '/queries/q1/elements', body: aPage([]) }])

    expect(await screen.findByRole('button', { name: 'Les noeuds' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/nom de la requête/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /supprimer/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /renommer/i })).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer**

Run: `cd frontend && npm test -- --run tests/SearchSection.spec.ts tests/router.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/features/search/SearchSection.vue"`.

- [ ] **Step 3: Le panneau** — `frontend/src/features/search/SavedQueries.vue`

```vue
<script setup lang="ts">
// The shared, named filters of the catalogue.
//
// Anyone who can log in lists and opens them; an editor also keeps the
// filter on screen under a name, renames a query, replaces its filter with the
// one on screen, deletes it. The API refuses a reader's write anyway
// (docs/adr/0032) — hiding the controls only spares them ones that could not work.
import { ref } from 'vue'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import type { Query } from './queryState'
import type { useSavedQueries } from './useSavedQueries'

const props = defineProps<{
  store: ReturnType<typeof useSavedQueries>
  current: Query
  activeId: string
}>()
const emit = defineEmits<{ open: [id: string] }>()

const { canWrite } = useMe()

const name = ref('')
const description = ref('')
const renaming = ref('')
const renameTo = ref('')
const confirming = ref('')
const failure = ref('')
const busy = ref(false)

async function guarded(action: () => Promise<void>): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    await action()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function save(): Promise<void> {
  await guarded(async () => {
    const created = await props.store.create(name.value.trim(), description.value, props.current)
    name.value = ''
    description.value = ''
    emit('open', created.id)
  })
}

function startRename(id: string, current: string): void {
  renaming.value = id
  renameTo.value = current
}

async function rename(): Promise<void> {
  await guarded(async () => {
    await props.store.update(renaming.value, { name: renameTo.value })
    renaming.value = ''
  })
}

async function replaceFilter(id: string): Promise<void> {
  await guarded(async () => {
    await props.store.update(id, { query: props.current })
    // The same address, a new filter: the screen reloads it.
    emit('open', id)
  })
}

async function remove(): Promise<void> {
  await guarded(async () => {
    await props.store.remove(confirming.value)
    confirming.value = ''
  })
}
</script>

<template>
  <section class="saved" aria-label="Requêtes enregistrées">
    <h3>Requêtes enregistrées</h3>

    <form v-if="canWrite" class="save" aria-label="Enregistrer le filtre actuel" @submit.prevent="save">
      <div class="field">
        <label for="saved-name">Nom de la requête</label>
        <input id="saved-name" v-model="name" maxlength="200" autocomplete="off" />
      </div>
      <div class="field">
        <label for="saved-description">Description</label>
        <input id="saved-description" v-model="description" autocomplete="off" />
      </div>
      <button type="submit" :disabled="busy || !name.trim()">Enregistrer</button>
    </form>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="store.status.value === 'error'" class="banner banner--error" role="alert">
      {{ store.error.value }}
    </p>

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Supprimer cette requête ? Les éléments qu'elle trouve sont conservés.
      <button type="button" :disabled="busy" @click="remove">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = ''">Renoncer</button>
    </p>

    <p v-if="store.status.value === 'ready' && store.queries.value.length === 0" class="hint">
      Aucune requête enregistrée pour l'instant.
    </p>

    <ul class="list">
      <li v-for="saved in store.queries.value" :key="saved.id">
        <form v-if="renaming === saved.id" class="inline" @submit.prevent="rename">
          <input v-model="renameTo" :aria-label="`Nouveau nom de ${saved.name}`" maxlength="200" />
          <button type="submit" :disabled="busy || !renameTo.trim()">Valider</button>
          <button type="button" class="secondary" @click="renaming = ''">Renoncer</button>
        </form>
        <template v-else>
          <button
            type="button"
            class="link"
            :aria-current="activeId === saved.id ? 'true' : undefined"
            :title="saved.description || undefined"
            @click="emit('open', saved.id)"
          >
            {{ saved.name }}
          </button>
          <span class="hint">par {{ saved.created_by }}</span>
          <span v-if="canWrite" class="actions">
            <button
              type="button"
              :aria-label="`Renommer ${saved.name}`"
              @click="startRename(saved.id, saved.name)"
            >
              Renommer
            </button>
            <button
              type="button"
              :aria-label="`Remplacer le filtre de ${saved.name} par le filtre actuel`"
              @click="replaceFilter(saved.id)"
            >
              Remplacer le filtre
            </button>
            <button
              type="button"
              class="secondary"
              :aria-label="`Supprimer ${saved.name}`"
              @click="confirming = saved.id"
            >
              Supprimer
            </button>
          </span>
        </template>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.saved {
  display: grid;
  gap: 0.6rem;
}
h3 {
  margin: 0;
}
.save {
  display: grid;
  gap: 0.4rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.4rem;
}
.list li,
.inline,
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
}
.link {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.link[aria-current='true'] {
  text-decoration-thickness: 3px;
}
input {
  font: inherit;
  padding: 0.35rem 0.55rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.35rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.link {
  border: none;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
.hint {
  opacity: 0.75;
  font-size: 0.9em;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.banner--confirm {
  border-color: #ef6c00;
}
</style>
```

- [ ] **Step 4: L'écran** — `frontend/src/features/search/SearchSection.vue`

```vue
<script setup lang="ts">
// The `/recherche` section: build a structured search, read its results, keep it.
//
// The address is the question — `?q=` carries a filter that fits in a link
// (2 000 characters at most), `?saved=` names a shared query — so a search can
// be sent to a colleague, and the back button leaves it. Submitting a filter
// writes `?q=` (a history entry: it is a new question); paging does not touch
// the address. A filter too long for a link is still run, and the person is
// told the way to share it is to save it.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import { usePackages } from '../packages/usePackages'
import QueryBuilder from './QueryBuilder.vue'
import {
  PAGE_SIZE,
  cloneQuery,
  decodeQuery,
  emptyQuery,
  encodeQuery,
  type Query,
} from './queryState'
import SavedQueries from './SavedQueries.vue'
import { useElementSearch } from './useElementSearch'
import { useSavedQueries } from './useSavedQueries'

const route = useRoute()
const router = useRouter()
const metamodel = useMetamodel()
const packages = usePackages()
const results = useElementSearch()
const saved = useSavedQueries()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const savedId = computed(() => one(route.query.saved))
const rawQuery = computed(() => one(route.query.q))

/** The filter on screen — what the form edits, and what "Rechercher" sends. */
const query = ref<Query>(emptyQuery())
const page = ref(0)
/** What the address could not give: said in words, next to the form. */
const addressProblem = ref('')
const tooLongForALink = ref(false)
const hasAsked = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(results.total.value / PAGE_SIZE)))

onMounted(async () => {
  await Promise.all([metamodel.load(), packages.load(), saved.load()])
})

/** Run what the screen holds, at the current page. */
async function run(): Promise<void> {
  hasAsked.value = true
  const offset = page.value * PAGE_SIZE
  if (savedId.value && saved.opened.value) {
    await results.runSaved(savedId.value, PAGE_SIZE, offset)
  } else {
    await results.run({ ...cloneQuery(query.value), limit: PAGE_SIZE, offset })
  }
}

/** The address changed: read the question it asks and answer it. */
async function readAddress(): Promise<void> {
  addressProblem.value = ''
  page.value = 0
  if (savedId.value) {
    await saved.open(savedId.value)
    if (!saved.opened.value) {
      results.clear()
      return
    }
    query.value = cloneQuery(saved.opened.value.query)
    await run()
  } else if (rawQuery.value) {
    saved.close()
    const decoded = decodeQuery(rawQuery.value)
    if (!decoded) {
      addressProblem.value = "Le filtre de l'adresse est illisible : construis-le dans le formulaire."
      query.value = emptyQuery()
      results.clear()
      return
    }
    query.value = decoded
    await run()
  } else {
    saved.close()
    query.value = emptyQuery()
    hasAsked.value = false
    results.clear()
  }
}

// Set when the screen itself empties the address (a filter too long to keep in
// one): the watcher must not then read that empty address and wipe the form.
let addressWasEmptiedByUs = false

watch(
  [savedId, rawQuery],
  async () => {
    if (addressWasEmptiedByUs) {
      addressWasEmptiedByUs = false
      return
    }
    await readAddress()
  },
  { immediate: true },
)

/** "Rechercher": a new question, so a history entry — and an address, if it fits in one. */
async function submit(): Promise<void> {
  const encoded = encodeQuery(query.value)
  tooLongForALink.value = encoded === null
  page.value = 0
  if (encoded !== null) {
    await router.push({ query: { q: encoded } })
    return
  }
  // Too long to share by link: run it anyway, from a clean address.
  saved.close()
  if (savedId.value || rawQuery.value) {
    addressWasEmptiedByUs = true
    await router.replace({ query: {} })
  }
  await run()
}

async function openSaved(id: string): Promise<void> {
  tooLongForALink.value = false
  if (id === savedId.value) {
    await readAddress()
    return
  }
  await router.push({ query: { saved: id } })
}

async function goToPage(target: number): Promise<void> {
  page.value = Math.max(0, target)
  await run()
}

/** One message for the two places a question can fail: opening the saved query, running it. */
const sourceError = computed(() => saved.openError.value || results.error.value)
</script>

<template>
  <section class="search">
    <header>
      <h2>Recherche</h2>
      <p class="hint">
        Types, couches, nom, paquet, attributs et relations présentes ou absentes. Les critères se
        combinent par « et ».
      </p>
    </header>

    <div class="layout">
      <div class="main">
        <QueryBuilder v-model="query" :metamodel="metamodel" :packages="packages.packages.value" @submit="submit" />

        <p v-if="tooLongForALink" class="banner" role="status">
          Ce filtre est trop long pour une adresse : enregistre-le pour le partager.
        </p>
        <p v-if="addressProblem" class="banner banner--error" role="alert">{{ addressProblem }}</p>
        <p v-if="sourceError" class="banner banner--error" role="alert">{{ sourceError }}</p>

        <p v-if="!hasAsked && !addressProblem" class="hint">
          Définis un filtre, puis « Rechercher ».
        </p>
        <p v-else-if="results.status.value === 'loading'" class="hint">Recherche…</p>
        <p v-else-if="results.status.value === 'ready' && results.items.value.length === 0" class="hint">
          Aucun élément ne correspond.
        </p>

        <template v-if="results.items.value.length > 0">
          <p class="hint" aria-live="polite">
            {{ results.total.value }} résultat{{ results.total.value > 1 ? 's' : '' }}
          </p>
          <table>
            <caption class="sr-only">Résultats de la recherche</caption>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Type</th>
                <th scope="col">Couche</th>
                <th scope="col">Paquet</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="element in results.items.value" :key="element.id">
                <td>
                  <RouterLink :to="{ path: '/elements', query: { element: element.id } }">
                    {{ element.name }}
                  </RouterLink>
                </td>
                <td>{{ metamodel.labelOf(element.element_type) }}</td>
                <td>{{ LAYER_LABELS[element.layer] }}</td>
                <td>
                  {{
                    element.package_id
                      ? (packages.byId.value.get(element.package_id)?.path ?? '—')
                      : 'Racine'
                  }}
                </td>
              </tr>
            </tbody>
          </table>

          <nav class="pagination" aria-label="Pagination des résultats">
            <button type="button" :disabled="page === 0" @click="goToPage(page - 1)">
              Page précédente
            </button>
            <span>Page {{ page + 1 }} sur {{ pageCount }}</span>
            <button type="button" :disabled="page + 1 >= pageCount" @click="goToPage(page + 1)">
              Page suivante
            </button>
          </nav>
        </template>
      </div>

      <aside class="side">
        <SavedQueries :store="saved" :current="query" :active-id="savedId" @open="openSaved" />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.search {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(14rem, 20rem);
  gap: 1.2rem;
  align-items: start;
}
.main {
  display: grid;
  gap: 0.8rem;
  min-width: 0;
}
@media (max-width: 56rem) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.45rem 0.6rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.pagination {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}
.pagination button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.pagination button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.hint {
  opacity: 0.75;
  margin: 0;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
```

`frontend/src/router/sections.ts`, après l'entrée `impact` :

```ts
  {
    path: '/recherche',
    name: 'search',
    label: 'Recherche',
    summary: 'Chercher par type, paquet, attributs et relations ; garder les requêtes utiles.',
    group: 'analysis',
    view: () => import('../features/search/SearchSection.vue'),
  },
```

- [ ] **Step 5: Relancer**

Run: `cd frontend && npm test -- --run tests/SearchSection.spec.ts tests/router.spec.ts tests/AppNav.spec.ts && npm run typecheck && npm run lint`
Expected: PASS. `AppNav.spec.ts` dérive ses attentes de `SECTIONS` (aucun nombre en dur) : la nouvelle entrée y passe sans modification.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/search frontend/src/router/sections.ts frontend/tests/SearchSection.spec.ts frontend/tests/router.spec.ts
git commit -m "feat(spa): add the search section and the shared saved queries" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 29: Clôture du lot 5

**Files:** aucun nouveau fichier ; pas d'ADR (décisions portées par l'ADR du filtre).

- [ ] **Step 1: La porte qualité**

Run: `make check`
Expected: vert.

- [ ] **Step 2: Parcours manuel** (`make run`, puis `http://localhost:5173/recherche`)

Construire « composants applicatifs du paquet Finance, propriétaire existant, sans relation `sert` sortante » ; « Rechercher » (l'adresse gagne `?q=`, recharger la page rejoue la requête) ; « Enregistrer » sous un nom (l'adresse devient `?saved=<id>`) ; ouvrir la même adresse dans une fenêtre en lecteur : la requête s'ouvre, s'exécute, aucune commande d'écriture ; supprimer le paquet Finance côté catalogue (en déplaçant son contenu) puis rouvrir la requête : message « names the package … which no longer exists », formulaire intact, requête modifiable ; coller `?q=` avec du JSON cassé : message « illisible », aucune requête. Noter tout écart dans la PR.

- [ ] **Step 3: Commit** seulement si le parcours a imposé un correctif.

---

# LOT 6 — MCP : dix outils

### Task 30: Les deux fournisseurs de services et les cinq outils de paquets

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (docstring de module ; imports ; fournisseurs ; `build_mcp_server` ; nouveaux outils ; `list_elements`)
- Modify: `backend/src/ea/main.py` (`_mount_mcp`, appel de `build_mcp_server`)
- Test: `backend/tests/unit/test_mcp_server.py` (fixtures, liste complète, `TestThePackageTools`)

**Interfaces:**
- Consumes: `PackageService`, `SavedQueryService` (Tasks 6, 23) ; `package_service_of`, `saved_query_service_of` (Tasks 7, 24) ; `PackageRead`, `PackageName`, `PackageTarget`, `package_target` (Task 7) ; `UNSET` ; `MAX_MOVE_BATCH`.
- Produces:
  - `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_packages, get_queries, *, version=..., token_verifier=None, auth=None)` — `get_packages: Callable[[], PackageService]`, `get_queries: Callable[[], SavedQueryService]`, **requis** comme les autres.
  - Outils : `list_packages() -> list[PackageRead]`, `create_package(name, description="", parent_id=None) -> PackageRead`, `update_package(package_id, name=None, description=None, parent_id: str | None = None) -> PackageRead` (`parent_id` : un UUID, ou `root`, ou absent pour ne pas déplacer), `delete_package(package_id, move_contents_to: str | None = None) -> str`, `move_elements(element_ids: list[UUID] (1..100), package_id: str) -> str` (`package_id` : un UUID ou `root`).
  - `list_elements` gagne `package_id: UUID | None = None` et `include_subpackages: bool = True`.

- [ ] **Step 1: Écrire les tests qui échouent** — `backend/tests/unit/test_mcp_server.py`

Imports à ajouter : `from ea.services.packages import PackageService`, `from ea.services.saved_queries import SavedQueryService`. Remplacer les deux fixtures `server` et `server_without_an_index` :

```python
@pytest.fixture
def server(
    service: ArchitectureService,
    document_service: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
) -> MCPServer[Any]:
    """The adapter over the in-memory stores — no database, no transport."""
    return build_mcp_server(
        lambda: service,
        lambda: document_service,
        lambda: ipam,
        lambda: file_service,
        lambda: package_service,
        lambda: saved_query_service,
    )


@pytest.fixture
def server_without_an_index(
    service: ArchitectureService,
    document_service_without_an_index: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
) -> MCPServer[Any]:
    """The same adapter on a deployment with `EA_EMBEDDINGS_ENABLED` off."""
    return build_mcp_server(
        lambda: service,
        lambda: document_service_without_an_index,
        lambda: ipam,
        lambda: file_service,
        lambda: package_service,
        lambda: saved_query_service,
    )
```

Dans l'ensemble de la liste complète de `test_it_exposes_the_whole_service_and_nothing_else`, ajouter après `"delete_file",` :

```python
            "list_packages",
            "create_package",
            "update_package",
            "delete_package",
            "move_elements",
```

Ajouter la classe (avant `TestTheSameBoundsAsTheHttpAdapter`) :

```python
@pytest.mark.asyncio
class TestThePackageTools:
    """Packages are folders: a tool files things, and never deletes what it files."""

    async def test_the_tree_comes_back_flat_with_paths_and_direct_counts(
        self, server: MCPServer[Any]
    ) -> None:
        finance = await call(server, "create_package", name="Finance")
        await call(server, "create_package", name="Budget", parent_id=finance["id"])
        element = await an_element(server, "node", "db-01")
        await call(server, "move_elements", element_ids=[element["id"]], package_id=finance["id"])

        listed = (await call(server, "list_packages"))["result"]

        assert [(p["path"], p["element_count"], p["child_count"]) for p in listed] == [
            ("Finance", 1, 1),
            ("Finance / Budget", 0, 0),
        ]

    async def test_a_taken_sibling_name_is_told_to_the_model(self, server: MCPServer[Any]) -> None:
        await call(server, "create_package", name="Finance")

        with pytest.raises(ToolError, match="already named"):
            await call(server, "create_package", name="FINANCE")

    async def test_a_name_with_a_slash_is_refused_by_the_schema(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "create_package", name="a/b")

    async def test_the_parent_is_kept_when_omitted_and_the_word_root_is_the_top(
        self, server: MCPServer[Any]
    ) -> None:
        finance = await call(server, "create_package", name="Finance")
        budget = await call(server, "create_package", name="Budget", parent_id=finance["id"])

        kept = await call(server, "update_package", package_id=budget["id"], description="x")
        at_the_top = await call(server, "update_package", package_id=budget["id"], parent_id="root")
        back = await call(
            server, "update_package", package_id=budget["id"], parent_id=finance["id"]
        )

        assert kept["parent_id"] == finance["id"]
        assert at_the_top["parent_id"] is None
        assert back["parent_id"] == finance["id"]

    async def test_a_parent_that_is_neither_an_id_nor_root_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")

        with pytest.raises(ToolError, match="root"):
            await call(server, "update_package", package_id=package["id"], parent_id="ici")

    async def test_a_package_cannot_be_moved_below_itself(self, server: MCPServer[Any]) -> None:
        a = await call(server, "create_package", name="A")
        b = await call(server, "create_package", name="B", parent_id=a["id"])

        with pytest.raises(ToolError, match="inside itself"):
            await call(server, "update_package", package_id=a["id"], parent_id=b["id"])

    async def test_a_package_that_holds_something_is_not_deleted_until_told_where_it_goes(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="A")
        element = await an_element(server, "node", "db-01")
        await call(server, "move_elements", element_ids=[element["id"]], package_id=package["id"])

        with pytest.raises(ToolError, match="move_contents_to"):
            await call(server, "delete_package", package_id=package["id"])

        await call(server, "delete_package", package_id=package["id"], move_contents_to="root")
        assert (await call(server, "get_element", element_id=element["id"]))["package_id"] is None
        assert (await call(server, "list_packages"))["result"] == []

    async def test_files_up_to_the_batch_at_once_and_back_to_the_root(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="A")
        one, two = await an_element(server, "node", "one"), await an_element(server, "node", "two")

        await call(
            server, "move_elements", element_ids=[one["id"], two["id"]], package_id=package["id"]
        )
        assert (await call(server, "list_packages"))["result"][0]["element_count"] == 2

        await call(server, "move_elements", element_ids=[one["id"]], package_id="root")
        assert (await call(server, "list_packages"))["result"][0]["element_count"] == 1

    async def test_an_absent_element_moves_nothing(self, server: MCPServer[Any]) -> None:
        package = await call(server, "create_package", name="A")
        kept = await an_element(server, "node", "db-01")

        with pytest.raises(ToolError, match="no element"):
            await call(
                server,
                "move_elements",
                element_ids=[kept["id"], str(uuid4())],
                package_id=package["id"],
            )

        assert (await call(server, "get_element", element_id=kept["id"]))["package_id"] is None

    async def test_more_than_the_batch_or_none_is_refused_by_the_schema(
        self, server: MCPServer[Any]
    ) -> None:
        for ids in ([str(uuid4()) for _ in range(101)], []):
            with pytest.raises(ToolError):
                await call(server, "move_elements", element_ids=ids, package_id="root")

    async def test_a_reader_may_read_the_tree_and_nothing_more(self, server: MCPServer[Any]) -> None:
        package = await call(server, "create_package", name="A")

        with acting_as(a_reader()):
            assert len((await call(server, "list_packages"))["result"]) == 1
            for name, arguments in (
                ("create_package", {"name": "B"}),
                ("update_package", {"package_id": package["id"], "name": "C"}),
                ("delete_package", {"package_id": package["id"]}),
                ("move_elements", {"element_ids": [str(uuid4())], "package_id": "root"}),
            ):
                with pytest.raises(ToolError):
                    await call(server, name, **arguments)

    async def test_the_catalogue_listing_takes_a_package(
        self, server: MCPServer[Any], service: ArchitectureService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = []
        real = service.list_elements

        async def spy(criteria):  # type: ignore[no-untyped-def]
            seen.append(criteria)
            return await real(criteria)

        monkeypatch.setattr(service, "list_elements", spy)
        package = await call(server, "create_package", name="A")

        await call(server, "list_elements", package_id=package["id"], include_subpackages=False)

        assert str(seen[0].package_id) == package["id"]
        assert seen[0].include_subpackages is False
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x -q`
Expected: FAIL — `TypeError: build_mcp_server() missing 2 required positional arguments: 'get_packages' and 'get_queries'`.

- [ ] **Step 3: Implémenter** — `backend/src/ea/mcp/server.py`

Docstring de module : « Four services » → « Six services, because the catalogue spans six concerns: … the graph, the markdown…, the IP addressing…, the files…, the tree of packages that files elements and diagrams, and the saved queries that keep a structured search under a name » (adapter la phrase existante, mêmes liens vers les ADR).

Imports (compléter les blocs existants) :

```python
from ea.api.schemas import (
    # (les noms déjà importés restent, dans l'ordre alphabétique)
    ElementQuerySchema,
    PackageName,
    PackageRead,
    PackageTarget,
    PropertyKey,
    PropertyTestSchema,
    PropertyValue,
    RelationTestSchema,
    SavedQueryRead,
    SavedQuerySummaryRead,
    package_target,
)
from ea.domain.packages import MAX_MOVE_BATCH, UNSET
from ea.domain.query import MAX_PROPERTY_TESTS, MAX_RELATION_TESTS, Direction, PropertyOp, QueryOrder
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
```

Fournisseurs, après `FileProvider` :

```python
PackageProvider = Callable[[], PackageService]
QueryProvider = Callable[[], SavedQueryService]
```

Arguments, après `FileContent` :

```python
PackageId = Annotated[UUID, Field(description="The id of a package, as `list_packages` reports it.")]
PackageParent = Annotated[
    PackageTarget | None,
    Field(
        description=(
            "A package id to put it under, or `root` to put it at the top of the tree. "
            "Leave it out to keep where it is."
        )
    ),
]
MoveContentsTo = Annotated[
    PackageTarget | None,
    Field(
        description=(
            "Where the package's direct contents (sub-packages, elements, diagrams) go "
            "first: a package id, or `root`. Required when the package is not empty."
        )
    ),
]
FilingTarget = Annotated[
    PackageTarget,
    Field(description="The package to file them in: its id, or `root` to take them out of any package."),
]
ElementIds = Annotated[
    list[UUID],
    Field(
        min_length=1,
        max_length=MAX_MOVE_BATCH,
        description="The ids of the elements to file, at most 100.",
    ),
]
```

Signature de `build_mcp_server` : ajouter après `get_files: FileProvider,` les deux lignes `get_packages: PackageProvider,` et `get_queries: QueryProvider,` ; docstring : « `get_packages` and `get_queries` are required for the same reason. ». Dans `list_elements`, ajouter les paramètres et le champ :

```python
        package_id: Annotated[
            UUID | None,
            Field(description="Only the elements filed in this package (see `list_packages`)."),
        ] = None,
        include_subpackages: bool = True,
```

```python
        criteria = ElementFilter(
            element_types=tuple(element_types or ()),
            layers=tuple(layers or ()),
            search=search,
            package_id=package_id,
            include_subpackages=include_subpackages,
            limit=limit,
            offset=offset,
        )
```

(docstring : « All the criteria are combined with AND… For attribute or relation tests use `search_elements`. »)

Après le bloc « Files », avant `describe_metamodel`, les cinq outils :

```python
    # --- Packages ---------------------------------------------------------
    # A package is a folder, not an ArchiMate element: filing changes nothing
    # about what an element says, and no tool here deletes one.

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_packages() -> list[PackageRead]:
        """Show the tree of packages, flat, with what each one holds.

        Packages file elements and diagrams the way folders do. They are not
        ArchiMate elements and carry no relationship. Each entry names its
        parent, gives its full `path` (`Finance / Budget`) and counts what it
        holds *directly*. Call this before filing something or before searching
        inside a domain.
        """
        return [PackageRead.of(summary) for summary in await get_packages().list_packages()]

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def create_package(
        name: PackageName,
        description: Description = "",
        parent_id: Annotated[
            UUID | None, Field(description="The package to create it in; leave out for the top.")
        ] = None,
    ) -> PackageRead:
        """Create a package (a classification folder); this is not an ArchiMate element.

        Names are unique among siblings, ignoring case, and cannot contain `/`.
        The tree is at most 12 levels deep.
        """
        return PackageRead.of(
            await get_packages().create(name=name, description=description, parent_id=parent_id)
        )

    @server.tool(annotations=EDITS)
    @speaking_plainly
    async def update_package(
        package_id: PackageId,
        name: PackageName | None = None,
        description: Description | None = None,
        parent_id: PackageParent = None,
    ) -> PackageRead:
        """Rename or describe a package, or move it (and everything below it).

        An omitted field is kept. `parent_id` is a package id, or `root` for the
        top of the tree; a package cannot be moved under itself or below itself.
        """
        parent = UNSET if parent_id is None else package_target(parent_id)
        return PackageRead.of(
            await get_packages().update(
                package_id, name=name, description=description, parent_id=parent
            )
        )

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def delete_package(
        package_id: PackageId, move_contents_to: MoveContentsTo = None
    ) -> str:
        """Delete a package — never what it holds.

        An empty package is deleted. One that still holds sub-packages,
        elements or diagrams is refused unless you pass `move_contents_to` (a
        package id, or `root`): the direct contents move there first, in one
        step. Elements and diagrams are never deleted by this tool. Confirm
        with the person you are working for before calling it.
        """
        target = UNSET if move_contents_to is None else package_target(move_contents_to)
        await get_packages().delete(package_id, move_contents_to=target)
        return f"package {package_id} was deleted"

    @server.tool(annotations=EDITS)
    @speaking_plainly
    async def move_elements(element_ids: ElementIds, package_id: FilingTarget) -> str:
        """File up to 100 elements in a package, or take them out of any package.

        Filing changes no fact of the graph: an element's name, links and
        attributes are untouched. All or none move — an unknown element or
        package refuses the whole call. `package_id` is a package id, or `root`.
        """
        moved = await get_packages().move_elements(element_ids, package_target(package_id))
        return f"{moved} element(s) filed in {package_id}"
```

`main.py` : dans `_mount_mcp`, imports `package_service_of`, `saved_query_service_of` depuis `ea.api.dependencies`, et

```python
        lambda: file_service_of(app),
        lambda: package_service_of(app),
        lambda: saved_query_service_of(app),
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/e2e/test_mcp_endpoint.py -x -q && uv run mypy src && uv run ruff check src tests`
Expected: PASS. (`TestTheSameBoundsAsTheHttpAdapter` reste vert : les nouvelles entrées arrivent à la Task 32.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/src/ea/main.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): file elements in packages from an agent" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 31: Les cinq outils de recherche et de requêtes enregistrées

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (arguments `PropertyArgument`/`RelationArgument`, aide `_query_schema`, cinq outils)
- Test: `backend/tests/unit/test_mcp_server.py` (liste complète, `TestTheSearchTools`)

**Interfaces:**
- Consumes: `ElementQuerySchema`, `PropertyTestSchema`, `RelationTestSchema`, `SavedQueryRead`, `SavedQuerySummaryRead` (Tasks 19, 24) ; `ElementFilter.from_query` ; `SavedQueryService` ; `ElementPage`.
- Produces: `search_elements(element_types=None, layers=None, text=None, package_id=None, include_subpackages=True, root_only=False, properties=None, relations=None, order="name", limit=50, offset=0) -> ElementPage` ; `list_saved_queries() -> list[SavedQuerySummaryRead]` ; `save_query(name, element_types=None, …, order="name", description="") -> SavedQueryRead` ; `run_saved_query(query_id, limit=50, offset=0) -> ElementPage` ; `delete_saved_query(query_id) -> str`.

- [ ] **Step 1: Écrire les tests qui échouent** — `backend/tests/unit/test_mcp_server.py`

Ajouter à la liste complète, après `"move_elements",` :

```python
            "search_elements",
            "list_saved_queries",
            "save_query",
            "run_saved_query",
            "delete_saved_query",
```

et la classe :

```python
@pytest.mark.asyncio
class TestTheSearchTools:
    """What an agent sends reaches the repository as one `ElementFilter` — and nothing else does."""

    @pytest.fixture
    def seen(
        self, service: ArchitectureService, monkeypatch: pytest.MonkeyPatch
    ) -> list[Any]:
        criteria: list[Any] = []
        real = service.list_elements

        async def spy(found):  # type: ignore[no-untyped-def]
            criteria.append(found)
            return await real(found)

        monkeypatch.setattr(service, "list_elements", spy)
        return criteria

    async def test_every_criterion_reaches_the_filter(
        self, server: MCPServer[Any], seen: list[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")

        page = await call(
            server,
            "search_elements",
            element_types=["application_component"],
            layers=["application"],
            text=" fact ",
            package_id=package["id"],
            include_subpackages=False,
            properties=[
                {"key": "owner", "op": "eq", "value": "finance"},
                {"key": "cidr", "op": "exists"},
            ],
            relations=[
                {"relationship_type": "serving", "direction": "out", "other_type": "business_process"},
                {"relationship_type": "association", "present": False},
            ],
            order="updated_at",
            limit=10,
            offset=20,
        )

        criteria = seen[0]
        assert [t.value for t in criteria.element_types] == ["application_component"]
        assert criteria.search == "fact"
        assert str(criteria.package_id) == package["id"]
        assert criteria.include_subpackages is False
        assert [(p.key, p.op.value, p.value) for p in criteria.properties] == [
            ("owner", "eq", "finance"),
            ("cidr", "exists", None),
        ]
        assert [(r.relationship_type.value, r.direction.value, r.present) for r in criteria.relations] == [
            ("serving", "out", True),
            ("association", "any", False),
        ]
        assert (criteria.order.value, criteria.limit, criteria.offset) == ("updated_at", 10, 20)
        assert (page["limit"], page["offset"]) == (10, 20)

    async def test_no_argument_at_all_is_the_whole_catalogue_paged(
        self, server: MCPServer[Any]
    ) -> None:
        await an_element(server, "node", "db-01")

        page = await call(server, "search_elements")

        assert (page["total"], page["limit"], page["offset"]) == (1, 50, 0)

    @pytest.mark.parametrize(
        "arguments",
        [
            {"properties": [{"key": "owner", "op": "eq"}]},
            {"properties": [{"key": "owner", "op": "exists", "value": "x"}]},
            {"properties": [{"key": "a-b", "op": "exists"}]},
            {"properties": [{"key": "owner", "op": "gt", "value": "1"}]},
            {"root_only": True, "package_id": "11111111-1111-4111-8111-111111111111"},
            {"element_types": ["microservice"]},
            {"limit": 0},
        ],
    )
    async def test_a_filter_the_server_would_refuse_is_refused_with_a_message(
        self, server: MCPServer[Any], arguments: dict[str, Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "search_elements", **arguments)

    async def test_a_filter_is_kept_under_a_name_and_run_again(
        self, server: MCPServer[Any], seen: list[Any]
    ) -> None:
        await an_element(server, "node", "db-01")
        saved = await call(
            server,
            "save_query",
            name="Les noeuds",
            description="Tous",
            element_types=["node"],
            properties=[{"key": "owner", "op": "absent"}],
        )

        assert saved["created_by"] == "editor"
        assert saved["query"]["element_types"] == ["node"]
        listed = (await call(server, "list_saved_queries"))["result"]
        assert [(q["name"], "query" in q) for q in listed] == [("Les noeuds", False)]

        page = await call(server, "run_saved_query", query_id=saved["id"], limit=10)

        assert (page["total"], page["limit"]) == (1, 10)
        assert [t.value for t in seen[-1].element_types] == ["node"]

    async def test_a_name_already_taken_is_told_to_the_model(self, server: MCPServer[Any]) -> None:
        await call(server, "save_query", name="A")

        with pytest.raises(ToolError, match="already named"):
            await call(server, "save_query", name="A")

    async def test_a_query_naming_a_vanished_package_says_so_and_stays(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")
        saved = await call(server, "save_query", name="Compta", package_id=package["id"])
        await call(server, "delete_package", package_id=package["id"], move_contents_to="root")

        with pytest.raises(ToolError, match="no longer exists"):
            await call(server, "run_saved_query", query_id=saved["id"])

        assert len((await call(server, "list_saved_queries"))["result"]) == 1

    async def test_deleting_a_query_leaves_the_elements_it_found(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")
        saved = await call(server, "save_query", name="Tous")

        await call(server, "delete_saved_query", query_id=saved["id"])

        assert (await call(server, "list_saved_queries"))["result"] == []
        assert (await call(server, "get_element", element_id=element["id"]))["name"] == "db-01"

    async def test_an_unknown_query_is_a_message(self, server: MCPServer[Any]) -> None:
        for name in ("run_saved_query", "delete_saved_query"):
            with pytest.raises(ToolError, match="no saved query"):
                await call(server, name, query_id=str(uuid4()))

    async def test_a_reader_searches_lists_and_runs_but_does_not_write(
        self, server: MCPServer[Any]
    ) -> None:
        saved = await call(server, "save_query", name="A")

        with acting_as(a_reader()):
            await call(server, "search_elements")
            assert len((await call(server, "list_saved_queries"))["result"]) == 1
            await call(server, "run_saved_query", query_id=saved["id"])
            with pytest.raises(ToolError):
                await call(server, "save_query", name="B")
            with pytest.raises(ToolError):
                await call(server, "delete_saved_query", query_id=saved["id"])
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x -q -k "TestTheSearchTools or whole_service"`
Expected: FAIL — `Unknown tool: search_elements` / la liste complète diffère.

- [ ] **Step 3: Implémenter** — `backend/src/ea/mcp/server.py`

Imports : `from pydantic import BaseModel, ConfigDict, Field` (compléter) ; `from ea.api.schemas import … RelationshipType` est déjà importé via `ea.domain.archimate`. Après les arguments de la Task 30 :

```python
class PropertyArgument(BaseModel):
    """One test on a user-defined attribute, in the words an agent writes it."""

    model_config = ConfigDict(extra="forbid")

    key: PropertyKey
    op: PropertyOp = Field(
        description="`eq` equals, `contains` has the fragment (case ignored), `exists`, `absent`."
    )
    value: PropertyValue | None = Field(
        default=None, description="Required by `eq` and `contains`; leave out for the others."
    )


class RelationArgument(BaseModel):
    """Whether an element has — or has no — relationship of one type."""

    model_config = ConfigDict(extra="forbid")

    relationship_type: RelationshipType
    direction: Direction = Field(
        default=Direction.ANY, description="`out` leaves the element, `in` arrives at it."
    )
    present: bool = Field(
        default=True, description="`false` finds the elements that have NO such relationship."
    )
    other_type: ElementType | None = Field(
        default=None, description="The type of the element at the other end, if it matters."
    )


PropertyTests = Annotated[
    list[PropertyArgument] | None,
    Field(
        max_length=MAX_PROPERTY_TESTS,
        description="Tests on attributes, combined with AND. At most 10.",
    ),
]
RelationTests = Annotated[
    list[RelationArgument] | None,
    Field(
        max_length=MAX_RELATION_TESTS,
        description="Tests on links present or absent, combined with AND. At most 5.",
    ),
]
QueryId = Annotated[UUID, Field(description="The id of a saved query, as `list_saved_queries` gives it.")]


def _query_schema(
    *,
    element_types: list[ElementType] | None,
    layers: list[Layer] | None,
    text: str | None,
    package_id: UUID | None,
    include_subpackages: bool,
    root_only: bool,
    properties: list[PropertyArgument] | None,
    relations: list[RelationArgument] | None,
    order: QueryOrder,
    limit: int,
    offset: int,
) -> ElementQuerySchema:
    """The HTTP adapter's own schema, built from an agent's friendlier arguments.

    Going through it — and then `to_domain()` — means a filter is judged by
    the very same bounds and rules whichever door it came in by.
    """
    return ElementQuerySchema(
        version=1,
        element_types=list(element_types or ()),
        layers=list(layers or ()),
        text=text,
        package_id=package_id,
        include_subpackages=include_subpackages,
        root_only=root_only,
        properties=[
            PropertyTestSchema(key=test.key, op=test.op, value=test.value)
            for test in properties or ()
        ],
        relations=[
            RelationTestSchema(
                relationship_type=test.relationship_type,
                direction=test.direction,
                present=test.present,
                other_type=test.other_type,
            )
            for test in relations or ()
        ],
        order=order,
        limit=limit,
        offset=offset,
    )
```

(`ElementType` est importé de `ea.domain.archimate` ; `MAX_PROPERTY_TESTS` / `MAX_RELATION_TESTS` de la Task 30.) Les cinq outils, après `move_elements` :

```python
    # --- Structured search and saved queries ------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def search_elements(
        element_types: list[ElementType] | None = None,
        layers: list[Layer] | None = None,
        text: Search | None = None,
        package_id: Annotated[
            UUID | None, Field(description="Only the elements filed in this package.")
        ] = None,
        include_subpackages: bool = True,
        root_only: Annotated[
            bool, Field(description="Only the elements filed in no package at all.")
        ] = False,
        properties: PropertyTests = None,
        relations: RelationTests = None,
        order: QueryOrder = QueryOrder.NAME,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> ElementPage:
        """Structured search of the catalogue: prefer it to paging through `list_elements`.

        Combine, with AND: element types, layers, a name fragment, a package
        (with or without its sub-packages, or `root_only` for the unfiled),
        tests on attributes (`eq`, `contains`, `exists`, `absent`) and tests on
        links present or absent, optionally to an element of a given type —
        "the application components of Finance that have an owner and serve
        nothing" is one call. `total` counts everything the filter matches.
        A filter no element can satisfy answers an empty page, not an error.
        """
        query = _query_schema(
            element_types=element_types,
            layers=layers,
            text=text,
            package_id=package_id,
            include_subpackages=include_subpackages,
            root_only=root_only,
            properties=properties,
            relations=relations,
            order=order,
            limit=limit,
            offset=offset,
        ).to_domain()
        criteria = ElementFilter.from_query(query)
        service = get_service()
        return ElementPage(
            items=[ElementRead.of(element) for element in await service.list_elements(criteria)],
            total=await service.count_elements(criteria),
            limit=query.limit,
            offset=query.offset,
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_saved_queries() -> list[SavedQuerySummaryRead]:
        """The named filters the team already keeps: reuse one rather than rebuild it.

        A summary names the query and who wrote it; `run_saved_query` runs it.
        """
        return [
            SavedQuerySummaryRead.of(summary) for summary in await get_queries().list_queries()
        ]

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def save_query(
        name: Name,
        description: Description = "",
        element_types: list[ElementType] | None = None,
        layers: list[Layer] | None = None,
        text: Search | None = None,
        package_id: Annotated[
            UUID | None, Field(description="Only the elements filed in this package.")
        ] = None,
        include_subpackages: bool = True,
        root_only: Annotated[
            bool, Field(description="Only the elements filed in no package at all.")
        ] = False,
        properties: PropertyTests = None,
        relations: RelationTests = None,
        order: QueryOrder = QueryOrder.NAME,
    ) -> SavedQueryRead:
        """Keep a filter under a name, for the whole team.

        The filter arguments are those of `search_elements`. The name must be
        free. Anyone can run the query afterwards; any editor can change it.
        A query names its package by id: if the package is deleted later the
        query stays, and running it says which package is gone.
        """
        query = _query_schema(
            element_types=element_types,
            layers=layers,
            text=text,
            package_id=package_id,
            include_subpackages=include_subpackages,
            root_only=root_only,
            properties=properties,
            relations=relations,
            order=order,
            limit=50,
            offset=0,
        ).to_domain()
        return SavedQueryRead.read(
            await get_queries().create(name=name, description=description, query=query)
        )

    @server.tool(annotations=READS)
    @speaking_plainly
    async def run_saved_query(
        query_id: QueryId, limit: Limit = 50, offset: Offset = 0
    ) -> ElementPage:
        """Run a saved query, one page at a time."""
        result = await get_queries().run(query_id, limit=limit, offset=offset)
        return ElementPage(
            items=[ElementRead.of(element) for element in result.elements],
            total=result.total,
            limit=limit,
            offset=offset,
        )

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def delete_saved_query(query_id: QueryId) -> str:
        """Delete a saved query for everyone. The elements it finds are not touched.

        Confirm with the person you are working for before calling it.
        """
        await get_queries().delete(query_id)
        return f"saved query {query_id} was deleted"
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x -q && uv run mypy src && uv run ruff check src tests`
Expected: PASS. Si un argument de type `list[PropertyArgument] | None` avec `max_length` n'est pas porté par l'`anyOf` du schéma d'entrée, vérifier avec `bounds_of` à la Task 32 : la borne doit se trouver dans la branche `array`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): search the catalogue and keep named queries from an agent" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 32: `INSTRUCTIONS`, annotations destructives et bornes identiques à HTTP

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (`INSTRUCTIONS`)
- Test: `backend/tests/unit/test_mcp_server.py` (`TestTheToolset`, `TestTheSameBoundsAsTheHttpAdapter`)

**Interfaces:**
- Consumes: les dix outils (Tasks 30-31) ; `PackageCreate`, `PackageUpdate`, `MoveElements`, `SavedQueryCreate`, `ElementQuerySchema` (Tasks 7, 19, 24).
- Produces: `INSTRUCTIONS` mentionne paquets, `move_elements`, `search_elements`, requêtes enregistrées ; la parité des bornes HTTP/MCP est testée pour chaque borne des dix outils.

- [ ] **Step 1: Écrire les tests qui échouent**

Imports à ajouter à `test_mcp_server.py` : `ElementQuerySchema, MoveElements, PackageCreate, PackageUpdate, SavedQueryCreate` dans l'import de `ea.api.schemas`, et `from ea.mcp.server import INSTRUCTIONS`.

Dans `TestTheToolset` :

```python
    async def test_the_two_new_deletions_are_flagged_destructive_and_filing_is_not(
        self, server: MCPServer[Any]
    ) -> None:
        annotations = {
            tool.name: tool.annotations
            for tool in await server.list_tools()
            if tool.annotations is not None
        }

        for name in ("delete_package", "delete_saved_query"):
            assert annotations[name].destructive_hint is True, name
        for name in ("move_elements", "update_package", "create_package", "save_query"):
            assert annotations[name].destructive_hint is False, name
        for name in ("list_packages", "search_elements", "list_saved_queries", "run_saved_query"):
            assert annotations[name].read_only_hint is True, name

    async def test_the_instructions_teach_packages_and_the_structured_search(self) -> None:
        for phrase in (
            "list_packages",
            "move_elements",
            "root",
            "search_elements",
            "list_saved_queries",
            "run_saved_query",
            "save_query",
        ):
            assert phrase in INSTRUCTIONS, phrase

    async def test_the_filing_tools_tell_the_model_they_delete_no_element(
        self, server: MCPServer[Any]
    ) -> None:
        tools = {tool.name: tool for tool in await server.list_tools()}

        assert "never" in (tools["delete_package"].description or "").lower()
        assert "no fact" in (tools["move_elements"].description or "").lower()
```

Dans `TestTheSameBoundsAsTheHttpAdapter`, ajouter à la liste du premier `parametrize` :

```python
            ("create_package", "name", PackageCreate, "name"),
            ("create_package", "description", PackageCreate, "description"),
            ("update_package", "name", PackageUpdate, "name"),
            ("update_package", "description", PackageUpdate, "description"),
            ("move_elements", "element_ids", MoveElements, "element_ids"),
            ("save_query", "name", SavedQueryCreate, "name"),
            ("save_query", "description", SavedQueryCreate, "description"),
            ("search_elements", "text", ElementQuerySchema, "text"),
            ("search_elements", "limit", ElementQuerySchema, "limit"),
            ("search_elements", "offset", ElementQuerySchema, "offset"),
            ("search_elements", "properties", ElementQuerySchema, "properties"),
            ("search_elements", "relations", ElementQuerySchema, "relations"),
            ("save_query", "text", ElementQuerySchema, "text"),
            ("save_query", "properties", ElementQuerySchema, "properties"),
            ("save_query", "relations", ElementQuerySchema, "relations"),
```

et au second (`/elements`) :

```python
            ("run_saved_query", "limit", "/queries/{query_id}/elements", "limit"),
            ("run_saved_query", "offset", "/queries/{query_id}/elements", "offset"),
```

(`query_parameter` lit l'opération `get` : ces deux chemins sont des `GET`.) Ajouter enfin un test pour le seul paramètre de requête d'un `DELETE` :

```python
    async def test_the_package_target_is_the_same_type_over_http_and_mcp(
        self, server: MCPServer[Any], service: ArchitectureService
    ) -> None:
        openapi = create_app(Settings(debug=True), architecture_service=service).openapi()
        operation = openapi["paths"]["/packages/{package_id}"]["delete"]
        over_http = next(p["schema"] for p in operation["parameters"] if p["name"] == "move_contents_to")
        tools = {listed.name: listed for listed in await server.list_tools()}
        sent = tools["delete_package"].input_schema["properties"]["move_contents_to"]

        assert bounds_of(sent), "move_contents_to carries no bound at all"
        assert bounds_of(sent) == bounds_of(over_http)
        assert bounds_of(tools["move_elements"].input_schema["properties"]["package_id"]) == bounds_of(over_http)
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x -q -k "Toolset or SameBounds"`
Expected: FAIL — `AssertionError: list_packages not in INSTRUCTIONS` (et, si une borne manque côté outil, `search_elements.properties carries no bound at all`).

- [ ] **Step 3: Les instructions** — `backend/src/ea/mcp/server.py`, `INSTRUCTIONS` : ajouter, avant le paragraphe sur les fichiers (« The server also keeps files of any kind… »), deux paragraphes :

```
Elements and diagrams are filed in *packages*, a tree of folders that carries
no meaning in the model: filing an element changes nothing about it but where
it sits. `list_packages` shows the tree with what each package holds;
`create_package`, `update_package` and `delete_package` shape it — a package is
deleted only when empty, or with `move_contents_to`, never with its contents —
and `move_elements` files up to 100 elements at once. Packages are named by
id; where an id is expected for a package, the word `root` means the top of
the tree.

To find elements, prefer `search_elements` to paging through `list_elements`:
it combines types, layers, a name fragment, a package (with or without its
sub-packages), tests on attributes (`eq`, `contains`, `exists`, `absent`) and on
links present or absent — "the application components of Finance with an owner
and no serving link" is one call. `list_saved_queries` shows the filters the
team already named; reuse one with `run_saved_query` rather than rebuilding it,
and `save_query` keeps a good filter for the others.
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q && uv run mypy src && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS. Si la borne `maxItems` d'un argument-liste optionnel n'apparaît pas dans le schéma de l'outil, la déclarer par `Annotated[list[X], Field(max_length=N)] | None` (borne sur la branche `array`) plutôt que `Annotated[list[X] | None, Field(max_length=N)]` — `bounds_of` descend dans `anyOf`, donc les deux écritures sont lues ; corriger l'écriture jusqu'à ce que `sent_to_the_agent` ne soit pas vide.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): teach packages and structured search in the instructions" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 33: Le parcours de bout en bout, `CLAUDE.md` et clôture du sous-projet

**Files:**
- Create: `backend/tests/integration/test_referentiel_journey.py`
- Modify: `CLAUDE.md` (état du projet, nombre d'outils, nombre de fournisseurs)

**Interfaces:**
- Consumes: tout le sous-projet ; `create_app(..., architecture_service=, documents=, packages=, queries=)` ; les fixtures d'intégration (`engine_at_head`).
- Produces: un test de parcours HTTP sur PostgreSQL jetable ; `CLAUDE.md` à jour.

- [ ] **Step 1: Écrire le parcours** — `backend/tests/integration/test_referentiel_journey.py`

```python
"""The journey of the whole sub-project, over HTTP, on a real PostgreSQL.

Create a package, file an element in it, search for "sous-arbre", keep the
query, run it again — then delete the package and see the query explain
itself. The doubled suites cannot prove it: their graph does not filter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.postgres import create_session_factory
from ea.domain.query import ElementQuery, to_json
from ea.main import create_app
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.repositories.saved_query_store import PostgresSavedQueryRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def client(engine_at_head: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    sessions = create_session_factory(engine_at_head)
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=ArchitectureService(PostgresArchitectureRepository(sessions)),
        diagrams=PostgresDiagramRepository(sessions),
        packages=PostgresPackageRepository(sessions),
        queries=PostgresSavedQueryRepository(sessions),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def body(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


async def made(client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_create_file_search_save_run_and_survive_a_deleted_package(
    client: httpx.AsyncClient,
) -> None:
    finance = await made(client, "/packages", {"name": "Finance"})
    budget = await made(client, "/packages", {"name": "Budget", "parent_id": finance["id"]})
    inside = await made(
        client,
        "/elements",
        {"element_type": "application_component", "name": "Facturation sous-arbre",
         "properties": {"owner": "finance"}},
    )  # fmt: skip
    outside = await made(
        client, "/elements", {"element_type": "application_component", "name": "Facturation racine"}
    )
    assert outside["package_id"] is None
    filed = await client.post(
        "/elements/move", json={"element_ids": [inside["id"]], "package_id": budget["id"]}
    )
    assert filed.status_code == 204

    # The catalogue's own listing and the structured search agree on the subtree.
    listed = (await client.get("/elements", params={"package": finance["id"]})).json()
    assert [e["name"] for e in listed["items"]] == ["Facturation sous-arbre"]

    query = body(
        text="sous-arbre",
        package_id=finance["id"],
        properties=[{"key": "owner", "op": "eq", "value": "finance"}],
    )
    found = (await client.post("/elements/search", json=query)).json()
    assert (found["total"], [e["name"] for e in found["items"]]) == (1, ["Facturation sous-arbre"])

    without_children = (
        await client.post("/elements/search", json={**query, "include_subpackages": False})
    ).json()
    assert without_children["total"] == 0

    saved = await made(client, "/queries", {"name": "Compta", "description": "d", "query": query})
    assert saved["created_by"] == "local-developer"
    replay = (await client.get(f"/queries/{saved['id']}/elements")).json()
    assert [e["name"] for e in replay["items"]] == ["Facturation sous-arbre"]

    # A package that still holds something is not deleted; moving its content is.
    refused = await client.delete(f"/packages/{finance['id']}")
    assert (refused.status_code, refused.json()["error"]) == (409, "package_not_empty")
    gone = await client.delete(f"/packages/{budget['id']}", params={"move_contents_to": "root"})
    assert gone.status_code == 204
    assert (await client.get(f"/elements/{inside['id']}")).json()["package_id"] is None

    # The query names the package by id and keeps no foreign key: it survives and explains.
    await client.delete(f"/packages/{finance['id']}")
    stale = await client.get(f"/queries/{saved['id']}/elements")
    assert (stale.status_code, stale.json()["error"]) == (422, "unknown_package")
    assert (await client.get(f"/queries/{saved['id']}")).status_code == 200
    assert [q["name"] for q in (await client.get("/queries")).json()] == ["Compta"]
```

Run: `make test-integration`
Expected: PASS (HTTP réel sur PostgreSQL jetable ; sans conteneur le test est *skippé* : exiger un vrai passage).

- [ ] **Step 2: Mettre à jour `CLAUDE.md`** (trois remplacements factuels, aucune réécriture)

1. Dans « Project status » : remplacer `with eight sections built —` par `with nine sections built —`, et, juste avant la phrase `The same backend also speaks **MCP**:`, insérer : `Elements and diagrams are filed in a tree of *packages* — a classification with no ArchiMate meaning, \`packages\` plus a nullable \`package_id\` on \`elements\` and \`diagrams\` (migration \`0009\`, see \`docs/adr/00NN\`) — and the *Recherche* section runs one pure \`ElementQuery\` (types, layers, name, package, attribute tests, relations present or absent) that \`POST /elements/search\` and the MCP tool \`search_elements\` share and that \`saved_queries\` (migration \`0010\`, see \`docs/adr/00MM\`) keeps under a name, shared by every reader. ` (remplacer `00NN` / `00MM` par les numéros réels des deux ADR).
2. Remplacer `as thirty-four tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing and the files of the bucket —` par `as forty-four tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing, the files of the bucket, the packages and the structured search —`.
3. Dans « The MCP adapter is a sibling of \`api/\` », remplacer `so the adapter holds **four** service providers` par `so the adapter holds **six** service providers` (les deux ajoutés sont les paquets et les requêtes enregistrées).
4. Dans l'arborescence « Repository layout », sous \`domain/\`, ajouter trois lignes alignées sur \`diagrams.py\` : \`packages.py   # the classification tree: names, cycles, depth — no ArchiMate meaning\`, \`query.py      # ElementQuery: the one structured filter, and its stored JSON\`, \`saved_queries.py # a named, shared ElementQuery\`.

Run: `git diff CLAUDE.md`
Expected: quatre hunks courts, aucune autre ligne touchée.

- [ ] **Step 3: La porte qualité finale**

Run: `make check && make test-integration`
Expected: vert ; `make openapi-check` confirme que le contrat n'a pas bougé depuis le lot 4 (le MCP n'entre pas dans OpenAPI).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_referentiel_journey.py CLAUDE.md
git commit -m "docs: record the package tree and the structured search in CLAUDE.md" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Auto-revue

**Couverture de la spec (§ par §).**
- §1-2 périmètre : paquets (Tasks 1-9), `ElementQuery` (17-20), requêtes enregistrées (21-25), outils MCP (30-32), arbre Éléments/Diagrammes (10-16), section Recherche (26-29) — le « hors périmètre » (droits par paquet, multi-paquets, XMI, vues générées, SQL libre) n'est touché nulle part.
- §3 modèle : `domain/packages.py` (T1), `domain/query.py` (T17), `domain/saved_queries.py` (T21), erreurs (T1, T17, T21), alias étroit d'`ElementFilter` (T2, T17), bornes `MAX_PROPERTY_TESTS=10`, `MAX_RELATION_TESTS=5`, `MAX_TEXT=200` (T17), relation impossible acceptée (test T17 + T18), grouping ≠ paquet (ADR T9).
- §4 données : deux migrations réversibles `0009`/`0010` (T3, T22), index partiels, `RESTRICT`, base peuplée aller-retour (T3), `move_contents_to` transactionnel (T4, T6, T7), descendant → `cyclic_package` (T6, T7), CTE de sous-arbre (T4 — l'écart 3 explique `check_tree`).
- §5 API : toutes les lignes du tableau (T7, T8, T19, T24), `model_fields_set` (T7, T8), `_STATUS` complet gardé par `test_error_statuses` (T1, T7, T19, T24), OpenAPI régénéré (T9, T20, T25).
- §6 MCP : dix outils, docstrings, annotations, liste complète, `list_elements` étendu, `INSTRUCTIONS` (T30-T32).
- §7 SPA : `PackageTree`, `?package=`/`?subpackages=0` push/replace, glisser-déposer **et** « Déplacer vers… », lecteur sans commandes, diagrammes, chemin du détail, section Recherche (`QueryBuilder`, `SavedQueries`, `?saved=`, `?q=` ≤ 2000), `useLatestRequest`/`debounce`, rôle `tree`/flèches/`aria-expanded` (T10-T16, T26-T29).
- §8 sécurité : gardes AST (T6, T23), paramètres liés + `strpos` (T18), 16 Ko (T17), lot atomique (T4), `created_by` du `Caller` (T23, T24), relecture validée (T17, T22), journal (T6, T23).
- §9 tests : unit / intégration (dont migration peuplée) / API / frontend / parcours (T33, écart 8).
- §10 ADR : deux ADR, rédigés (T9, T20 + complément T25).
- §11 Q1-Q9 : Q1 un paquet par élément (modèle), Q2 refus + `move_contents_to`, Q3 unicité globale conservée, Q4 partagées + `created_by`, Q5 `unknown_package`, Q6 plafond `422 too_many_packages`, Q7 texte seulement, Q8 aucun index dédié au-delà de `package_id`, Q9 pipeline inchangé.

**Scan des interdits.** Aucun « TBD », « TODO » ni « similaire à la tâche N » : chaque tâche répète son code. Les rares renvois « à ajuster au premier passage rouge » ont été supprimés ou remplacés par du code décidé (sélection multiple du constructeur : `option.selected` + `change`).

**Cohérence des noms entre tâches (vérifiée).** `UNSET`/`Unset` (T1 → T5, T6, T7, T8, T30) ; `PackageSummary(package, path, counts)` et `PackageCounts(elements, diagrams, children)` (T1 → T6, T7 `PackageRead.of`) ; `PackageRepository.delete(package_id, *, into)` (T2 → T4, T5 double, T6) ; `DiagramService(..., packages=...)` et `list_diagrams(package_id=, include_subpackages=, root_only=)` (T5 → T7/T8 routes) ; `ElementFilter.package_id/include_subpackages/root_only` (T2 → T4, T8) puis `.properties/.relations/.order` (T17 → T18) ; `ElementQuerySchema.to_domain()/of()` (T19 → T24, T31) ; `SavedQueryRead.read(saved)` et `SavedQuerySummaryRead.of(summary)` (T24 → T31) ; `package_target`, `ROOT_PACKAGE` (T7 → T30) ; `QueryResult(elements, total)` (T17 → T23, T24) ; côté SPA `ROOT = 'root'`, `ALL = ''`, `ELEMENT_DRAG_TYPE` (T10 → T11, T13, T15, T27) et `emptyQuery`/`cloneQuery`/`encodeQuery`/`decodeQuery` (T26 → T27, T28) ; `usePackages().{packages, byId, tree, load, create, update, remove, moveElements}` (T10 → T12, T13, T15, T28).

**Ordre et dépendances.** Lot 1 → (2 ‖ 3) → 4 → (5 ‖ 6) comme dans la spec ; le plan les écrit en séquence 1-2-3-4-5-6 (les lots 2 et 3 sont indépendants et peuvent être menés en parallèle par deux agents, tant que les générations de client de T9/T20/T25 sont rejouées dans l'ordre). Les tâches 17-19 laissent `test_error_statuses.py` vert dès T7 puis après chaque ajout d'erreur (`InvalidQueryError` : T19 ; erreurs de requêtes enregistrées : T24) — entre T17 et T19, et entre T21 et T24, ce test est rouge **pour les erreurs ajoutées seulement** ; ne pas lancer la suite complète comme jalon avant T19 / T24.

**Points de vigilance à l'exécution.** (1) Numéros de migration `0009`/`0010` et des deux ADR à recalculer au moment d'exécuter. (2) `make test-integration` doit réellement tourner (sans conteneur les tests s'auto-*skippent*). (3) `ruff format` reformatera les longues lignes de tests collés ; relire le diff avant commit. (4) Les nombres de lignes cités pour les fichiers modifiés (`≈ l.`) sont ceux du dépôt le 2026-10-10.
