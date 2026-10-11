# Temps et feuille de route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner un temps de validité (`valid_from` / `valid_until`) aux éléments et aux relations, filtrer les lectures par `as_of`, calculer l'écart entre deux plateaux, et dessiner une feuille de route (SVG) avec un sélecteur de date global `?asof=`.

**Architecture:** Module de domaine pur `domain/timeline.py` (voisin de `domain/ipam.py`) ; les dates restent des clés de `properties` validées à toute écriture dans `ArchitectureService` ; un prédicat SQL unique `visible_at()` dans `architecture_store.py` entre dans les listes et dans le terme récursif des deux CTE ; les cas d'usage plateau / écart / frise sont de nouvelles méthodes de `ArchitectureService` (pas de nouveau service) exposées par `api/timeline.py` et par deux outils MCP ; le SPA lit `?asof=` dans l'URL, propage la date par une garde de routeur, et dessine la frise par un module pur `lib/roadmapLayout.ts`.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2 async + Alembic ; MCP SDK 2.x ; Vue 3 `<script setup>` + TypeScript + openapi-fetch ; pytest, Vitest + Testing Library ; PostgreSQL jetable pour l'intégration.

**Spec:** `docs/superpowers/specs/2026-10-10-05-temps-feuille-de-route-design.md`

## Écarts constatés avec la spec

Le plan suit le code réel, pas la spec, sur ces points :

1. **Noms des outils MCP.** La spec parle de `get_neighbourhood` / `get_impact` ; les outils réels de `backend/src/ea/mcp/server.py` s'appellent `neighbourhood` et `impact_of`. Ce sont eux qui gagnent `as_of` (avec `list_elements`). La liste asserted dans `tests/unit/test_mcp_server.py` passe de 34 à 36 noms (`list_plateaus`, `gap_between`).
2. **Pas de `TimelineService`.** `build_mcp_server(get_service, get_documents, get_ipam, get_files)` et `create_app` n'ont que quatre fournisseurs ; un cinquième obligerait à toucher chaque appel de test. Les cas d'usage (`list_plateaus`, `plateau_gap`, `create_gap`, `roadmap`) sont des méthodes de `ArchitectureService` ; `test_service_guards.py` les couvre en ajoutant `create_gap` à `WRITES[ArchitectureService]`.
3. **Diagrammes : §5 et §7 se contredisent.** §5 dit que `GET /diagrams/{id}` filtre par `as_of`, §7 dit que les boîtes hors période sont *grisées, pas retirées*. Le plan retient §7 : `as_of` ne retire rien ; `DiagramRead` gagne `inactive_element_ids` et `inactive_relationship_ids` (calculés dans `DiagramService.open` avec `Lifespan.covers`), le SPA grise.
4. **`GET /elements/{id}/relationships` n'est pas dans la liste de §5** et n'est donc pas filtré : le panneau Relations montre encore les liens vers un élément retiré. Hors périmètre, noté dans l'ADR de `as_of`.
5. **Les index partiels ne servent pas le prédicat de visibilité.** Un index partiel `WHERE (properties ->> 'valid_from') IS NOT NULL` ne peut pas répondre à `valid_from IS NULL OR valid_from <= :d` (les lignes sans date sont hors de l'index). Il sert les bornes sur lignes datées (`valid_from <= :d`). Le plan crée quand même les quatre index comme la spec le demande (migration `0009`), l'`EXPLAIN` est testé sur la forme `valid_from <= :d`, et aucune requête de ce plan n'en dépend pour être correcte. Mesure sur le jeu réel avant d'aller plus loin (ADR de `as_of`).
6. **`gap_between` : une seule annotation par outil.** La spec veut « lecture si `create_gap` faux, écriture sinon » ; le protocole n'a qu'un jeu d'annotations par outil. Le plan retient la pessimiste (`ADDS`) et dit dans la docstring que l'outil ne lit que tant que `create_gap` est faux.
7. **Propagation de `asof` aux liens du menu.** Pas de modification de `router/sections.ts` pour cela : `AppNav` rend des `RouterLink` vers `section.path`. Une garde globale dans `router/index.ts` recopie `asof` quand on change de section (`to.path !== from.path`) — elle couvre aussi les liens internes (`RouterLink` du catalogue, boutons « Centrer sur »). `sections.ts` reçoit seulement l'entrée « Feuille de route ».
8. **La section « Feuille de route » naît au lot 4**, avec le panneau d'écart seul (sinon ce panneau n'a aucune porte d'entrée) ; le lot 5 y ajoute la frise.
9. **Élément demandé hors période.** `neighbourhood` / `impact_of` d'un élément qui existe mais n'est pas en service à `as_of` renvoient un sous-graphe **vide** (200), pas un 404 : l'élément existe, il n'est pas visible à cette date. Choix consigné dans l'ADR de `as_of`.
10. **Fenêtre de `/roadmap`.** « Max 20 ans » est implémenté en jours (`MAX_ROADMAP_DAYS = 20 * 366 = 7320`) : refus 422 (`ValueError`, enveloppe `invalid_input`) si `from` et `to` sont tous deux donnés et plus larges ; une fenêtre par défaut est rognée à cette largeur. Plus de `MAX_ROADMAP_ITEMS = 1000` éléments → 422 également (jamais de troncature silencieuse).
11. **Valeur vide = absente.** `PATCH` avec `valid_until: ""` (champ date vidé dans le SPA) retirerait sinon l'élément de toute lecture datée (`"" >= '2027-01-01'` est faux). `validate_lifespan_properties` retire les clés vides ; c'est une règle de plus que la spec ne dit pas.
12. **Numéros.** Migration `0009` (dernière : `0008_file_metadata`) ; ADR `0042`, `0043`, `0044` (dernier : `0041`). À renuméroter à l'exécution si une autre migration / un autre ADR est entré avant.

## Global Constraints

- Lire `CLAUDE.md` avant toute tâche ; ses règles gagnent sur ce plan. Flèche : `api → services → domain ← repositories`, `mcp/` à côté de `api/`, `domain/` sans import de framework.
- TDD : test qui échoue d'abord, pour la bonne raison, puis le code minimal. Backend : `cd backend && uv run pytest <fichier> -x` **sans** `--cov`. Frontend : `cd frontend && npm test -- --run <fichier>`.
- Format des dates : `YYYY-MM-DD` canonique (`date.fromisoformat(...).isoformat()`), clés de `properties` `valid_from` et `valid_until`, `valid_until` **inclusive**, absente = ouvert ; format partiel (`2027-06`, `2027-Q2`) refusé.
- Visible à `d` : `(valid_from IS NULL OR valid_from <= d) AND (valid_until IS NULL OR valid_until >= d)` ; une relation l'est si elle-même **et** ses deux extrémités le sont ; le prédicat est dans le terme récursif des CTE.
- `as_of` absent = comportement actuel strictement inchangé (`visible_at(..., None) == []`, aucune jointure ajoutée).
- `as_of` est un `date` typé, valeur liée (jamais de texte libre dans le SQL) ; la clé `'valid_from'` est une constante inlinée avec `literal_column` pour que l'expression corresponde à celle de l'index.
- Les bornes se déclarent une fois dans `backend/src/ea/api/schemas.py` (`AsOf`) et le MCP les importe.
- Chaque méthode publique de `ArchitectureService` commence par `require_caller()` (lecture) ou `require_editor()` (`create_gap`, après quoi viennent les autres instructions) ; `tests/unit/test_service_guards.py` l'impose (AST).
- Refus : `InvalidLifespanError` → 422 `invalid_lifespan` ; `DuplicateGapError` → 409 `duplicate_gap` ; `a == b`, élément qui n'est pas un plateau, fenêtre trop large → `ValueError` → 422 `invalid_input` ; plateau inconnu → `ElementNotFoundError` → 404.
- `MAX_ROADMAP_DAYS = 7320`, `MAX_PLATEAUS = 500`, `MAX_ROADMAP_ITEMS = 1000`.
- Appartenance à un plateau **calculée** (cible d'une `composition` ou `aggregation` partant du plateau), jamais stockée ; un gap = élément `gap` ordinaire relié par `association` (source gap, cible plateau) aux deux plateaux ; aucune table nouvelle.
- Aucune règle de métamodèle copiée dans le SPA ; SVG fait main, aucune bibliothèque de graphes ni de sélecteur de date (`<input type="date">`).
- État dans l'URL : `?asof=YYYY-MM-DD` (changer = `replace`), `?a=` / `?b=` pour l'écart ; toute lecture passe par `useLatestRequest()`.
- Écriture réservée à `ea-editor` : le SPA masque « Créer l'écart » pour un lecteur (`useMe().canWrite`), l'API décide.
- `make openapi` après tout changement de schéma ; `make check` vert à la fin de chaque lot ; `make test-integration` quand une migration ou du SQL entre.
- Commits Conventional Commits en anglais, terminés par `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Texte d'interface et ADR en français ; code, identifiants, commits en anglais.

## Review Focus

1. **Dates vides ou partielles.** `valid_until: ""` doit être retiré (sinon l'élément disparaît de toute lecture datée), `2027-06` et `2027-Q2` refusés, `valid_until < valid_from` refusé aussi par `PATCH` et par `POST /relationships`. Test : Task 1 (domaine) et Task 2 (portes d'écriture).
2. **Extrémité invisible et racine invisible.** Une relation sans date dont une extrémité est hors période disparaît ; un voisin retiré **coupe** le chemin (pas de saut par-dessus) ; une racine hors période donne un graphe vide, pas un 404. Test : Task 9 (PostgreSQL).
3. **`as_of` absent = SQL inchangé.** Aucune condition, aucune jointure en plus ; mêmes résultats sur un graphe peuplé avec et sans `as_of=None`. Test : Task 5 (`visible_at`) et Task 9 (régression sur le graphe).
4. **`PATCH` remplace toute la map `properties` et efface donc les dates.** Le formulaire du SPA doit renvoyer `valid_from` / `valid_until` à chaque enregistrement même quand on ne les touche pas, et ne les envoyer que non vides. Test : Task 20.
5. **Garde-fous de l'écart et de la frise.** `a == b`, élément qui n'est pas un plateau, gap déjà présent pour le couple, lecteur qui crée, fenêtre > 20 ans, gap orphelin si une `association` échoue (nettoyage). Test : Task 12 (service) et Task 13 (API).

---

## Carte des fichiers

| Fichier | Action | Tâche | Rôle |
|---|---|---|---|
| `backend/src/ea/domain/timeline.py` | Create | 1, 11 | `Lifespan`, validation, écart, frise (pur) |
| `backend/src/ea/domain/errors.py` | Modify | 1, 12 | `InvalidLifespanError`, `DuplicateGapError` |
| `backend/src/ea/api/errors.py` | Modify | 1, 12 | table `_STATUS` |
| `backend/src/ea/services/architecture.py` | Modify | 2, 7, 12 | validation d'écriture, `as_of`, plateaux / écart / frise |
| `backend/src/ea/db/models/architecture.py` | Modify | 3 | `Index` déclarés (4) |
| `backend/migrations/versions/0009_validity_indexes.py` | Create | 3 | contrôle des données + 4 index |
| `backend/src/ea/domain/ports.py` | Modify | 5, 6 | `ElementFilter.as_of`, signatures `as_of` |
| `backend/src/ea/repositories/architecture_store.py` | Modify | 5, 6 | `visible_at`, CTE, listes |
| `backend/src/ea/domain/diagrams.py` | Modify | 7 | `DiagramDetail.inactive_*` |
| `backend/src/ea/services/diagrams.py` | Modify | 7 | `open(..., as_of)` |
| `backend/src/ea/api/schemas.py` | Modify | 7, 13 | `AsOf`, `DiagramRead`, lectures plateau / frise |
| `backend/src/ea/api/architecture.py` | Modify | 7 | paramètre `as_of` |
| `backend/src/ea/api/diagrams.py` | Modify | 7 | paramètre `as_of` |
| `backend/src/ea/api/timeline.py` | Create | 13 | `/plateaus`, `/plateaus/gap`, `/roadmap` |
| `backend/src/ea/main.py` | Modify | 13 | `include_router(timeline_router, ...)` |
| `backend/src/ea/mcp/server.py` | Modify | 8, 14 | `as_of`, `list_plateaus`, `gap_between`, `INSTRUCTIONS` |
| `backend/tests/conftest.py` | Modify | 5, 11 | double `InMemoryRepository`, helper `an_element` |
| `backend/tests/unit/test_timeline.py` | Create | 1, 11 | domaine |
| `backend/tests/unit/test_lifespan_writes.py` | Create | 2 | portes d'écriture |
| `backend/tests/unit/test_visibility_sql.py` | Create | 5 | `visible_at` |
| `backend/tests/unit/test_timeline_service.py` | Create | 12 | plateaux / écart / frise |
| `backend/tests/unit/test_migrations.py`, `test_service_guards.py`, `test_mcp_server.py` | Modify | 3, 12, 8, 14 | ajouts |
| `backend/tests/e2e/test_timeline_api.py` | Create | 2, 7, 13 | contrat HTTP |
| `backend/tests/integration/test_lifespan_indexes.py`, `test_as_of_graph.py`, `test_plateau_gap_graph.py` | Create | 3, 9, 15 | PostgreSQL |
| `docs/adr/0042-…`, `0043-…`, `0044-…` | Create | 4, 10, 15 | ADR |
| `frontend/src/lib/asof.ts` | Create | 16 | lecture / écriture de `?asof=` |
| `frontend/src/router/index.ts` | Modify | 16 | garde de propagation |
| `frontend/src/components/AsOfPicker.vue`, `frontend/src/App.vue` | Create / Modify | 17 | sélecteur + bandeau |
| `frontend/src/features/{elements,neighbourhood,impact}/…` | Modify | 18 | `asOf` |
| `frontend/src/features/diagrams/{useDiagram.ts,DiagramCanvas.vue,DiagramsSection.vue}` | Modify | 19 | grisage |
| `frontend/src/features/elements/ElementForm.vue` | Modify | 20 | champs date |
| `frontend/src/features/roadmap/{useGap.ts,GapPanel.vue,RoadmapSection.vue}` | Create | 21, 22 | écart |
| `frontend/src/router/sections.ts` | Modify | 21 | entrée « Feuille de route » |
| `frontend/src/lib/roadmapLayout.ts` | Create | 23 | géométrie pure |
| `frontend/src/features/roadmap/useRoadmap.ts`, `frontend/src/components/RoadmapChart.vue` | Create | 24, 25 | frise |
| `frontend/tests/…` | Create / Modify | 16–25 | specs |
| `CLAUDE.md` | Modify | 26 | une phrase |

---

# Lot 1 — Domaine, validation d'écriture, migration d'index

### Task 1: `domain/timeline.py` — `Lifespan` et `validate_lifespan_properties`

**Files:**
- Create: `backend/src/ea/domain/timeline.py`
- Modify: `backend/src/ea/domain/errors.py` (ajouter après `NotASubnetError`, l. 74-76)
- Modify: `backend/src/ea/api/errors.py` (import l. 24-52 ; table `_STATUS`, après l'entrée `IllegalRelationshipError`, l. 72-75)
- Test: `backend/tests/unit/test_timeline.py`

**Interfaces:**
- Consumes: `ea.domain.errors.DomainError`.
- Produces (Python) :
  - `VALID_FROM: Final = "valid_from"`, `VALID_UNTIL: Final = "valid_until"`
  - `class Lifespan` (frozen dataclass) : `start: date | None = None`, `end: date | None = None` ; `Lifespan.read(properties: Mapping[str, str]) -> Lifespan` ; `covers(day: date) -> bool` ; `overlaps(other: Lifespan) -> bool` ; propriété `is_dated -> bool`
  - `parse_day(value: str, *, key: str) -> date`
  - `validate_lifespan_properties(properties: Mapping[str, str] | None) -> Mapping[str, str] | None`
  - `class InvalidLifespanError(DomainError)`
- Produces (TypeScript) : rien.

- [ ] **Step 1: Écrire le test qui échoue** — créer `backend/tests/unit/test_timeline.py` :

```python
"""Time in the model: lifespans and their validation, with no database."""

from __future__ import annotations

from datetime import date

import pytest

from ea.domain.errors import InvalidLifespanError
from ea.domain.timeline import (
    VALID_FROM,
    VALID_UNTIL,
    Lifespan,
    validate_lifespan_properties,
)


class TestLifespan:
    def test_both_bounds_are_inclusive(self) -> None:
        span = Lifespan(date(2026, 1, 1), date(2026, 12, 31))

        assert span.covers(date(2026, 1, 1))
        assert span.covers(date(2026, 12, 31))
        assert not span.covers(date(2025, 12, 31))
        assert not span.covers(date(2027, 1, 1))

    def test_an_open_end_covers_everything_after_the_start(self) -> None:
        assert Lifespan(date(2026, 1, 1), None).covers(date(2099, 1, 1))
        assert not Lifespan(date(2026, 1, 1), None).covers(date(2025, 1, 1))

    def test_an_open_start_covers_everything_before_the_end(self) -> None:
        assert Lifespan(None, date(2026, 1, 1)).covers(date(1990, 1, 1))
        assert not Lifespan(None, date(2026, 1, 1)).covers(date(2026, 1, 2))

    def test_an_undated_thing_is_always_there(self) -> None:
        assert Lifespan().covers(date(1900, 1, 1))
        assert Lifespan().covers(date(2999, 1, 1))
        assert not Lifespan().is_dated

    def test_two_lifespans_sharing_one_day_overlap(self) -> None:
        first = Lifespan(date(2026, 1, 1), date(2026, 6, 30))
        second = Lifespan(date(2026, 6, 30), date(2026, 12, 31))

        assert first.overlaps(second) and second.overlaps(first)

    def test_two_disjoint_lifespans_do_not_overlap(self) -> None:
        first = Lifespan(date(2026, 1, 1), date(2026, 6, 29))
        second = Lifespan(date(2026, 6, 30), date(2026, 12, 31))

        assert not first.overlaps(second)

    def test_an_open_lifespan_overlaps_whatever_it_reaches(self) -> None:
        assert Lifespan().overlaps(Lifespan(date(2026, 1, 1), date(2026, 2, 1)))
        assert Lifespan(date(2030, 1, 1), None).overlaps(Lifespan(None, date(2031, 1, 1)))

    def test_it_is_read_from_the_properties(self) -> None:
        span = Lifespan.read({VALID_FROM: "2026-03-01", VALID_UNTIL: "2027-06-30", "owner": "x"})

        assert span == Lifespan(date(2026, 3, 1), date(2027, 6, 30))

    def test_a_blank_property_reads_as_open(self) -> None:
        assert Lifespan.read({VALID_FROM: "  ", VALID_UNTIL: ""}) == Lifespan()

    def test_a_malformed_stored_value_is_named_not_swallowed(self) -> None:
        with pytest.raises(InvalidLifespanError, match=VALID_FROM):
            Lifespan.read({VALID_FROM: "2027-06"})


class TestValidation:
    def test_nothing_to_validate_comes_back_as_it_came(self) -> None:
        assert validate_lifespan_properties(None) is None
        assert validate_lifespan_properties({}) == {}

    def test_other_properties_are_left_alone(self) -> None:
        assert validate_lifespan_properties({"owner": "finance"}) == {"owner": "finance"}

    def test_a_valid_pair_is_stored_in_its_canonical_form(self) -> None:
        stored = validate_lifespan_properties(
            {VALID_FROM: " 2026-03-01 ", VALID_UNTIL: "20270630", "owner": "x"}
        )

        assert stored == {VALID_FROM: "2026-03-01", VALID_UNTIL: "2027-06-30", "owner": "x"}

    @pytest.mark.parametrize("partial", ["2027-06", "2027-Q2", "2027", "demain", "2027-13-01"])
    def test_a_partial_or_impossible_date_is_refused(self, partial: str) -> None:
        with pytest.raises(InvalidLifespanError, match=VALID_UNTIL):
            validate_lifespan_properties({VALID_UNTIL: partial})

    def test_an_end_before_the_start_is_refused(self) -> None:
        with pytest.raises(InvalidLifespanError, match="before"):
            validate_lifespan_properties({VALID_FROM: "2027-01-02", VALID_UNTIL: "2027-01-01"})

    def test_a_one_day_lifespan_is_legal(self) -> None:
        stored = validate_lifespan_properties({VALID_FROM: "2027-01-01", VALID_UNTIL: "2027-01-01"})

        assert stored == {VALID_FROM: "2027-01-01", VALID_UNTIL: "2027-01-01"}

    def test_a_blank_date_is_removed_not_stored(self) -> None:
        """`"" >= '2027-01-01'` is false: a stored blank would hide the element from every dated read."""
        stored = validate_lifespan_properties({VALID_FROM: "", VALID_UNTIL: "  ", "owner": "x"})

        assert stored == {"owner": "x"}
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_timeline.py -x` → `ModuleNotFoundError: No module named 'ea.domain.timeline'`.

- [ ] **Step 3: Implémenter** — d'abord l'erreur. Dans `backend/src/ea/domain/errors.py`, après la classe `NotASubnetError` :

```python
class InvalidLifespanError(DomainError):
    """A validity date that is not a day written YYYY-MM-DD, or an end before its start.

    The model keeps `valid_from` / `valid_until` as ISO strings of fixed width so
    that sorting the strings is sorting the days (docs/adr/0042); a partial date
    such as `2027-06` would break exactly that, so it is refused at the door.
    """
```

Dans `backend/src/ea/api/errors.py` : ajouter `InvalidLifespanError,` à l'import (ordre alphabétique, après `InvalidFileMetadataError`), puis dans `_STATUS`, après l'entrée `IllegalRelationshipError` :

```python
    # --- Time in the model (docs/adr/0042) ---
    InvalidLifespanError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_lifespan"),
```

Puis créer `backend/src/ea/domain/timeline.py` :

```python
"""Time in the model: when a thing is true in the world.

An element or a relationship may carry `valid_from` and `valid_until` in its
`properties`, as `YYYY-MM-DD`. `valid_until` is inclusive, and a missing bound is
an open one ("since forever", "no end planned"). That is the *validity* axis —
when the thing holds in the world; the moment the model learnt it (the
*transaction* axis) is another subject. See docs/adr/0042.

Pure: no framework, no clock. The convention is checked wherever properties are
written, exactly like `validate_ipam_properties`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from ea.domain.errors import InvalidLifespanError

VALID_FROM: Final = "valid_from"
VALID_UNTIL: Final = "valid_until"


def parse_day(value: str, *, key: str) -> date:
    """A day written `YYYY-MM-DD` — or refuse, naming the key that held it."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        msg = f"{key} must be a day written YYYY-MM-DD, not {value.strip()[:40]!r}"
        raise InvalidLifespanError(msg) from None


def _read(properties: Mapping[str, str], key: str) -> date | None:
    raw = properties.get(key, "").strip()
    return parse_day(raw, key=key) if raw else None


@dataclass(frozen=True, slots=True)
class Lifespan:
    """The days a thing holds: both ends inclusive, `None` meaning open."""

    start: date | None = None
    end: date | None = None

    @classmethod
    def read(cls, properties: Mapping[str, str]) -> Lifespan:
        return cls(_read(properties, VALID_FROM), _read(properties, VALID_UNTIL))

    @property
    def is_dated(self) -> bool:
        return self.start is not None or self.end is not None

    def covers(self, day: date) -> bool:
        return (self.start is None or self.start <= day) and (self.end is None or day <= self.end)

    def overlaps(self, other: Lifespan) -> bool:
        return (self.start is None or other.end is None or self.start <= other.end) and (
            other.start is None or self.end is None or other.start <= self.end
        )


def validate_lifespan_properties(
    properties: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    """Refuse a validity date that is malformed or out of order; return what to store.

    Same contract as `validate_ipam_properties`: the properties come back with
    both dates in their one canonical spelling, because the database compares
    stored strings. A blank date is *removed*: `"" >= '2027-01-01'` is false, so
    a stored blank end would silently hide the element from every dated read.
    """
    if not properties:
        return properties
    canonical = dict(properties)
    days: dict[str, date] = {}
    for key in (VALID_FROM, VALID_UNTIL):
        raw = properties.get(key)
        if raw is None:
            continue
        if not raw.strip():
            del canonical[key]
            continue
        days[key] = parse_day(raw, key=key)
        canonical[key] = days[key].isoformat()
    if VALID_FROM in days and VALID_UNTIL in days and days[VALID_UNTIL] < days[VALID_FROM]:
        msg = (
            f"{VALID_UNTIL} ({days[VALID_UNTIL].isoformat()}) is before "
            f"{VALID_FROM} ({days[VALID_FROM].isoformat()})"
        )
        raise InvalidLifespanError(msg)
    return canonical
```

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_timeline.py -x` → tout vert. Puis `uv run mypy src` sans erreur.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/timeline.py backend/src/ea/domain/errors.py backend/src/ea/api/errors.py backend/tests/unit/test_timeline.py
git commit -m "feat(domain): lifespan of elements and relationships" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 2: Valider les dates à toutes les portes d'écriture

**Files:**
- Modify: `backend/src/ea/services/architecture.py` (imports l. 20-31 ; `create_element` l. 59-69 ; `update_element` l. 108-125 ; `connect` l. 178-192)
- Test: `backend/tests/unit/test_lifespan_writes.py` ; `backend/tests/e2e/test_timeline_api.py` (création)

**Interfaces:**
- Consumes: `validate_lifespan_properties` (Task 1).
- Produces : aucune signature nouvelle ; `create_element`, `update_element`, `connect` refusent par `InvalidLifespanError` et stockent la forme canonique.

- [ ] **Step 1: Tests qui échouent** — `backend/tests/unit/test_lifespan_writes.py` :

```python
"""Every door that writes properties refuses a bad validity date — PATCH included."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import InvalidLifespanError
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository

pytestmark = pytest.mark.asyncio


async def test_creating_with_a_partial_date_stores_nothing(
    service: ArchitectureService, repository: InMemoryRepository
) -> None:
    with pytest.raises(InvalidLifespanError):
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing", properties={"valid_from": "2027-06"}
        )

    assert repository.elements == {}


async def test_creating_stores_the_canonical_dates(service: ArchitectureService) -> None:
    created = await service.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        properties={"valid_from": "20240301", "valid_until": ""},
    )

    assert dict(created.properties) == {"valid_from": "2024-03-01"}


async def test_patching_is_not_a_back_door(service: ArchitectureService) -> None:
    element = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

    with pytest.raises(InvalidLifespanError):
        await service.update_element(
            element.id, properties={"valid_from": "2027-01-02", "valid_until": "2027-01-01"}
        )

    assert dict((await service.get_element(element.id)).properties) == {}


async def test_an_update_that_leaves_properties_alone_checks_nothing(
    service: ArchitectureService,
) -> None:
    element = await service.create_element(
        element_type=E.APPLICATION_COMPONENT, name="Billing", properties={"valid_from": "2026-01-01"}
    )

    renamed = await service.update_element(element.id, name="Invoicing")

    assert dict(renamed.properties) == {"valid_from": "2026-01-01"}


async def test_a_relationship_carries_a_lifespan_too(service: ArchitectureService) -> None:
    source = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="A")
    target = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="B")

    with pytest.raises(InvalidLifespanError):
        await service.connect(
            relationship_type=R.SERVING,
            source_id=source.id,
            target_id=target.id,
            properties={"valid_until": "bientôt"},
        )

    link = await service.connect(
        relationship_type=R.SERVING,
        source_id=source.id,
        target_id=target.id,
        properties={"valid_until": "20271231"},
    )
    assert dict(link.properties) == {"valid_until": "2027-12-31"}
```

`backend/tests/e2e/test_timeline_api.py` (nouveau fichier ; il grossira aux Tasks 7 et 13) :

```python
"""The time endpoints and the `as_of` parameter, end to end against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(service: ArchitectureService) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(debug=True, auth_enabled=False), architecture_service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def create(
    client: httpx.AsyncClient, element_type: str, name: str, **properties: str
) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestWritingDates:
    async def test_a_patch_with_an_impossible_lifespan_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await create(client, "application_component", "Billing")

        response = await client.patch(
            f"/elements/{element['id']}",
            json={"properties": {"valid_from": "2027-06"}},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_lifespan"
        assert "valid_from" in response.json()["detail"]

    async def test_a_post_with_an_end_before_the_start_is_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/elements",
            json={
                "element_type": "application_component",
                "name": "Billing",
                "properties": {"valid_from": "2027-02-01", "valid_until": "2027-01-01"},
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_lifespan"
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_lifespan_writes.py tests/e2e/test_timeline_api.py -x` → `Failed: DID NOT RAISE InvalidLifespanError`.

- [ ] **Step 3: Implémenter** — dans `backend/src/ea/services/architecture.py` :

Import (après `from ea.domain.model import Element, Relationship`) :

```python
from ea.domain.timeline import validate_lifespan_properties
```

`create_element`, remplacer `properties = validate_ipam_properties(element_type, properties)` par :

```python
        properties = validate_lifespan_properties(
            validate_ipam_properties(element_type, properties)
        )
```

`update_element`, remplacer `properties = validate_ipam_properties(current.element_type, properties)` par :

```python
        properties = validate_lifespan_properties(
            validate_ipam_properties(current.element_type, properties)
        )
```

`connect`, juste après `require_editor()` (la première instruction reste l'appel de garde) :

```python
        require_editor()
        properties = validate_lifespan_properties(properties)
```

- [ ] **Step 4: Relancer, PASS** — mêmes commandes ; puis `uv run pytest tests/unit/test_architecture_service.py tests/unit/test_service_guards.py -x` (non-régression).

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/architecture.py backend/tests/unit/test_lifespan_writes.py backend/tests/e2e/test_timeline_api.py
git commit -m "feat(services): validate validity dates on every write door" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 3: Migration `0009` — contrôle des données et quatre index d'expression

**Files:**
- Create: `backend/migrations/versions/0009_validity_indexes.py`
- Modify: `backend/src/ea/db/models/architecture.py` (imports l. 17-30 ; `ElementRecord.__table_args__` l. 38-57 ; `RelationshipRecord` l. 71-90)
- Test: `backend/tests/unit/test_migrations.py` (ajout en fin de fichier) ; `backend/tests/integration/test_lifespan_indexes.py`

**Interfaces:**
- Consumes: rien.
- Produces : index `ix_elements_valid_from`, `ix_elements_valid_until`, `ix_relationships_valid_from`, `ix_relationships_valid_until` (b-tree d'expression partiels `WHERE (properties ->> '<clé>') IS NOT NULL`) ; révision `0009` (down `0008`) qui **refuse** de s'appliquer si une valeur existante n'est pas un jour canonique.

- [ ] **Step 1: Tests qui échouent** — ajouter à la fin de `backend/tests/unit/test_migrations.py` :

```python
def test_the_validity_indexes_are_declared_on_the_models_and_follow_0008() -> None:
    """The four expression indexes of docs/adr/0042 are in the metadata, so autogenerate keeps them."""
    declared = {
        index.name
        for table in ("elements", "relationships")
        for index in Base.metadata.tables[table].indexes
    }

    assert {
        "ix_elements_valid_from",
        "ix_elements_valid_until",
        "ix_relationships_valid_from",
        "ix_relationships_valid_until",
    } <= declared
    revision = ScriptDirectory.from_config(_config()).get_revision("0009")
    assert revision is not None and revision.down_revision == "0008"
```

`backend/tests/integration/test_lifespan_indexes.py` :

```python
"""Revision 0009: the validity indexes, and the refusal to index malformed dates."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

INDEXES = {
    "ix_elements_valid_from",
    "ix_elements_valid_until",
    "ix_relationships_valid_from",
    "ix_relationships_valid_until",
}


async def _index_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            # A catalogue read with no runtime value: nothing to bind.
            text("SELECT indexname FROM pg_indexes WHERE tablename IN ('elements', 'relationships')")
        )
    return {row[0] for row in rows}


async def test_head_carries_the_four_indexes(engine_at_head: AsyncEngine) -> None:
    assert INDEXES <= await _index_names(engine_at_head)


async def test_a_range_on_valid_from_can_be_served_by_its_index(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    await graph_service.create_element(
        element_type=E.PLATEAU, name="To-Be", properties={"valid_from": "2027-01-01"}
    )

    async with engine_at_head.begin() as connection:
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await connection.execute(
            # Literal values only: EXPLAIN of the range the roadmap asks for.
            text(
                "EXPLAIN SELECT id FROM elements "
                "WHERE (properties ->> 'valid_from') <= '2027-06-01'"
            )
        )
        rendered = " ".join(row[0] for row in plan)

    assert "ix_elements_valid_from" in rendered


async def test_the_chain_goes_down_and_up_again_on_a_populated_base(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0008")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                # Test setup with literal values only: one element carrying a good date.
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                    "'active_structure', 'db-01', '{\"valid_from\": \"2026-01-01\"}'::jsonb, "
                    "now(), now())"
                )
            )
        await asyncio.to_thread(command.upgrade, alembic_config, "0009")
        assert INDEXES <= await _index_names(postgres_engine)

        await asyncio.to_thread(command.downgrade, alembic_config, "0008")
        assert not INDEXES & await _index_names(postgres_engine)
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_0009_refuses_a_stored_date_that_is_not_canonical(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0008")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                # Test setup with literal values only: a partial date written before the rule.
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                    "'active_structure', 'db-01', '{\"valid_until\": \"2027-06\"}'::jsonb, "
                    "now(), now())"
                )
            )

        with pytest.raises(Exception, match="valid_until"):
            await asyncio.to_thread(command.upgrade, alembic_config, "0009")
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_migrations.py -x` → échec sur `ix_elements_valid_from` absent. (Les tests d'intégration sont lancés à la Task 4.)

- [ ] **Step 3: Implémenter.**

`backend/src/ea/db/models/architecture.py` — dans `ElementRecord.__table_args__`, après l'index `uq_elements_vrf_cidr` :

```python
        # The validity dates of docs/adr/0042, as fixed-width ISO strings: the
        # order of the strings is the order of the days. Partial, so an element
        # without a date costs the index nothing.
        Index(
            "ix_elements_valid_from",
            text("(properties ->> 'valid_from')"),
            postgresql_where=text("(properties ->> 'valid_from') IS NOT NULL"),
        ),
        Index(
            "ix_elements_valid_until",
            text("(properties ->> 'valid_until')"),
            postgresql_where=text("(properties ->> 'valid_until') IS NOT NULL"),
        ),
```

Dans `RelationshipRecord`, juste sous `__tablename__ = "relationships"` :

```python
    __table_args__ = (
        Index(
            "ix_relationships_valid_from",
            text("(properties ->> 'valid_from')"),
            postgresql_where=text("(properties ->> 'valid_from') IS NOT NULL"),
        ),
        Index(
            "ix_relationships_valid_until",
            text("(properties ->> 'valid_until')"),
            postgresql_where=text("(properties ->> 'valid_until') IS NOT NULL"),
        ),
    )
```

`backend/migrations/versions/0009_validity_indexes.py` :

```python
"""les dates de validité des éléments et des relations : quatre index d'expression

Revision ID: 0009
Revises: 0008
Date: 2026-10-10

Aucune colonne : `valid_from` et `valid_until` sont des clés de `properties`
(docs/adr/0042), au format `YYYY-MM-DD` de largeur fixe, dont l'ordre
lexicographique est l'ordre chronologique. Cette révision ne fait que deux
choses, toutes deux sans risque sur une base peuplée :

1. refuser de s'appliquer si une valeur déjà stockée n'est pas un jour canonique
   — un index sur des chaînes mal formées trierait mal sans rien dire ;
2. créer les quatre index partiels (`WHERE ... IS NOT NULL`), réversibles.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("elements", "relationships")
_KEYS = ("valid_from", "valid_until")


def _refuse_non_canonical_days() -> None:
    connection = op.get_bind()
    refused: list[str] = []
    for table in _TABLES:
        properties = sa.column("properties", postgresql.JSONB)
        for key in _KEYS:
            value = properties.op("->>")(key)
            rows = connection.execute(
                sa.select(sa.distinct(value))
                .select_from(sa.table(table, properties))
                .where(value.is_not(None))
            )
            for (stored,) in rows:
                try:
                    canonical = date.fromisoformat(stored).isoformat() == stored
                except ValueError:
                    canonical = False
                if not canonical:
                    refused.append(f"{table}.{key}={stored!r}")
    if refused:
        msg = (
            "revision 0009 refuses to index validity dates that are not YYYY-MM-DD: "
            + ", ".join(sorted(refused)[:10])
            + ". Correct them (PATCH the element) and run the migration again."
        )
        raise RuntimeError(msg)


def upgrade() -> None:
    _refuse_non_canonical_days()
    # Du DDL statique : aucune valeur d'exécution.
    for table in _TABLES:
        for key in _KEYS:
            op.create_index(
                f"ix_{table}_{key}",
                table,
                [sa.text(f"(properties ->> '{key}')")],
                postgresql_where=sa.text(f"(properties ->> '{key}') IS NOT NULL"),
            )


def downgrade() -> None:
    for table in _TABLES:
        for key in _KEYS:
            op.drop_index(f"ix_{table}_{key}", table_name=table)
```

- [ ] **Step 4: Relancer, PASS (sans base)** — `cd backend && uv run pytest tests/unit/test_migrations.py -x` et `uv run mypy src migrations`. L'intégration est lancée en Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0009_validity_indexes.py backend/src/ea/db/models/architecture.py backend/tests/unit/test_migrations.py backend/tests/integration/test_lifespan_indexes.py
git commit -m "feat(db): index validity dates, refuse to migrate malformed ones" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 4: ADR « temps de validité » et vérification du lot 1

**Files:**
- Create: `docs/adr/0042-temps-de-validite-dans-les-proprietes.md`

**Interfaces:** aucune.

- [ ] **Step 1: Rédiger l'ADR** — `docs/adr/0042-temps-de-validite-dans-les-proprietes.md` :

````markdown
---
titre: Le temps de validité vit dans les propriétés
date: 2026-10-10
statut: Proposition
affects: domain/timeline.py, services/architecture.py, db/models/architecture.py, migrations/versions/0009_validity_indexes.py
---

# Le temps de validité vit dans les propriétés

## Contexte

Le métamodèle porte déjà le vocabulaire de l'évolution du SI (`plateau`, `work_package`, `deliverable`, `gap`), mais aucun élément n'a de date : on ne peut dire ni « cette application est en service du 2024-03-01 au 2027-06-30 », ni « le To-Be est atteint en 2028 ». Il faut un temps, sans nouvelle famille de tables ni nouvelle règle de métamodèle.

Deux temps existent et sont indépendants. Le **temps de validité** est celui du monde (l'application X est en service entre deux dates). Le **temps de transaction** est celui du modèle (saisi le 2026-10-10). On peut saisir aujourd'hui une mise hors service de 2028 (validité future, transaction présente), ou corriger en 2026 une date de 2024. Cette décision ne livre que le premier axe ; une future baseline portera le second, et les deux se composeront (`known_at`, nom réservé, non livré).

## Décision

- Un élément ou une relation porte **`valid_from`** et **`valid_until`** dans `properties`, au format `YYYY-MM-DD` canonique. `valid_until` est **inclusive** ; une borne absente est ouverte. Un seul axe de validité, pas d'historique des versions d'un fait.
- `domain/timeline.py` (pur) définit `Lifespan` (`read`, `covers`, `overlaps`) et `validate_lifespan_properties`, qui suit le contrat de `validate_ipam_properties` : refuse une date mal formée ou `valid_until < valid_from` (`InvalidLifespanError`, 422), renvoie les propriétés sous leur forme canonique, **retire** une date vide (une chaîne vide stockée cacherait l'élément de toute lecture datée).
- `ArchitectureService.create_element`, `update_element` et `connect` l'appellent à côté de l'IPAM : `PATCH /elements/{id}` n'est pas une porte dérobée, et l'import du pipeline (qui passe par l'API) est validé de même.
- Le format de largeur fixe est ce qui rend le stockage en chaîne légitime : l'ordre lexicographique est l'ordre chronologique pour une même largeur, sous n'importe quelle collation. Le format partiel (`2027-06`, `2027-Q2`) est refusé précisément parce qu'il casserait cette propriété.
- La révision Alembic `0009` crée quatre index d'expression partiels (`properties ->> 'valid_from'` et `'valid_until'`, sur `elements` et `relationships`, `WHERE ... IS NOT NULL`) et **refuse de s'appliquer** si une valeur déjà stockée n'est pas un jour canonique. Aucune colonne ajoutée : sans risque sur une base peuplée.
- L'appartenance à un plateau reste **calculée** (cible d'une `composition` ou `aggregation` partant du plateau), jamais stockée en double.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| (a) dates dans `properties` | Même schéma que `cidr` / `ip_address` (ADR 0020) ; visible partout (catalogue, voisinage, impact, MCP, import) ; une migration d'index seulement | Chaînes : la validité repose sur la validation d'écriture | Retenu |
| (b) table `element_lifespans` | `DATE` natif, clé étrangère | Une jointure dans chaque CTE, un dépôt, une cascade, un ADR de stockage | Écarté |
| Bitemporel complet | Réponse à « tel que nous le croyions le… » | Hors besoin ; le temps de transaction est un autre sous-projet | Écarté |
| Index partiels vs non partiels | Partiel : taille nulle pour les éléments non datés | Un index partiel ne sert pas `IS NULL OR …` | Partiel retenu, à mesurer (voir ADR 0043) |

## Conséquences

- La validité des dates repose sur la validation d'écriture. Tout nouveau chemin d'écriture de `properties` doit appeler `validate_lifespan_properties`.
- `PATCH` remplace toute la map `properties` : un client qui n'envoie pas les dates les efface. Le SPA renvoie la map complète ; le pipeline n'écrit jamais `properties`.
- La migration `0009` échoue bruyamment sur une base qui contiendrait déjà une date non canonique ; il faut la corriger puis relancer.
- Granularité : le jour. Les trimestres et les mois sont affichés par l'échelle de la frise, jamais stockés.

## Références

- ADR liés : [0020](0020-adressage-ip-dans-les-proprietes.md) (convention de propriétés), [0033](0033-postgresql-pour-tout-le-modele.md) (le graphe)
- Code concerné : `backend/src/ea/domain/timeline.py`, `backend/src/ea/services/architecture.py`, `backend/migrations/versions/0009_validity_indexes.py`
````

- [ ] **Step 2: Corriger les références** — `ls docs/adr | grep -E "^(0020|0033)"` ; remplacer les deux noms de fichier de la section Références par les vrais (et `0042` si ce numéro est pris : `ls docs/adr | tail -3`, renuméroter le fichier et le champ `affects`).

- [ ] **Step 3: Vérifier le lot** — `make test-unit lint-check typecheck` puis `make test-integration` (nécessite `docker` : lance PostgreSQL et MinIO jetables) ; attendu : `tests/integration/test_lifespan_indexes.py` passe (4 tests), reste inchangé ailleurs. Puis `make check`.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0042-temps-de-validite-dans-les-proprietes.md
git commit -m "docs(adr): validity time lives in properties" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 2 — Le paramètre `as_of` sur les lectures

### Task 5: `visible_at`, `ElementFilter.as_of`, `list_elements` / `count_elements` / `list_relationships`

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (imports l. 9-12 ; `ElementFilter` l. 44-52 ; `list_relationships` l. 82-90)
- Modify: `backend/src/ea/repositories/architecture_store.py` (imports l. 15-45 ; `_matching` l. 197-207 ; `list_relationships` l. 395-417)
- Modify: `backend/tests/conftest.py` (`InMemoryRepository`, l. 258-424)
- Test: `backend/tests/unit/test_visibility_sql.py`

**Interfaces:**
- Consumes: `VALID_FROM`, `VALID_UNTIL` (Task 1).
- Produces (Python) :
  - `ElementFilter(..., as_of: date | None = None)`
  - `visible_at(record: Any, as_of: date | None) -> list[ColumnElement[bool]]` (dans `architecture_store.py`) — `[]` si `as_of is None`
  - `ArchitectureRepository.list_relationships(*, element_id=None, relationship_types=(), limit=50, offset=0, as_of: date | None = None)`
  - double : `InMemoryRepository.last_as_of: date | None`

- [ ] **Step 1: Test qui échoue** — `backend/tests/unit/test_visibility_sql.py` :

```python
"""The one predicate that decides what is visible on a day — compiled, not executed."""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.repositories.architecture_store import visible_at


def test_no_day_asked_adds_no_condition_at_all() -> None:
    """The regression guarantee: without `as_of` the SQL is the SQL it always was."""
    assert visible_at(ElementRecord, None) == []
    assert visible_at(RelationshipRecord, None) == []


def test_a_day_asked_compares_both_ends_with_the_day_bound_not_inlined() -> None:
    clauses = visible_at(ElementRecord, date(2027, 1, 1))

    compiled = and_(*clauses).compile(dialect=postgresql.dialect())

    sql = str(compiled)
    assert "elements.properties ->> 'valid_from'" in sql
    assert "elements.properties ->> 'valid_until'" in sql
    assert "IS NULL" in sql
    assert "2027-01-01" not in sql
    assert "2027-01-01" in compiled.params.values()


def test_the_same_predicate_reads_a_relationship() -> None:
    sql = str(
        and_(*visible_at(RelationshipRecord, date(2027, 1, 1))).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "relationships.properties ->> 'valid_until'" in sql
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_visibility_sql.py -x` → `ImportError: cannot import name 'visible_at'`.

- [ ] **Step 3: Implémenter.**

`ports.py` : ajouter `from datetime import date, datetime` (remplace `from datetime import datetime`) ; dans `ElementFilter` ajouter, après `offset: int = 0` :

```python
    #: Read the model as it is on this day (docs/adr/0043); `None` filters nothing.
    as_of: date | None = None
```

et dans le protocole, `list_relationships` gagne `as_of: date | None = None,` après `offset: int = 0,`.

`architecture_store.py` : imports — ajouter `from datetime import date` sous `import logging` côté exécution **n'est pas nécessaire** pour `visible_at` (la valeur est utilisée telle quelle) mais l'annotation oui : ajouter `from datetime import date` dans le bloc `if TYPE_CHECKING:`; ajouter `from ea.domain.timeline import VALID_FROM, VALID_UNTIL`. Après `_text_of` (l. 224), ajouter :

```python
def _day_of(record: Any, key: str) -> ColumnElement[str]:
    """`properties ->> 'key'` with the key inlined — a constant of ours, not user input.

    Inlined with `literal_column` and not bound: the expression must read exactly
    like the one the validity indexes are built on (migration 0009), which a
    bound parameter would not.
    """
    return record.properties.op("->>", return_type=Text)(literal_column("'" + key + "'"))


def visible_at(record: Any, as_of: date | None) -> list[ColumnElement[bool]]:
    """The conditions that make a row visible on `as_of`; none when no day is asked.

    Both ends are inclusive and a missing bound is open (docs/adr/0042). The day
    is compared as its ISO string, which sorts like the day itself. Called with
    `ElementRecord`, `RelationshipRecord` or an alias of either; returning a list
    lets every call site splat it, so `None` adds no condition and no join.
    """
    if as_of is None:
        return []
    day = as_of.isoformat()
    starts, ends = _day_of(record, VALID_FROM), _day_of(record, VALID_UNTIL)
    return [or_(starts.is_(None), starts <= day), or_(ends.is_(None), ends >= day)]
```

`_matching` : avant `return clauses` ajouter `clauses.extend(visible_at(ElementRecord, criteria.as_of))`.

`list_relationships` : signature `..., offset: int = 0, as_of: date | None = None`, corps :

```python
        statement = select(RelationshipRecord).where(
            *_of_types(relationship_types), *visible_at(RelationshipRecord, as_of)
        )
        if as_of is not None:
            # A link is visible only if both of its ends are (docs/adr/0043).
            alive = select(ElementRecord.id).where(*visible_at(ElementRecord, as_of))
            statement = statement.where(
                RelationshipRecord.source_id.in_(alive), RelationshipRecord.target_id.in_(alive)
            )
```
(le reste — filtre `element_id`, tri, pagination — inchangé).

`conftest.py` — dans `InMemoryRepository` : ajouter `from datetime import UTC, date, datetime` (import existant `from datetime import UTC, datetime`), `from ea.domain.timeline import Lifespan`, `self.last_as_of: date | None = None` dans `__init__`, et remplacer `list_elements`, `count_elements`, `list_relationships` :

```python
    def _matching(self, criteria: ElementFilter) -> list[Element]:
        return [
            element
            for element in self.elements.values()
            if (not criteria.element_types or element.element_type in criteria.element_types)
            and (criteria.as_of is None or Lifespan.read(element.properties).covers(criteria.as_of))
        ]

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        return tuple(self._matching(criteria))[criteria.offset : criteria.offset + criteria.limit]

    async def count_elements(self, criteria: ElementFilter) -> int:
        return len(self._matching(criteria))
```
et `list_relationships(..., offset: int = 0, as_of: date | None = None)` avec `self.last_as_of = as_of` en première ligne (corps par ailleurs inchangé).

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_visibility_sql.py tests/unit/test_architecture_service.py -x` ; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/architecture_store.py backend/tests/conftest.py backend/tests/unit/test_visibility_sql.py
git commit -m "feat(store): visible_at predicate for lists and relationships" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 6: `as_of` dans les CTE du voisinage et de l'impact

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (`neighbourhood` l. 107-115, `impacted_by` l. 117-130)
- Modify: `backend/src/ea/repositories/architecture_store.py` (imports ; `_seed` l. 240-246 ; `_neighbourhood_walk` l. 249-264 ; `_impact_walk` l. 267-293 ; `_links_within` l. 296-311 ; `neighbourhood`, `impacted_by`, `_view_of_walk` l. 463-481 et 519-534)
- Modify: `backend/tests/conftest.py` (`neighbourhood`, `impacted_by` du double)
- Test: `backend/tests/integration/test_as_of_graph.py` (écrit et lancé en Task 9 ; cette tâche se vérifie par mypy, la suite unitaire et la compilation ci-dessous)

**Interfaces:**
- Consumes: `visible_at` (Task 5).
- Produces (Python) : `neighbourhood(element_id, *, depth=1, relationship_types=(), as_of: date | None = None)`, `impacted_by(element_id, *, depth=5, relationship_types=(), as_of: date | None = None)` sur le port et sur `PostgresArchitectureRepository` ; le double enregistre `last_as_of`.

- [ ] **Step 1: Test qui échoue** — ajouter à `backend/tests/unit/test_visibility_sql.py` (compilation des CTE, sans base) :

```python
from uuid import uuid4

from ea.domain.archimate import RelationshipType
from ea.repositories.architecture_store import _impact_walk, _neighbourhood_walk


def _sql(cte: object) -> str:
    return str(cte.select().compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_the_walks_are_byte_for_byte_the_same_without_a_day() -> None:
    element = uuid4()
    for walk in (_neighbourhood_walk, _impact_walk):
        before = _sql(walk(element, 3, (RelationshipType.SERVING,)))
        after = _sql(walk(element, 3, (RelationshipType.SERVING,), None))

        assert "valid_from" not in before
        assert before == after


def test_a_day_puts_the_predicate_in_the_recursive_term_for_the_link_and_the_far_end() -> None:
    element = uuid4()
    for walk in (_neighbourhood_walk, _impact_walk):
        sql = _sql(walk(element, 3, (), date(2027, 1, 1)))

        assert sql.count("relationships.properties ->> 'valid_until'") >= 1
        assert "properties_1 ->> 'valid_until'" in sql.replace("elements_1.properties", "properties_1")
        # the seed is filtered as well: a retired root reaches nothing
        assert sql.count("'valid_from'") >= 3
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_visibility_sql.py -x` → `TypeError: _neighbourhood_walk() takes 3 positional arguments but 4 were given`.

- [ ] **Step 3: Implémenter.**

`architecture_store.py` : imports — ajouter `from sqlalchemy.orm import aliased`. Remplacer `_seed`, `_neighbourhood_walk`, `_impact_walk`, `_links_within` :

```python
def _seed(element_id: UUID, as_of: date | None) -> CTE:
    """The start of a walk: the element itself at zero hops, or nothing if absent.

    Nothing too when it is not in service on `as_of`: a retired root reaches nothing.
    """
    return (
        select(ElementRecord.id.label("id"), literal_column("0", Integer).label("hops"))
        .where(ElementRecord.id == element_id, *visible_at(ElementRecord, as_of))
        .cte("reached", recursive=True)
    )


def _in_service(step: Any, next_id: Any, as_of: date | None) -> Any:
    """Restrict a recursive term to links and far ends in service on `as_of`.

    The far end is joined only when a day is asked, so without one the term is
    exactly the one it always was. In the recursive term, so a neighbour out of
    service *cuts* the path: nothing beyond it is reached through it.
    """
    if as_of is None:
        return step
    far = aliased(ElementRecord)
    return step.join(far, far.id == next_id).where(
        *visible_at(RelationshipRecord, as_of), *visible_at(far, as_of)
    )


def _neighbourhood_walk(
    element_id: UUID, depth: int, types: Sequence[RelationshipType], as_of: date | None = None
) -> CTE:
    """Every element within `depth` hops, whichever way each link points."""
    reached = _seed(element_id, as_of)
    link = RelationshipRecord
    next_id = case((link.source_id == reached.c.id, link.target_id), else_=link.source_id)
    step = (
        select(next_id.label("id"), (reached.c.hops + 1).label("hops"))
        .select_from(reached)
        .join(link, or_(link.source_id == reached.c.id, link.target_id == reached.c.id))
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(_in_service(step, next_id, as_of))


def _impact_walk(
    element_id: UUID, depth: int, types: Sequence[RelationshipType], as_of: date | None = None
) -> CTE:
    """Everything that depends on an element, each hop taken the way dependency runs.

    A hop along a type of `ALONG_THE_ARROW` leaves from its source; any other
    leaves from its target. That lets one walk mix both — forwards for
    `serving`, backwards for `composition` — without answering a different
    question halfway through.
    """
    reached = _seed(element_id, as_of)
    link = RelationshipRecord
    along = link.relationship_type.in_(ALONG_THE_ARROW)
    next_id = case((along, link.target_id), else_=link.source_id)
    step = (
        select(next_id.label("id"), (reached.c.hops + 1).label("hops"))
        .select_from(reached)
        .join(
            link,
            or_(
                and_(along, link.source_id == reached.c.id),
                and_(not_(along), link.target_id == reached.c.id),
            ),
        )
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(_in_service(step, next_id, as_of))


async def _links_within(
    session: AsyncSession,
    ids: Sequence[UUID],
    types: Sequence[RelationshipType],
    as_of: date | None = None,
) -> tuple[Relationship, ...]:
    """The relationships whose two ends are both among `ids`: a drawable sub-graph."""
    if not ids:
        return ()
    statement = (
        select(RelationshipRecord)
        .where(
            RelationshipRecord.source_id.in_(ids),
            RelationshipRecord.target_id.in_(ids),
            *_of_types(types),
            *visible_at(RelationshipRecord, as_of),
        )
        .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
    )
    return tuple(relationship_from_row(row) for row in await session.scalars(statement))
```

Méthodes du dépôt :

```python
    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
        as_of: date | None = None,
    ) -> GraphView:
        walk = _neighbourhood_walk(element_id, _clamp_depth(depth), relationship_types, as_of)
        return await self._view_of_walk(walk, relationship_types, as_of)

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
        as_of: date | None = None,
    ) -> GraphView:
        walk = _impact_walk(element_id, _clamp_depth(depth), relationship_types, as_of)
        return await self._view_of_walk(walk, relationship_types, as_of)
```

`_view_of_walk(self, walk, relationship_types, as_of: date | None = None)` : remplacer l'appel `_links_within(session, [...], relationship_types)` par `_links_within(session, [element.id for element in elements], relationship_types, as_of)`.

`ports.py` : ajouter `as_of: date | None = None,` à la fin des paramètres de `neighbourhood` et `impacted_by`.

`conftest.py` : dans le double, `neighbourhood` et `impacted_by` prennent `as_of: date | None = None` et commencent par `self.last_as_of = as_of` (le corps renvoie toujours l'élément seul).

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_visibility_sql.py tests/unit/test_architecture_service.py -x` ; `uv run mypy src`. Si l'assertion sur l'alias `properties_1` est fragile, la remplacer par `assert sql.count("'valid_until'") >= 3` (lien, extrémité lointaine, graine) — l'intention du test est que les trois endroits portent le prédicat.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/architecture_store.py backend/tests/conftest.py backend/tests/unit/test_visibility_sql.py
git commit -m "feat(store): as_of cuts the recursive walks at out-of-service neighbours" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 7: Service, schémas, routes et diagrammes (`as_of` de bout en bout, côté HTTP)

**Files:**
- Modify: `backend/src/ea/services/architecture.py` (`list_relationships` l. 240-254 ; `neighbourhood` l. 286-298 ; `impact_of` l. 300-312)
- Modify: `backend/src/ea/domain/diagrams.py` (`DiagramDetail` l. 88-94)
- Modify: `backend/src/ea/services/diagrams.py` (imports ; `open` l. 70-82)
- Modify: `backend/src/ea/api/schemas.py` (imports l. 11-14 ; après `Search` l. 101 ; `DiagramRead` l. 770-796)
- Modify: `backend/src/ea/api/architecture.py` (`list_elements` l. 68-91 ; `list_relationships` l. 154-169 ; traversées l. 206-240)
- Modify: `backend/src/ea/api/diagrams.py` (`read_diagram` l. 52-55)
- Test: `backend/tests/e2e/test_timeline_api.py` (compléter) ; `backend/tests/unit/test_timeline_service.py` (création, pour `DiagramService.open` seulement ici)

**Interfaces:**
- Consumes: `Lifespan`, `ElementFilter.as_of`, `visible_at`.
- Produces :
  - `AsOf = Annotated[date, Field(description=...)]` (schemas)
  - `ArchitectureService.list_relationships(..., as_of: date | None = None)`, `.neighbourhood(..., as_of=None)`, `.impact_of(..., as_of=None)`
  - `DiagramService.open(diagram_id: UUID, *, as_of: date | None = None) -> DiagramDetail`
  - `DiagramDetail.inactive_elements: frozenset[UUID] = frozenset()`, `.inactive_relationships: frozenset[UUID] = frozenset()`
  - `DiagramRead.inactive_element_ids: list[UUID]`, `.inactive_relationship_ids: list[UUID]`
  - HTTP : `?as_of=YYYY-MM-DD` sur `GET /elements`, `/relationships`, `/elements/{id}/neighbourhood`, `/elements/{id}/impact`, `/diagrams/{id}`

- [ ] **Step 1: Tests qui échouent** — ajouter à `backend/tests/e2e/test_timeline_api.py` :

```python
from datetime import date

from tests.conftest import InMemoryDiagrams, InMemoryRepository


@pytest_asyncio.fixture
async def diagram_client(
    service: ArchitectureService, diagrams: InMemoryDiagrams
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


class TestAsOfOnReads:
    async def test_without_as_of_the_catalogue_is_what_it_was(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "application_component", "Legacy", valid_until="2026-12-31")
        await create(client, "application_component", "Always")

        everything = (await client.get("/elements")).json()

        assert everything["total"] == 2
        assert {item["name"] for item in everything["items"]} == {"Legacy", "Always"}

    async def test_as_of_leaves_out_what_is_not_in_service_that_day(
        self, client: httpx.AsyncClient
    ) -> None:
        await create(client, "application_component", "Legacy", valid_until="2026-12-31")
        await create(client, "application_component", "Future", valid_from="2028-01-01")
        await create(client, "application_component", "Always")

        page = (await client.get("/elements", params={"as_of": "2027-06-01"})).json()

        assert [item["name"] for item in page["items"]] == ["Always"]
        assert page["total"] == 1

    async def test_the_boundary_days_are_inclusive(self, client: httpx.AsyncClient) -> None:
        await create(client, "application_component", "A", valid_until="2026-12-31")

        last = (await client.get("/elements", params={"as_of": "2026-12-31"})).json()
        after = (await client.get("/elements", params={"as_of": "2027-01-01"})).json()

        assert last["total"] == 1 and after["total"] == 0

    @pytest.mark.parametrize("bad", ["demain", "2027-06", "2027-13-40"])
    async def test_an_invalid_day_is_a_422_on_every_read(
        self, client: httpx.AsyncClient, bad: str
    ) -> None:
        element = await create(client, "application_component", "A")
        for path in (
            "/elements",
            "/relationships",
            f"/elements/{element['id']}/neighbourhood",
            f"/elements/{element['id']}/impact",
        ):
            assert (await client.get(path, params={"as_of": bad})).status_code == 422, path

    async def test_the_traversals_and_links_hand_the_day_to_the_store(
        self, client: httpx.AsyncClient, repository: InMemoryRepository
    ) -> None:
        element = await create(client, "application_component", "A")

        await client.get(f"/elements/{element['id']}/neighbourhood", params={"as_of": "2028-01-01"})
        assert repository.last_as_of == date(2028, 1, 1)
        await client.get(f"/elements/{element['id']}/impact")
        assert repository.last_as_of is None
        await client.get("/relationships", params={"as_of": "2029-02-03"})
        assert repository.last_as_of == date(2029, 2, 3)


class TestDiagramsGreyInsteadOfFiltering:
    async def test_boxes_out_of_period_are_flagged_not_removed(
        self, diagram_client: httpx.AsyncClient
    ) -> None:
        legacy = await create(diagram_client, "application_component", "Legacy", valid_until="2026-12-31")
        always = await create(diagram_client, "application_component", "Always")
        link = await diagram_client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": legacy["id"],
                "target_id": always["id"],
            },
        )
        diagram = (await diagram_client.post("/diagrams", json={"name": "Vente"})).json()
        await diagram_client.put(
            f"/diagrams/{diagram['id']}/layout",
            json={
                "nodes": [
                    {"element_id": e["id"], "x": 0, "y": 0, "width": 100, "height": 50}
                    for e in (legacy, always)
                ]
            },
        )

        today = (await diagram_client.get(f"/diagrams/{diagram['id']}")).json()
        later = (
            await diagram_client.get(f"/diagrams/{diagram['id']}", params={"as_of": "2027-06-01"})
        ).json()

        assert today["inactive_element_ids"] == [] and today["inactive_relationship_ids"] == []
        assert len(later["nodes"]) == 2 and len(later["elements"]) == 2
        assert later["inactive_element_ids"] == [legacy["id"]]
        assert later["inactive_relationship_ids"] == [link.json()["id"]]
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/e2e/test_timeline_api.py -x` → `assert 2 == 1` (le paramètre est ignoré).

- [ ] **Step 3: Implémenter.**

`api/schemas.py` : `from datetime import date, datetime` ; après `Search = ...` (l. 101) :

```python
#: A day to read the model on (docs/adr/0043): elements and links outside their
#: `valid_from` / `valid_until` are left out. A typed `date`, never free text.
AsOf = Annotated[
    date,
    Field(
        description=(
            "Read the model as it is on this day (YYYY-MM-DD): what is not in service "
            "that day is left out. Leave it out for no date filter."
        )
    ),
]
```

`DiagramRead` : ajouter après `relationships` :

```python
    inactive_element_ids: list[UUID] = Field(
        default_factory=list,
        description="Boxes whose element is not in service on the requested `as_of` day.",
    )
    inactive_relationship_ids: list[UUID] = Field(
        default_factory=list,
        description="Links out of period on `as_of`, or whose either end is.",
    )
```
et dans `of(...)` : `inactive_element_ids=sorted(detail.inactive_elements, key=str),` `inactive_relationship_ids=sorted(detail.inactive_relationships, key=str),`.

`domain/diagrams.py` — `DiagramDetail` : ajouter les deux champs (`frozenset[UUID] = frozenset()`) et l'import de `UUID` s'il manque.

`services/diagrams.py` : imports `from datetime import UTC, date, datetime` (+ `date` runtime pas requis → `TYPE_CHECKING`) et `from ea.domain.timeline import Lifespan` ; `open` devient :

```python
    async def open(self, diagram_id: UUID, *, as_of: date | None = None) -> DiagramDetail:
        """A diagram with the part of the graph it shows, stale boxes left out.

        `as_of` removes nothing — a diagram is a view, and a box out of period
        keeps its place in the layout (docs/adr/0043) — it only says which ones
        are not in service that day, for the SPA to grey.
        """
        require_caller()
        diagram = await self.get(diagram_id)
        nodes = await self._repository.nodes_of(diagram_id)
        graph = await self._architecture.view_of([node.element_id for node in nodes])
        present = {element.id for element in graph.elements}
        inactive_elements: frozenset[UUID] = frozenset()
        inactive_links: frozenset[UUID] = frozenset()
        if as_of is not None:
            inactive_elements = frozenset(
                e.id for e in graph.elements if not Lifespan.read(e.properties).covers(as_of)
            )
            inactive_links = frozenset(
                link.id
                for link in graph.relationships
                if not Lifespan.read(link.properties).covers(as_of)
                or link.source_id in inactive_elements
                or link.target_id in inactive_elements
            )
        return DiagramDetail(
            diagram=diagram,
            nodes=tuple(node for node in nodes if node.element_id in present),
            graph=graph,
            inactive_elements=inactive_elements,
            inactive_relationships=inactive_links,
        )
```
(`UUID` est déjà importé sous `TYPE_CHECKING` ; avec `from __future__ import annotations` les annotations locales ne sont pas évaluées.)

`services/architecture.py` : `from datetime import UTC, date, datetime` (remplace l'import) ; `list_relationships`, `neighbourhood`, `impact_of` gagnent `as_of: date | None = None` et le passent (`as_of=as_of`) au dépôt.

`api/architecture.py` : `from datetime import date` ; `from ea.api.schemas import AsOf, ...` ; alias `AsOfQuery = Annotated[AsOf | None, Query()]` sous `OffsetQuery` ; `list_elements`, `list_relationships`, `read_neighbourhood`, `read_impact` gagnent `as_of: AsOfQuery = None` (après les autres paramètres) ; `ElementFilter(..., offset=offset, as_of=as_of)` ; les appels de service reçoivent `as_of=as_of`.

`api/diagrams.py` : importer `Annotated`, `date`, `Query` (`from fastapi import APIRouter, Query, Response, status`), `AsOf` ; `read_diagram(diagram_id: UUID, diagrams: Diagrams, as_of: Annotated[AsOf | None, Query()] = None)` → `DiagramRead.of(await diagrams.open(diagram_id, as_of=as_of))`.

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/e2e tests/unit -x -q` (tout le périmètre non-intégration) ; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services backend/src/ea/domain/diagrams.py backend/src/ea/api/schemas.py backend/src/ea/api/architecture.py backend/src/ea/api/diagrams.py backend/tests/e2e/test_timeline_api.py
git commit -m "feat(api): as_of on elements, links, traversals and diagrams" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 8: Outils MCP — `as_of` sur `list_elements`, `neighbourhood`, `impact_of`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (import schemas l. 38-72 ; arguments l. 167-250 ; `list_elements` ; `neighbourhood` ; `impact_of`)
- Test: `backend/tests/unit/test_mcp_server.py` (nouvelle classe `TestTime` avant `TestTheSameBoundsAsTheHttpAdapter`)

**Interfaces:**
- Consumes: `AsOf` (Task 7).
- Produces : `list_elements(..., as_of: AsOfArgument = None)`, `neighbourhood(..., as_of=None)`, `impact_of(..., as_of=None)` ; `AsOfArgument = Annotated[AsOf | None, Field(description=...)]`.

- [ ] **Step 1: Test qui échoue** — dans `backend/tests/unit/test_mcp_server.py` :

```python
@pytest.mark.asyncio
class TestTime:
    async def test_the_catalogue_can_be_read_on_a_day(self, server: MCPServer[Any]) -> None:
        await call(
            server, "create_element", element_type="application_component", name="Legacy",
            properties={"valid_until": "2026-12-31"},
        )
        await an_element(server, "application_component", "Always")

        everything = await call(server, "list_elements")
        later = await call(server, "list_elements", as_of="2027-06-01")

        assert everything["total"] == 2
        assert [item["name"] for item in later["items"]] == ["Always"]

    async def test_an_agent_that_writes_a_partial_date_is_told_why(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="valid_from"):
            await call(
                server, "create_element", element_type="application_component", name="X",
                properties={"valid_from": "2027-06"},
            )

    async def test_the_day_is_a_typed_argument(self, server: MCPServer[Any]) -> None:
        created = await an_element(server, "node", "db-01")

        with pytest.raises(ToolError):
            await call(server, "list_elements", as_of="demain")
        for tool in ("neighbourhood", "impact_of"):
            answer = await call(server, tool, element_id=created["id"], as_of="2027-06-01")
            assert [e["name"] for e in answer["elements"]] == ["db-01"]
            with pytest.raises(ToolError):
                await call(server, tool, element_id=created["id"], as_of="2027-13-01")
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_mcp_server.py -k TestTime -x` → `ToolError`/`validation error: Unexpected keyword argument as_of`.

- [ ] **Step 3: Implémenter** — dans `mcp/server.py` : ajouter `AsOf,` à l'import depuis `ea.api.schemas` (ordre alphabétique, après `Address...`), puis après `ElementId` :

```python
AsOfArgument = Annotated[
    AsOf | None,
    Field(
        description=(
            "A day, YYYY-MM-DD: read the model as it is that day — what is not yet or "
            "no longer in service is left out. Omit it to see everything."
        )
    ),
]
```
`list_elements` : paramètre `as_of: AsOfArgument = None` (après `search`, avant `limit` n'est pas requis : placer en dernier) et `ElementFilter(..., as_of=as_of)` ; ajouter à la docstring : « `as_of` reads the catalogue on a day: elements carry `valid_from` / `valid_until` in their properties ». `neighbourhood` et `impact_of` : `as_of: AsOfArgument = None` en dernier paramètre et `as_of=as_of` passé au service.

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_mcp_server.py -x` ; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): as_of argument on list_elements, neighbourhood, impact_of" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 9: Intégration PostgreSQL de `as_of` (listes, voisinage, impact, régression)

**Files:**
- Create: `backend/tests/integration/test_as_of_graph.py`

**Interfaces:**
- Consumes: `graph_service`, `graph_repository` (fixtures de `tests/integration/conftest.py`), tout le lot 2.
- Produces : preuve SQL du comportement.

- [ ] **Step 1: Écrire les tests** (ils doivent déjà passer si les tâches 5-6 sont justes ; un échec ici révèle un bug de SQL) :

```python
"""`as_of` against a real PostgreSQL: what the recursive walks do at a retired neighbour."""

from __future__ import annotations

from datetime import date

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

AFTER = date(2028, 1, 1)
BEFORE = date(2027, 6, 1)


async def _chain(service: ArchitectureService, **middle: str):  # type: ignore[no-untyped-def]
    """a --serving--> b --serving--> c, where `b` may carry a lifespan."""
    a = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="a")
    b = await service.create_element(
        element_type=E.APPLICATION_COMPONENT, name="b", properties=middle or None
    )
    c = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="c")
    ab = await service.connect(relationship_type=R.SERVING, source_id=a.id, target_id=b.id)
    bc = await service.connect(relationship_type=R.SERVING, source_id=b.id, target_id=c.id)
    return a, b, c, ab, bc


class TestLists:
    async def test_the_catalogue_and_its_count_agree_on_the_day(
        self, graph_service: ArchitectureService
    ) -> None:
        await graph_service.create_element(
            element_type=E.NODE, name="old", properties={"valid_until": "2026-12-31"}
        )
        await graph_service.create_element(
            element_type=E.NODE, name="new", properties={"valid_from": "2028-01-01"}
        )
        await graph_service.create_element(element_type=E.NODE, name="always")

        criteria = ElementFilter(as_of=BEFORE)

        assert [e.name for e in await graph_service.list_elements(criteria)] == ["always"]
        assert await graph_service.count_elements(criteria) == 1

    async def test_a_link_with_an_end_out_of_service_is_not_listed(
        self, graph_service: ArchitectureService
    ) -> None:
        _, _, _, ab, bc = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.list_relationships(as_of=BEFORE)
        after = await graph_service.list_relationships(as_of=AFTER)

        assert {link.id for link in before} == {ab.id, bc.id}
        assert after == ()

    async def test_a_link_with_a_lifespan_of_its_own_is_cut_when_it_ends(
        self, graph_service: ArchitectureService
    ) -> None:
        a = await graph_service.create_element(element_type=E.NODE, name="a")
        b = await graph_service.create_element(element_type=E.NODE, name="b")
        link = await graph_service.connect(
            relationship_type=R.ASSOCIATION,
            source_id=a.id,
            target_id=b.id,
            properties={"valid_until": "2026-12-31"},
        )

        assert [x.id for x in await graph_service.list_relationships(as_of=date(2026, 12, 31))] == [link.id]
        assert await graph_service.list_relationships(as_of=date(2027, 1, 1)) == ()


class TestWalks:
    async def test_a_retired_neighbour_cuts_the_neighbourhood(
        self, graph_service: ArchitectureService
    ) -> None:
        a, b, c, _, _ = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.neighbourhood(a.id, depth=3, as_of=BEFORE)
        after = await graph_service.neighbourhood(a.id, depth=3, as_of=AFTER)

        assert {e.name for e in before.elements} == {"a", "b", "c"}
        assert {e.name for e in after.elements} == {"a"}
        assert after.relationships == ()

    async def test_a_retired_neighbour_cuts_the_impact_rather_than_being_skipped(
        self, graph_service: ArchitectureService
    ) -> None:
        a, b, c, _, _ = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.impact_of(a.id, depth=5, as_of=BEFORE)
        after = await graph_service.impact_of(a.id, depth=5, as_of=AFTER)

        assert {e.name for e in before.elements} == {"a", "b", "c"}
        assert {e.name for e in after.elements} == {"a"}  # c is not reached through a dead b

    async def test_an_undated_chain_is_untouched_by_any_day(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service)

        view = await graph_service.neighbourhood(a.id, depth=3, as_of=date(1999, 1, 1))

        assert {e.name for e in view.elements} == {"a", "b", "c"}

    async def test_a_root_out_of_period_reaches_nothing_and_is_not_a_404(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service)
        retired = await graph_service.update_element(
            a.id, properties={"valid_until": "2026-12-31"}
        )

        for view in (
            await graph_service.neighbourhood(retired.id, depth=3, as_of=BEFORE),
            await graph_service.impact_of(retired.id, depth=3, as_of=BEFORE),
        ):
            assert view.elements == () and view.relationships == ()

    async def test_the_boundary_day_is_inclusive_in_the_walk(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2027-12-31")

        last = await graph_service.neighbourhood(a.id, depth=2, as_of=date(2027, 12, 31))
        next_day = await graph_service.neighbourhood(a.id, depth=2, as_of=date(2028, 1, 1))

        assert len(last.elements) == 3 and len(next_day.elements) == 1


class TestNoDayAskedChangesNothing:
    async def test_the_answers_are_the_same_with_and_without_as_of_none(
        self, graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2020-01-01")  # retired long ago

        plain = await graph_repository.neighbourhood(a.id, depth=3)
        explicit_none = await graph_repository.neighbourhood(a.id, depth=3, as_of=None)
        impact_plain = await graph_repository.impacted_by(a.id, depth=3)
        impact_none = await graph_repository.impacted_by(a.id, depth=3, as_of=None)

        assert [e.id for e in plain.elements] == [e.id for e in explicit_none.elements]
        assert len(plain.elements) == 3 and len(plain.relationships) == 2
        assert [e.id for e in impact_plain.elements] == [e.id for e in impact_none.elements]
```

- [ ] **Step 2: Lancer** — `make test-integration` (ou, la pile jetable déjà levée, `cd backend && uv run pytest tests/integration/test_as_of_graph.py -x`). Attendu : tous PASS. En cas d'échec, c'est le SQL des Tasks 5-6 qui est faux : corriger là, pas ici.

- [ ] **Step 3: Régression globale** — `make test-integration` complet : aucun test existant du graphe ne doit changer.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_as_of_graph.py
git commit -m "test(store): as_of against PostgreSQL, lists and recursive walks" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 10: ADR `as_of` et vérification du lot 2

**Files:**
- Create: `docs/adr/0043-le-parametre-as-of-des-lectures.md`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)

**Interfaces:** client TypeScript régénéré : `as_of?: string | null` sur les cinq lectures, `inactive_element_ids`, `inactive_relationship_ids` sur `DiagramRead`.

- [ ] **Step 1: Rédiger l'ADR** — `docs/adr/0043-le-parametre-as-of-des-lectures.md` :

````markdown
---
titre: Le paramètre as_of des lectures
date: 2026-10-10
statut: Proposition
affects: services/architecture.py, services/diagrams.py, repositories/architecture_store.py, api/architecture.py, api/diagrams.py, mcp/server.py
---

# Le paramètre `as_of` des lectures

## Contexte

L'ADR 0042 donne des dates de validité aux éléments et aux relations. Reste à les lire : « que dit le catalogue au 2028-01-01 ? », « que casse la panne de cette application au 2028 ? ». La question se pose sur toutes les vues, pas sur une seule.

## Décision

- **Un seul paramètre transverse**, `as_of: date | None`, ajouté aux lectures `GET /elements`, `GET /relationships`, `GET /elements/{id}/neighbourhood`, `GET /elements/{id}/impact`, `GET /diagrams/{id}`, et aux outils MCP `list_elements`, `neighbourhood`, `impact_of`. **Absent, le comportement est strictement l'actuel** : le prédicat est une liste vide, aucune condition, aucune jointure.
- **Sémantique.** Un élément est visible à `d` si `(valid_from IS NULL OR valid_from <= d) AND (valid_until IS NULL OR valid_until >= d)` — bornes inclusives, borne absente ouverte. Une relation l'est si elle-même **et** ses deux extrémités le sont : pas de lien orphelin.
- **Dans les CTE récursives**, le prédicat est dans le terme récursif (le lien et l'extrémité lointaine) et dans la graine : un voisin non encore ou plus en service **coupe** le chemin ; l'impact « au 2028 » ne traverse pas une application retirée en 2027. Une racine hors période donne un sous-graphe **vide** (200), pas un 404 — l'élément existe, il n'est simplement pas visible ce jour-là.
- **Un seul endroit** écrit le SQL : `visible_at(record, as_of)` dans `architecture_store.py`, qui compare des chaînes ISO (`properties ->> 'valid_from'` avec la clé inlinée, la date liée en paramètre).
- **Un filtre de lecture, pas un type de requête à part.** `ElementFilter` porte `as_of` ; les signatures du port gagnent un argument optionnel.
- **Les diagrammes ne perdent rien.** Un diagramme est une vue : `as_of` ne retire aucune boîte (la mise en page serait perdue) ; `DiagramRead` ajoute `inactive_element_ids` / `inactive_relationship_ids` et le SPA grise.
- **Dans le SPA**, `?asof=YYYY-MM-DD` est global (absent : « aujourd'hui, sans filtre »), propagé par une garde du routeur ; le changer est un `replace` (ADR 0010).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Filtre de lecture `as_of` | Une seule sémantique pour toutes les vues ; rétro-compatible | Chaque lecture doit le porter | Retenu |
| Type de requête dédié (« état au… ») | Isolé | Duplique voisinage, impact, listes | Écarté |
| Filtrer dans le service, après lecture | Pas de SQL | Faux dans les CTE (un chemin passerait par un voisin retiré) et pagination fausse | Écarté |
| Retirer les boîtes hors période des diagrammes | Simple | Perte de la mise en page, incohérent avec « une vue ne porte aucun fait » | Écarté |

## Conséquences

- **Performance à mesurer.** Un index partiel (`WHERE … IS NOT NULL`) ne peut pas répondre à `IS NULL OR …` : le prédicat de visibilité lit les lignes. Sous quelques dizaines de milliers d'éléments, sans souci attendu ; à mesurer sur le jeu réel avant d'aller plus loin (index non partiels si besoin, nouvelle révision).
- `GET /elements/{id}/relationships` n'est pas filtré : le panneau Relations montre encore les liens vers un élément retiré. À reprendre si cela gêne.
- Le temps de transaction (`known_at`, baselines) reste libre : `as_of` pourra se combiner avec lui plus tard, le nom est réservé et non livré.
- Un lecteur voit tout le futur planifié ; des plans confidentiels seraient un contrôle d'accès général, hors périmètre.

## Références

- ADR liés : [0042](0042-temps-de-validite-dans-les-proprietes.md), [0031](0031-diagrammes-enregistres.md), [0010](0010-voisinage-dans-l-url.md), [0033](0033-postgresql-pour-tout-le-modele.md)
- Code concerné : `backend/src/ea/repositories/architecture_store.py` (`visible_at`), `backend/src/ea/services/diagrams.py` (`open`)
````

- [ ] **Step 2: Corriger les noms de fichiers des ADR liés** — `ls docs/adr | grep -E "^(0010|0031|0033)"`.

- [ ] **Step 3: Régénérer le contrat** — `make openapi` ; vérifier avec `git diff --stat backend/openapi.json frontend/src/api/schema.d.ts` que `as_of` et `inactive_*` apparaissent ; `make openapi-check`.

- [ ] **Step 4: Vérifier le lot** — `make check` ; `make test-integration`. Tout vert.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0043-le-parametre-as-of-des-lectures.md backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "docs(adr): the as_of parameter of reads; regenerate the client" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 3 — Plateaux, écart, feuille de route (API et MCP)

### Task 11: Domaine — `diff_plateaus`, types de plateau et de frise, fenêtre

**Files:**
- Modify: `backend/src/ea/domain/timeline.py` (imports ; ajouts en fin de fichier)
- Modify: `backend/tests/conftest.py` (helper `an_element`)
- Test: `backend/tests/unit/test_timeline.py` (ajouts)

**Interfaces:**
- Consumes: `Lifespan` (Task 1), `Element`, `RelationshipType`.
- Produces (Python) :
  - `MAX_ROADMAP_DAYS: Final = 20 * 366`, `MAX_PLATEAUS: Final = 500`, `MAX_ROADMAP_ITEMS: Final = 1000`
  - `PlateauGap(only_in_a: tuple[Element, ...], only_in_b: tuple[Element, ...], common: tuple[Element, ...])`
  - `diff_plateaus(a: Iterable[Element], b: Iterable[Element]) -> PlateauGap`
  - `PlateauSummary(element: Element, lifespan: Lifespan, member_count: int)` ; `sort_plateaus(summaries: Iterable[PlateauSummary]) -> tuple[PlateauSummary, ...]`
  - `PlateauComparison(a: Element, b: Element, diff: PlateauGap, gaps: tuple[Element, ...])`
  - `RoadmapItem(element: Element, lifespan: Lifespan, plateau_ids: tuple[UUID, ...])`, `RoadmapEdge(source_id: UUID, target_id: UUID, relationship_type: RelationshipType)`, `Roadmap(items, edges, window_from: date | None, window_to: date | None)`
  - `resolve_window(lifespans: Iterable[Lifespan], start: date | None, end: date | None) -> tuple[date | None, date | None]`
  - `in_window(lifespan: Lifespan, start: date | None, end: date | None) -> bool`
  - tests : `an_element(element_type=E.APPLICATION_COMPONENT, name="Billing", **properties: str) -> Element`

- [ ] **Step 1: Tests qui échouent** — ajouter à `backend/tests/conftest.py` (après `a_reader`) :

```python
def an_element(
    element_type: ElementType = ElementType.APPLICATION_COMPONENT,
    name: str = "Billing",
    **properties: str,
) -> Element:
    """A domain element, built the way the services build one, with a fixed clock."""
    return Element.create(
        element_type=element_type, name=name, now=FIXED_NOW, properties=properties or None
    )
```
(et `from ea.domain.archimate import ElementType`, à côté de l'import existant `RelationshipType as R`). Ajouter à `backend/tests/unit/test_timeline.py` :

```python
from ea.domain.archimate import ElementType as E
from ea.domain.timeline import (
    MAX_ROADMAP_DAYS,
    PlateauSummary,
    diff_plateaus,
    in_window,
    resolve_window,
    sort_plateaus,
)
from tests.conftest import an_element


class TestDiffPlateaus:
    def test_what_leaves_what_arrives_and_what_stays(self) -> None:
        old, kept, new = an_element(name="Old"), an_element(name="Kept"), an_element(name="New")

        gap = diff_plateaus([old, kept], [kept, new])

        assert gap.only_in_a == (old,)
        assert gap.only_in_b == (new,)
        assert gap.common == (kept,)

    def test_a_duplicate_is_counted_once(self) -> None:
        one = an_element(name="One")

        gap = diff_plateaus([one, one], [one])

        assert gap.common == (one,) and gap.only_in_a == () and gap.only_in_b == ()

    def test_two_empty_plateaus_differ_in_nothing(self) -> None:
        gap = diff_plateaus([], [])

        assert (gap.only_in_a, gap.only_in_b, gap.common) == ((), (), ())

    def test_an_empty_side_makes_everything_an_arrival_or_a_departure(self) -> None:
        one = an_element(name="One")

        assert diff_plateaus([], [one]).only_in_b == (one,)
        assert diff_plateaus([one], []).only_in_a == (one,)

    def test_the_answer_is_ordered_by_name_whatever_the_input_order(self) -> None:
        b, a = an_element(name="b"), an_element(name="A")

        assert [e.name for e in diff_plateaus([b, a], []).only_in_a] == ["A", "b"]


class TestPlateauOrder:
    def test_earliest_first_and_undated_last(self) -> None:
        def summary(name: str, start: date | None) -> PlateauSummary:
            return PlateauSummary(an_element(E.PLATEAU, name), Lifespan(start, None), 0)

        ordered = sort_plateaus(
            [summary("none", None), summary("late", date(2028, 1, 1)), summary("early", date(2026, 1, 1))]
        )

        assert [s.element.name for s in ordered] == ["early", "late", "none"]


class TestWindow:
    SPANS = [Lifespan(date(2026, 3, 1), date(2027, 6, 30)), Lifespan(None, date(2029, 1, 1)), Lifespan()]

    def test_the_default_window_is_the_bounds_of_the_data(self) -> None:
        assert resolve_window(self.SPANS, None, None) == (date(2026, 3, 1), date(2029, 1, 1))

    def test_no_dated_data_means_no_window(self) -> None:
        assert resolve_window([Lifespan()], None, None) == (None, None)

    def test_an_explicit_bound_wins_over_the_data(self) -> None:
        assert resolve_window(self.SPANS, date(2027, 1, 1), None) == (date(2027, 1, 1), date(2029, 1, 1))

    def test_a_window_that_ends_before_it_starts_is_refused(self) -> None:
        with pytest.raises(ValueError, match="before"):
            resolve_window(self.SPANS, date(2030, 1, 1), date(2029, 1, 1))

    def test_two_explicit_bounds_wider_than_twenty_years_are_refused(self) -> None:
        with pytest.raises(ValueError, match="20 years"):
            resolve_window(self.SPANS, date(2000, 1, 1), date(2030, 1, 1))

    def test_a_default_end_is_cut_to_twenty_years(self) -> None:
        start, end = resolve_window(
            [Lifespan(date(2000, 1, 1), date(2060, 1, 1))], None, None
        )

        assert start == date(2000, 1, 1)
        assert (end - start).days == MAX_ROADMAP_DAYS  # type: ignore[operator]

    def test_an_undated_thing_is_always_in_the_window(self) -> None:
        assert in_window(Lifespan(), date(2026, 1, 1), date(2026, 12, 31))

    def test_a_dated_thing_is_in_when_it_overlaps(self) -> None:
        assert in_window(Lifespan(date(2026, 12, 31), date(2030, 1, 1)), date(2026, 1, 1), date(2026, 12, 31))
        assert not in_window(Lifespan(date(2027, 1, 1), None), date(2026, 1, 1), date(2026, 12, 31))
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_timeline.py -x` → `ImportError: cannot import name 'diff_plateaus'`.

- [ ] **Step 3: Implémenter** — imports de `timeline.py` (remplacer le bloc d'imports) :

```python
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from ea.domain.errors import InvalidLifespanError

if TYPE_CHECKING:
    from uuid import UUID

    from ea.domain.archimate import RelationshipType
    from ea.domain.model import Element
```

et ajouter en fin de fichier :

```python
#: A roadmap window wider than this is a dump wearing a filter: 20 years, in days.
MAX_ROADMAP_DAYS: Final = 20 * 366
#: Plateaus are a handful; this only bounds a runaway catalogue.
MAX_PLATEAUS: Final = 500
#: Plateaus, work packages and deliverables a roadmap will draw — beyond it, narrow the question.
MAX_ROADMAP_ITEMS: Final = 1000


def _by_name(elements: Iterable[Element]) -> tuple[Element, ...]:
    """One of each (by id), in a stable order: name, then id."""
    unique = {element.id: element for element in elements}
    return tuple(sorted(unique.values(), key=lambda e: (e.name.lower(), str(e.id))))


@dataclass(frozen=True, slots=True)
class PlateauGap:
    """What changes between two plateaus: departures, arrivals, and what stays."""

    only_in_a: tuple[Element, ...]
    only_in_b: tuple[Element, ...]
    common: tuple[Element, ...]


def diff_plateaus(a: Iterable[Element], b: Iterable[Element]) -> PlateauGap:
    """Compare the members of two plateaus. Computed, never stored (docs/adr/0044)."""
    in_a = {element.id: element for element in a}
    in_b = {element.id: element for element in b}
    return PlateauGap(
        only_in_a=_by_name(e for i, e in in_a.items() if i not in in_b),
        only_in_b=_by_name(e for i, e in in_b.items() if i not in in_a),
        common=_by_name(e for i, e in in_a.items() if i in in_b),
    )


@dataclass(frozen=True, slots=True)
class PlateauSummary:
    element: Element
    lifespan: Lifespan
    member_count: int


def sort_plateaus(summaries: Iterable[PlateauSummary]) -> tuple[PlateauSummary, ...]:
    """Earliest first; a plateau with no start last; then by name."""
    return tuple(
        sorted(
            summaries,
            key=lambda s: (
                s.lifespan.start is None,
                s.lifespan.start or date.min,
                s.element.name.lower(),
                str(s.element.id),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class PlateauComparison:
    """Two plateaus, how they differ, and the gaps already drawn between them."""

    a: Element
    b: Element
    diff: PlateauGap
    gaps: tuple[Element, ...]


@dataclass(frozen=True, slots=True)
class RoadmapItem:
    element: Element
    lifespan: Lifespan
    #: The plateaus that contain it (a composition or aggregation from a plateau).
    plateau_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RoadmapEdge:
    source_id: UUID
    target_id: UUID
    relationship_type: RelationshipType


@dataclass(frozen=True, slots=True)
class Roadmap:
    items: tuple[RoadmapItem, ...]
    edges: tuple[RoadmapEdge, ...]
    window_from: date | None
    window_to: date | None


def resolve_window(
    lifespans: Iterable[Lifespan], start: date | None, end: date | None
) -> tuple[date | None, date | None]:
    """The days a roadmap spans: the bounds asked for, else those of the data.

    Two explicit bounds wider than `MAX_ROADMAP_DAYS` are refused; a bound taken
    from the data is cut to that width instead, since the caller did not choose it.
    """
    days = [d for span in lifespans for d in (span.start, span.end) if d is not None]
    low = start or (min(days) if days else None)
    high = end or (max(days) if days else None)
    if low is None or high is None:
        return low, high
    if high < low:
        if start is not None and end is not None:
            msg = "the roadmap window ends before it starts"
            raise ValueError(msg)
        low, high = (low, low) if end is None else (high, high)
    if (high - low).days > MAX_ROADMAP_DAYS:
        if start is not None and end is not None:
            msg = "the roadmap window is wider than 20 years"
            raise ValueError(msg)
        if end is None:
            high = low + timedelta(days=MAX_ROADMAP_DAYS)
        else:
            low = high - timedelta(days=MAX_ROADMAP_DAYS)
    return low, high


def in_window(lifespan: Lifespan, start: date | None, end: date | None) -> bool:
    """Undated things are always in (they are listed apart, never hidden)."""
    return not lifespan.is_dated or lifespan.overlaps(Lifespan(start, end))
```

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_timeline.py -x` ; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/timeline.py backend/tests/conftest.py backend/tests/unit/test_timeline.py
git commit -m "feat(domain): plateau diff, roadmap types and window" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 12: Service — `list_plateaus`, `plateau_gap`, `create_gap`, `roadmap`

**Files:**
- Modify: `backend/src/ea/domain/errors.py` (ajout `DuplicateGapError`)
- Modify: `backend/src/ea/api/errors.py` (import + `_STATUS`)
- Modify: `backend/src/ea/services/architecture.py` (imports l. 14-31 ; méthodes à ajouter dans une section « Time » en fin de classe)
- Modify: `backend/tests/unit/test_service_guards.py` (`WRITES[ArchitectureService]`, l. 29-36)
- Test: `backend/tests/unit/test_timeline_service.py`

**Interfaces:**
- Consumes: Task 11, port `ArchitectureRepository` (`list_elements`, `count_elements`, `relations_of`, `view_of`, `delete_element`).
- Produces (Python) :
  - `DuplicateGapError(DomainError)`
  - `ArchitectureService.list_plateaus() -> tuple[PlateauSummary, ...]`
  - `ArchitectureService.plateau_gap(a_id: UUID, b_id: UUID) -> PlateauComparison`
  - `ArchitectureService.create_gap(a_id: UUID, b_id: UUID, name: str | None = None) -> Element`
  - `ArchitectureService.roadmap(window_from: date | None = None, window_to: date | None = None) -> Roadmap`

- [ ] **Step 1: Tests qui échouent** — `backend/tests/unit/test_timeline_service.py` :

```python
"""Plateaus, the gap between two of them, and the roadmap — on the in-memory graph."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateGapError, ElementNotFoundError, NotAuthorisedError
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import InMemoryRepository, a_reader

pytestmark = pytest.mark.asyncio


async def plateau(service: ArchitectureService, name: str, **dates: str) -> Element:
    return await service.create_element(
        element_type=E.PLATEAU, name=name, properties=dates or None
    )


async def contain(service: ArchitectureService, owner: Element, *members: Element) -> None:
    for member in members:
        await service.connect(
            relationship_type=R.COMPOSITION, source_id=owner.id, target_id=member.id
        )


async def app(service: ArchitectureService, name: str, **dates: str) -> Element:
    return await service.create_element(
        element_type=E.APPLICATION_COMPONENT, name=name, properties=dates or None
    )


class TestPlateaus:
    async def test_they_come_earliest_first_with_what_they_contain(
        self, service: ArchitectureService
    ) -> None:
        as_is = await plateau(service, "As-Is", valid_from="2024-01-01")
        to_be = await plateau(service, "To-Be", valid_from="2028-01-01")
        undated = await plateau(service, "Idée")
        billing = await app(service, "Billing")
        await contain(service, to_be, billing)

        listed = await service.list_plateaus()

        assert [(s.element.name, s.member_count) for s in listed] == [
            ("As-Is", 0), ("To-Be", 1), ("Idée", 0),
        ]
        assert listed[0].lifespan.start == date(2024, 1, 1)
        assert as_is and undated

    async def test_only_plateaus_are_listed(self, service: ArchitectureService) -> None:
        await app(service, "Billing")

        assert await service.list_plateaus() == ()


class TestGap:
    async def test_it_says_what_leaves_arrives_and_stays(
        self, service: ArchitectureService
    ) -> None:
        as_is, to_be = await plateau(service, "As-Is"), await plateau(service, "To-Be")
        old, kept, new = await app(service, "Old"), await app(service, "Kept"), await app(service, "New")
        await contain(service, as_is, old, kept)
        await contain(service, to_be, kept, new)

        comparison = await service.plateau_gap(as_is.id, to_be.id)

        assert [e.name for e in comparison.diff.only_in_a] == ["Old"]
        assert [e.name for e in comparison.diff.only_in_b] == ["New"]
        assert [e.name for e in comparison.diff.common] == ["Kept"]
        assert comparison.gaps == ()

    async def test_a_plateau_cannot_be_compared_with_itself(
        self, service: ArchitectureService
    ) -> None:
        one = await plateau(service, "One")

        with pytest.raises(ValueError, match="itself"):
            await service.plateau_gap(one.id, one.id)

    async def test_an_unknown_plateau_is_a_404(self, service: ArchitectureService) -> None:
        one = await plateau(service, "One")

        with pytest.raises(ElementNotFoundError):
            await service.plateau_gap(one.id, uuid4())

    async def test_something_that_is_not_a_plateau_is_refused_by_name(
        self, service: ArchitectureService
    ) -> None:
        one, billing = await plateau(service, "One"), await app(service, "Billing")

        with pytest.raises(ValueError, match="Billing"):
            await service.plateau_gap(one.id, billing.id)


class TestCreatingTheGap:
    async def test_it_is_an_ordinary_gap_element_associated_to_both(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        as_is, to_be = await plateau(service, "As-Is"), await plateau(service, "To-Be")

        gap = await service.create_gap(as_is.id, to_be.id)

        assert gap.element_type is E.GAP
        assert gap.name == "Écart As-Is → To-Be"
        links = list(repository.relationships.values())
        assert {(l.relationship_type, l.source_id, l.target_id) for l in links} == {
            (R.ASSOCIATION, gap.id, as_is.id),
            (R.ASSOCIATION, gap.id, to_be.id),
        }
        assert [g.id for g in (await service.plateau_gap(as_is.id, to_be.id)).gaps] == [gap.id]

    async def test_a_name_can_be_given(self, service: ArchitectureService) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")

        assert (await service.create_gap(a.id, b.id, name="Migration ERP")).name == "Migration ERP"

    async def test_a_second_gap_for_the_same_pair_is_refused_in_either_order(
        self, service: ArchitectureService
    ) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")
        await service.create_gap(a.id, b.id)

        with pytest.raises(DuplicateGapError):
            await service.create_gap(a.id, b.id, name="Autre nom")
        with pytest.raises(DuplicateGapError):
            await service.create_gap(b.id, a.id, name="Encore un")

    async def test_a_reader_cannot_create_one(self, service: ArchitectureService) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await service.create_gap(a.id, b.id)

    async def test_a_gap_whose_association_fails_does_not_stay_behind(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")
        real = repository.add_relationship
        calls = 0

        async def failing_second(relationship):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("the store went away")
            return await real(relationship)

        repository.add_relationship = failing_second  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await service.create_gap(a.id, b.id)

        assert [e.element_type for e in repository.elements.values()] == [E.PLATEAU, E.PLATEAU]


class TestRoadmap:
    async def test_it_holds_plateaus_work_packages_and_deliverables_with_their_dates(
        self, service: ArchitectureService
    ) -> None:
        to_be = await plateau(service, "To-Be", valid_from="2028-01-01")
        wp = await service.create_element(
            element_type=E.WORK_PACKAGE, name="Migration",
            properties={"valid_from": "2026-06-01", "valid_until": "2027-12-31"},
        )
        deliverable = await service.create_element(element_type=E.DELIVERABLE, name="Runbook")
        await app(service, "Billing", valid_from="2020-01-01")  # not a roadmap type
        await service.connect(relationship_type=R.REALIZATION, source_id=wp.id, target_id=deliverable.id)
        await contain(service, to_be, wp)

        roadmap = await service.roadmap()

        assert {i.element.name for i in roadmap.items} == {"To-Be", "Migration", "Runbook"}
        by_name = {i.element.name: i for i in roadmap.items}
        assert by_name["Migration"].plateau_ids == (to_be.id,)
        assert by_name["Runbook"].lifespan.is_dated is False
        assert (roadmap.window_from, roadmap.window_to) == (date(2026, 6, 1), date(2028, 1, 1))
        assert [(e.relationship_type, e.source_id, e.target_id) for e in roadmap.edges] == [
            (R.REALIZATION, wp.id, deliverable.id)
        ]

    async def test_a_window_leaves_out_what_does_not_overlap_but_keeps_the_undated(
        self, service: ArchitectureService
    ) -> None:
        await service.create_element(
            element_type=E.WORK_PACKAGE, name="Early", properties={"valid_until": "2025-12-31"}
        )
        await service.create_element(
            element_type=E.WORK_PACKAGE, name="Inside", properties={"valid_from": "2026-03-01"}
        )
        await service.create_element(element_type=E.DELIVERABLE, name="Undated")

        roadmap = await service.roadmap(date(2026, 1, 1), date(2026, 12, 31))

        assert {i.element.name for i in roadmap.items} == {"Inside", "Undated"}

    async def test_a_window_wider_than_twenty_years_is_refused(
        self, service: ArchitectureService
    ) -> None:
        with pytest.raises(ValueError, match="20 years"):
            await service.roadmap(date(2000, 1, 1), date(2030, 1, 1))

    async def test_a_roadmap_with_no_date_at_all_has_no_window(
        self, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.DELIVERABLE, name="Undated")

        roadmap = await service.roadmap()

        assert (roadmap.window_from, roadmap.window_to) == (None, None)
        assert len(roadmap.items) == 1
```

(L'arête `triggering` est couverte par le test API de la Task 13.)

Ajouter dans `test_service_guards.py`, dans `WRITES[ArchitectureService]`, l'entrée `"create_gap",`.

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_timeline_service.py -x` → `ImportError: cannot import name 'DuplicateGapError'`.

- [ ] **Step 3: Implémenter.**

`domain/errors.py` :

```python
class DuplicateGapError(DomainError):
    """A gap already links these two plateaus (docs/adr/0044)."""
```
`api/errors.py` : importer `DuplicateGapError` et ajouter dans `_STATUS`, sous `InvalidLifespanError` :

```python
    DuplicateGapError: (status.HTTP_409_CONFLICT, "duplicate_gap"),
```

`services/architecture.py` — imports : `ElementFilter` doit exister à l'exécution ; remplacer le bloc `from ea.domain.errors import …` / `TYPE_CHECKING` par :

```python
from ea.domain.errors import CyclicContainmentError, DuplicateGapError, ElementNotFoundError
from ea.domain.ipam import validate_ipam_properties
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter
from ea.domain.timeline import (
    MAX_PLATEAUS,
    MAX_ROADMAP_ITEMS,
    Lifespan,
    PlateauComparison,
    PlateauSummary,
    Roadmap,
    RoadmapEdge,
    RoadmapItem,
    diff_plateaus,
    in_window,
    resolve_window,
    sort_plateaus,
    validate_lifespan_properties,
)
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import ArchitectureRepository, GraphView
```
et ajouter la constante `_ROADMAP_TYPES = (ElementType.PLATEAU, ElementType.WORK_PACKAGE, ElementType.DELIVERABLE)` sous `_CONTAINMENT`. Ajouter à la fin de la classe :

```python
    # --- Time (docs/adr/0042, 0044) ---------------------------------------
    # ponytail: one query per plateau for its members; plateaus are a handful
    # (MAX_PLATEAUS bounds the runaway), a batch query if that ever hurts.

    async def list_plateaus(self) -> tuple[PlateauSummary, ...]:
        """Every plateau, earliest first, with how many elements each one contains."""
        require_caller()
        plateaus = await self._repository.list_elements(
            ElementFilter(element_types=(ElementType.PLATEAU,), limit=MAX_PLATEAUS)
        )
        return sort_plateaus(
            [
                PlateauSummary(
                    element=plateau,
                    lifespan=Lifespan.read(plateau.properties),
                    member_count=len(await self._members_of(plateau)),
                )
                for plateau in plateaus
            ]
        )

    async def plateau_gap(self, a_id: UUID, b_id: UUID) -> PlateauComparison:
        """What changes between two plateaus, and the gaps already drawn between them."""
        require_caller()
        if a_id == b_id:
            msg = "a plateau cannot be compared with itself"
            raise ValueError(msg)
        a, b = await self._plateau(a_id), await self._plateau(b_id)
        diff = diff_plateaus(await self._members_of(a), await self._members_of(b))
        return PlateauComparison(a=a, b=b, diff=diff, gaps=await self._gaps_between(a, b))

    async def create_gap(self, a_id: UUID, b_id: UUID, name: str | None = None) -> Element:
        """Draw the gap between two plateaus: an ordinary `gap` element associated to both."""
        require_editor()
        comparison = await self.plateau_gap(a_id, b_id)
        a, b = comparison.a, comparison.b
        if comparison.gaps:
            msg = f"{comparison.gaps[0].name!r} already links {a.name!r} and {b.name!r}"
            raise DuplicateGapError(msg)
        gap = await self.create_element(
            element_type=ElementType.GAP, name=name or f"Écart {a.name} → {b.name}"
        )
        try:
            for plateau in (a, b):
                await self.connect(
                    relationship_type=RelationshipType.ASSOCIATION,
                    source_id=gap.id,
                    target_id=plateau.id,
                )
        except Exception:
            # Two writes in two transactions: do not leave a gap tied to nothing.
            await self._repository.delete_element(gap.id)
            raise
        return gap

    async def roadmap(
        self, window_from: date | None = None, window_to: date | None = None
    ) -> Roadmap:
        """Plateaus, work packages and deliverables with their dates, over a window."""
        require_caller()
        criteria = ElementFilter(element_types=_ROADMAP_TYPES, limit=MAX_ROADMAP_ITEMS)
        if await self._repository.count_elements(criteria) > MAX_ROADMAP_ITEMS:
            msg = f"more than {MAX_ROADMAP_ITEMS} plateaus, work packages and deliverables"
            raise ValueError(msg)
        elements = await self._repository.list_elements(criteria)
        spans = {element.id: Lifespan.read(element.properties) for element in elements}
        start, end = resolve_window(spans.values(), window_from, window_to)
        shown = [e for e in elements if in_window(spans[e.id], start, end)]
        shown.sort(key=lambda e: (spans[e.id].start is None, spans[e.id].start or date.min, e.name.lower(), str(e.id)))
        ids = {element.id for element in shown}
        links = (await self._repository.view_of(list(ids))).relationships
        containers: dict[UUID, list[UUID]] = {}
        for link in links:
            if link.relationship_type in _CONTAINMENT and link.source_type is ElementType.PLATEAU:
                containers.setdefault(link.target_id, []).append(link.source_id)
        return Roadmap(
            items=tuple(
                RoadmapItem(element, spans[element.id], tuple(containers.get(element.id, ())))
                for element in shown
            ),
            edges=tuple(
                RoadmapEdge(link.source_id, link.target_id, link.relationship_type)
                for link in links
                if link.relationship_type in (RelationshipType.TRIGGERING, RelationshipType.REALIZATION)
            ),
            window_from=start,
            window_to=end,
        )

    async def _plateau(self, plateau_id: UUID) -> Element:
        element = await self.get_element(plateau_id)
        if element.element_type is not ElementType.PLATEAU:
            msg = f"{element.name!r} is a {element.element_type.value}, not a plateau"
            raise ValueError(msg)
        return element

    async def _members_of(self, plateau: Element) -> tuple[Element, ...]:
        """What a plateau contains: the targets of its compositions and aggregations."""
        view = await self._repository.relations_of(
            plateau.id,
            relationship_types=(RelationshipType.COMPOSITION, RelationshipType.AGGREGATION),
        )
        contained = {l.target_id for l in view.relationships if l.source_id == plateau.id}
        return tuple(element for element in view.elements if element.id in contained)

    async def _gaps_between(self, a: Element, b: Element) -> tuple[Element, ...]:
        async def gaps_of(plateau: Element) -> dict[UUID, Element]:
            view = await self._repository.relations_of(
                plateau.id, relationship_types=(RelationshipType.ASSOCIATION,)
            )
            return {e.id: e for e in view.elements if e.element_type is ElementType.GAP}

        left, right = await gaps_of(a), await gaps_of(b)
        return tuple(
            sorted((left[i] for i in left.keys() & right.keys()), key=lambda e: (e.name.lower(), str(e.id)))
        )
```
Nettoyage lint : `for l in` → `for link in` (ruff E741) et lignes > 100 colonnes coupées par `ruff format` (`make lint`).

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_timeline_service.py tests/unit/test_service_guards.py -x` ; `make lint typecheck-be`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/errors.py backend/src/ea/api/errors.py backend/src/ea/services/architecture.py backend/tests/unit/test_timeline_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(services): plateaus, gap between two plateaus, roadmap" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 13: Routeur `api/timeline.py`

**Files:**
- Create: `backend/src/ea/api/timeline.py`
- Modify: `backend/src/ea/api/schemas.py` (nouveaux modèles de lecture, après `GraphRead` l. 225-238)
- Modify: `backend/src/ea/main.py` (import l. 15-30 ; `include_router` l. 531-537)
- Test: `backend/tests/e2e/test_timeline_api.py` (compléter) ; `backend/tests/unit/test_openapi.py` (ajout)

**Interfaces:**
- Consumes: Task 12.
- Produces (Python, schemas) :
  - `PlateauRead(id: UUID, name: str, valid_from: date | None, valid_until: date | None, element_count: int)` + `of(summary: PlateauSummary)`
  - `PlateauGapRead(a: ElementRead, b: ElementRead, only_in_a: list[ElementRead], only_in_b: list[ElementRead], common: list[ElementRead], gaps: list[ElementRead])` + `of(comparison: PlateauComparison)`
  - `GapCreate(a: UUID, b: UUID, name: Name | None = None)`
  - `RoadmapItemRead(id, name, element_type, valid_from: date | None, valid_until: date | None, plateau_ids: list[UUID])`, `RoadmapEdgeRead(source_id, target_id, relationship_type)`, `RoadmapRead(items, edges, window_from: date | None, window_to: date | None)` + `of(roadmap: Roadmap)`
  - HTTP : `GET /plateaus`, `GET /plateaus/gap?a=&b=`, `POST /plateaus/gap` (201 `ElementRead`), `GET /roadmap?from=&to=`

- [ ] **Step 1: Tests qui échouent** — ajouter à `backend/tests/e2e/test_timeline_api.py` :

```python
from uuid import uuid4

from tests.conftest import StaticVerifier, a_reader, an_editor


async def link(client: httpx.AsyncClient, kind: str, source: str, target: str) -> None:
    response = await client.post(
        "/relationships",
        json={"relationship_type": kind, "source_id": source, "target_id": target},
    )
    assert response.status_code == 201, response.text


async def two_plateaus(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        await create(client, "plateau", "As-Is", valid_from="2024-01-01"),
        await create(client, "plateau", "To-Be", valid_from="2028-01-01"),
    )


class TestPlateaus:
    async def test_they_are_listed_earliest_first_with_a_count(
        self, client: httpx.AsyncClient
    ) -> None:
        as_is, to_be = await two_plateaus(client)
        billing = await create(client, "application_component", "Billing")
        await link(client, "composition", to_be["id"], billing["id"])

        body = (await client.get("/plateaus")).json()

        assert [(p["name"], p["element_count"]) for p in body] == [("As-Is", 0), ("To-Be", 1)]
        assert body[0]["valid_from"] == "2024-01-01" and body[0]["valid_until"] is None
        assert as_is


class TestGap:
    async def test_the_departures_arrivals_and_survivors(self, client: httpx.AsyncClient) -> None:
        as_is, to_be = await two_plateaus(client)
        old = await create(client, "application_component", "Old")
        new = await create(client, "application_component", "New")
        kept = await create(client, "application_component", "Kept")
        for plateau, members in ((as_is, (old, kept)), (to_be, (kept, new))):
            for member in members:
                await link(client, "composition", plateau["id"], member["id"])

        body = (
            await client.get("/plateaus/gap", params={"a": as_is["id"], "b": to_be["id"]})
        ).json()

        assert [e["name"] for e in body["only_in_a"]] == ["Old"]
        assert [e["name"] for e in body["only_in_b"]] == ["New"]
        assert [e["name"] for e in body["common"]] == ["Kept"]
        assert body["gaps"] == []

    async def test_comparing_a_plateau_with_itself_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        as_is, _ = await two_plateaus(client)

        response = await client.get("/plateaus/gap", params={"a": as_is["id"], "b": as_is["id"]})

        assert response.status_code == 422 and response.json()["error"] == "invalid_input"

    async def test_an_unknown_plateau_is_a_404(self, client: httpx.AsyncClient) -> None:
        as_is, _ = await two_plateaus(client)

        response = await client.get("/plateaus/gap", params={"a": as_is["id"], "b": str(uuid4())})

        assert response.status_code == 404

    async def test_the_gap_is_created_once_then_refused(self, client: httpx.AsyncClient) -> None:
        as_is, to_be = await two_plateaus(client)
        payload = {"a": as_is["id"], "b": to_be["id"]}

        created = await client.post("/plateaus/gap", json=payload)
        again = await client.post("/plateaus/gap", json={**payload, "name": "Autre"})

        assert created.status_code == 201
        assert created.json()["element_type"] == "gap"
        assert created.json()["name"] == "Écart As-Is → To-Be"
        assert again.status_code == 409 and again.json()["error"] == "duplicate_gap"
        listed = (await client.get("/plateaus/gap", params=payload | {})).json()
        assert [g["id"] for g in listed["gaps"]] == [created.json()["id"]]

    async def test_an_unexpected_field_is_a_422(self, client: httpx.AsyncClient) -> None:
        as_is, to_be = await two_plateaus(client)

        response = await client.post(
            "/plateaus/gap", json={"a": as_is["id"], "b": to_be["id"], "nmae": "x"}
        )

        assert response.status_code == 422


class TestRoadmap:
    async def test_it_carries_the_dates_the_edges_and_the_window(
        self, client: httpx.AsyncClient
    ) -> None:
        _, to_be = await two_plateaus(client)
        step1 = await create(client, "work_package", "Phase 1", valid_from="2026-01-01", valid_until="2026-12-31")
        step2 = await create(client, "work_package", "Phase 2", valid_from="2027-01-01", valid_until="2027-12-31")
        runbook = await create(client, "deliverable", "Runbook")
        await link(client, "triggering", step1["id"], step2["id"])
        await link(client, "realization", step2["id"], runbook["id"])
        await link(client, "composition", to_be["id"], step2["id"])

        body = (await client.get("/roadmap")).json()

        assert body["window_from"] == "2024-01-01" and body["window_to"] == "2028-01-01"
        by_name = {i["name"]: i for i in body["items"]}
        assert by_name["Phase 2"]["plateau_ids"] == [to_be["id"]]
        assert by_name["Runbook"]["valid_from"] is None
        assert {(e["relationship_type"]) for e in body["edges"]} == {"triggering", "realization"}

    async def test_from_and_to_narrow_it(self, client: httpx.AsyncClient) -> None:
        await create(client, "work_package", "Early", valid_until="2025-12-31")
        await create(client, "work_package", "Inside", valid_from="2026-03-01")

        body = (await client.get("/roadmap", params={"from": "2026-01-01", "to": "2026-12-31"})).json()

        assert [i["name"] for i in body["items"]] == ["Inside"]

    async def test_a_window_wider_than_twenty_years_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/roadmap", params={"from": "2000-01-01", "to": "2030-01-01"})

        assert response.status_code == 422

    async def test_an_invalid_day_is_a_422(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/roadmap", params={"from": "demain"})).status_code == 422


class TestWhoMayDoWhat:
    async def test_a_reader_reads_but_cannot_create_the_gap(
        self, service: ArchitectureService, nobody_calling: None
    ) -> None:
        verifier = StaticVerifier({"editor": an_editor(), "reader": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            editor, reader = {"Authorization": "Bearer editor"}, {"Authorization": "Bearer reader"}
            ids = []
            for name in ("A", "B"):
                made = await client.post(
                    "/elements", json={"element_type": "plateau", "name": name}, headers=editor
                )
                ids.append(made.json()["id"])

            assert (await client.get("/plateaus")).status_code == 401
            assert (await client.get("/plateaus", headers=reader)).status_code == 200
            assert (await client.get("/roadmap", headers=reader)).status_code == 200
            forbidden = await client.post(
                "/plateaus/gap", json={"a": ids[0], "b": ids[1]}, headers=reader
            )
            assert forbidden.status_code == 403
            ok = await client.post(
                "/plateaus/gap", json={"a": ids[0], "b": ids[1]}, headers=editor
            )
            assert ok.status_code == 201
```

Ajouter à `backend/tests/unit/test_openapi.py` :

```python
def test_every_time_endpoint_is_described() -> None:
    paths = openapi_document()["paths"]

    assert set(paths["/plateaus"]) == {"get"}
    assert set(paths["/plateaus/gap"]) == {"get", "post"}
    assert set(paths["/roadmap"]) == {"get"}
```

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/e2e/test_timeline_api.py -x` → 404 sur `/plateaus`.

- [ ] **Step 3: Implémenter.**

`api/schemas.py` — imports `from ea.domain.timeline import PlateauComparison, PlateauSummary, Roadmap` ; après `GraphRead` :

```python
class PlateauRead(BaseModel):
    """A plateau with the days it is reached and how many elements it contains."""

    id: UUID
    name: str
    valid_from: date | None
    valid_until: date | None
    element_count: int

    @classmethod
    def of(cls, summary: PlateauSummary) -> PlateauRead:
        return cls(
            id=summary.element.id,
            name=summary.element.name,
            valid_from=summary.lifespan.start,
            valid_until=summary.lifespan.end,
            element_count=summary.member_count,
        )


class PlateauGapRead(BaseModel):
    """What leaves, what arrives and what stays between two plateaus (docs/adr/0044)."""

    a: ElementRead
    b: ElementRead
    only_in_a: list[ElementRead] = Field(description="Leaving: in the first plateau only.")
    only_in_b: list[ElementRead] = Field(description="Arriving: in the second plateau only.")
    common: list[ElementRead]
    gaps: list[ElementRead] = Field(description="The `gap` elements already linking both.")

    @classmethod
    def of(cls, comparison: PlateauComparison) -> PlateauGapRead:
        def many(elements: tuple[Element, ...]) -> list[ElementRead]:
            return [ElementRead.of(element) for element in elements]

        return cls(
            a=ElementRead.of(comparison.a),
            b=ElementRead.of(comparison.b),
            only_in_a=many(comparison.diff.only_in_a),
            only_in_b=many(comparison.diff.only_in_b),
            common=many(comparison.diff.common),
            gaps=many(comparison.gaps),
        )


class GapCreate(_Input):
    """Draw the gap between two plateaus."""

    a: UUID
    b: UUID
    name: Name | None = Field(default=None, description="Default: « Écart <A> → <B> ».")


class RoadmapItemRead(BaseModel):
    id: UUID
    name: str
    element_type: ElementType
    valid_from: date | None
    valid_until: date | None
    plateau_ids: list[UUID]


class RoadmapEdgeRead(BaseModel):
    source_id: UUID
    target_id: UUID
    relationship_type: RelationshipType


class RoadmapRead(BaseModel):
    """Plateaus, work packages and deliverables with their lifespans, over a window."""

    items: list[RoadmapItemRead]
    edges: list[RoadmapEdgeRead]
    window_from: date | None
    window_to: date | None

    @classmethod
    def of(cls, roadmap: Roadmap) -> RoadmapRead:
        return cls(
            items=[
                RoadmapItemRead(
                    id=item.element.id,
                    name=item.element.name,
                    element_type=item.element.element_type,
                    valid_from=item.lifespan.start,
                    valid_until=item.lifespan.end,
                    plateau_ids=list(item.plateau_ids),
                )
                for item in roadmap.items
            ],
            edges=[
                RoadmapEdgeRead(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    relationship_type=edge.relationship_type,
                )
                for edge in roadmap.edges
            ],
            window_from=roadmap.window_from,
            window_to=roadmap.window_to,
        )
```

`api/timeline.py` :

```python
"""Plateaus, the gap between two of them, and the roadmap (docs/adr/0044).

The dates themselves are written with `PATCH /elements/{id}`; nothing here
stores a fact the graph does not already hold.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ea.api.dependencies import Architecture
from ea.api.schemas import (
    AsOf,
    ElementRead,
    ErrorResponse,
    GapCreate,
    PlateauGapRead,
    PlateauRead,
    RoadmapRead,
)

router = APIRouter(tags=["timeline"])

_ERROR = {"model": ErrorResponse}
GAP_READ: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}
GAP_WRITE: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **GAP_READ,
    status.HTTP_409_CONFLICT: _ERROR,
}
ROADMAP_REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR
}


@router.get("/plateaus", response_model=list[PlateauRead])
async def list_plateaus(service: Architecture) -> list[PlateauRead]:
    """Every plateau, earliest first, with the days it is reached and its size."""
    return [PlateauRead.of(summary) for summary in await service.list_plateaus()]


@router.get("/plateaus/gap", response_model=PlateauGapRead, responses=GAP_READ)
async def read_gap(
    service: Architecture, a: Annotated[UUID, Query()], b: Annotated[UUID, Query()]
) -> PlateauGapRead:
    """What leaves, arrives and stays between plateau `a` and plateau `b`."""
    return PlateauGapRead.of(await service.plateau_gap(a, b))


@router.post(
    "/plateaus/gap",
    response_model=ElementRead,
    status_code=status.HTTP_201_CREATED,
    responses=GAP_WRITE,
)
async def create_gap(payload: GapCreate, service: Architecture) -> ElementRead:
    """Draw the gap: a `gap` element associated to both plateaus."""
    return ElementRead.of(await service.create_gap(payload.a, payload.b, name=payload.name))


@router.get("/roadmap", response_model=RoadmapRead, responses=ROADMAP_REJECTED)
async def read_roadmap(
    service: Architecture,
    window_from: Annotated[AsOf | None, Query(alias="from")] = None,
    window_to: Annotated[AsOf | None, Query(alias="to")] = None,
) -> RoadmapRead:
    """Plateaus, work packages and deliverables over a window (default: the data's own)."""
    return RoadmapRead.of(await service.roadmap(window_from, window_to))
```
(`from datetime import date` y est inutile : retirer l'import.)

`main.py` : `from ea.api.timeline import router as timeline_router` (ordre alphabétique) et, après la ligne de `files_router` :

```python
    app.include_router(timeline_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
```

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/e2e tests/unit -x -q` ; `uv run mypy src`. (`test_auth_api.py::test_every_route_but_health_wants_a_token` parcourt les nouvelles routes : 401 sans jeton.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/api/timeline.py backend/src/ea/api/schemas.py backend/src/ea/main.py backend/tests/e2e/test_timeline_api.py backend/tests/unit/test_openapi.py
git commit -m "feat(api): plateaus, gap between plateaus and roadmap endpoints" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 14: Outils MCP `list_plateaus` et `gap_between`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports schemas ; `INSTRUCTIONS` l. ~95-168 ; nouvelle section « Time » après les traversées)
- Test: `backend/tests/unit/test_mcp_server.py` (liste complète l. 85-120 ; `TestTime`)

**Interfaces:**
- Consumes: `PlateauRead`, `PlateauGapRead` (Task 13).
- Produces (Python) : `list_plateaus() -> list[PlateauRead]` (`READS`), `gap_between(a: ElementId, b: ElementId, create_gap: bool = False) -> PlateauGapRead` (`ADDS`).

- [ ] **Step 1: Tests qui échouent** — dans `test_mcp_server.py` : ajouter `"list_plateaus",` et `"gap_between",` à l'ensemble de `test_it_exposes_the_whole_service_and_nothing_else`, puis dans `TestTime` :

```python
    async def test_plateaus_are_listed_to_compare_two_states_of_the_it(
        self, server: MCPServer[Any]
    ) -> None:
        await call(server, "create_element", element_type="plateau", name="To-Be",
                   properties={"valid_from": "2028-01-01"})
        await call(server, "create_element", element_type="plateau", name="As-Is",
                   properties={"valid_from": "2024-01-01"})

        listed = await call(server, "list_plateaus")

        assert [p["name"] for p in listed["result"]] == ["As-Is", "To-Be"]

    async def test_the_gap_is_read_by_default_and_created_on_request(
        self, server: MCPServer[Any]
    ) -> None:
        as_is = await an_element(server, "plateau", "As-Is")
        to_be = await an_element(server, "plateau", "To-Be")
        new = await an_element(server, "application_component", "New")
        await call(server, "connect_elements", relationship_type="composition",
                   source_id=to_be["id"], target_id=new["id"])

        read = await call(server, "gap_between", a=as_is["id"], b=to_be["id"])
        assert [e["name"] for e in read["only_in_b"]] == ["New"]
        assert read["gaps"] == []

        created = await call(server, "gap_between", a=as_is["id"], b=to_be["id"], create_gap=True)
        assert [g["name"] for g in created["gaps"]] == ["Écart As-Is → To-Be"]
        with pytest.raises(ToolError, match="already links"):
            await call(server, "gap_between", a=as_is["id"], b=to_be["id"], create_gap=True)

    async def test_comparing_a_plateau_with_itself_is_explained(
        self, server: MCPServer[Any]
    ) -> None:
        one = await an_element(server, "plateau", "One")

        with pytest.raises(ToolError, match="itself"):
            await call(server, "gap_between", a=one["id"], b=one["id"])

    async def test_a_reader_cannot_ask_for_the_gap_to_be_created(
        self, server: MCPServer[Any]
    ) -> None:
        a = await an_element(server, "plateau", "A")
        b = await an_element(server, "plateau", "B")

        with acting_as(a_reader()), pytest.raises(ToolError):
            await call(server, "gap_between", a=a["id"], b=b["id"], create_gap=True)
        with acting_as(a_reader()):
            assert (await call(server, "gap_between", a=a["id"], b=b["id"]))["gaps"] == []
```
(le résultat d'un outil retournant une liste est enveloppé sous `result` par le SDK ; si le SDK renvoie la liste nue, ajuster l'accès au premier échec.)

- [ ] **Step 2: Lancer, échec attendu** — `cd backend && uv run pytest tests/unit/test_mcp_server.py -x` → l'ensemble attendu diffère (deux noms manquent).

- [ ] **Step 3: Implémenter** — `mcp/server.py` : importer `PlateauGapRead`, `PlateauRead` depuis `ea.api.schemas` ; ajouter à `INSTRUCTIONS`, avant le paragraphe « The server also keeps files » :

```
Elements and relationships carry time: `valid_from` and `valid_until` in their
properties, written as `YYYY-MM-DD` (both inclusive, either may be omitted; a
partial date such as `2027-06` is refused). To see the catalogue as it is on a
day, pass `as_of` to `list_elements`, `neighbourhood` or `impact_of`. To compare
two states of the information system, take two plateaus (`list_plateaus`) and
call `gap_between`: it computes what leaves, what arrives and what stays — never
work that out by hand. `create_gap=True` also records the gap as an element.
```

et, après `impact_of`, avant la section Documents :

```python
    # --- Time (docs/adr/0042, 0044) ----------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_plateaus() -> list[PlateauRead]:
        """Every plateau, earliest first — the states of the IT to compare two of.

        A plateau is a stable state of the architecture (an As-Is, a To-Be). Its
        `valid_from` is the day that state is reached; `element_count` is what
        it contains. Pass two ids to `gap_between` to see how they differ.
        """
        return [PlateauRead.of(summary) for summary in await get_service().list_plateaus()]

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def gap_between(
        a: ElementId, b: ElementId, create_gap: bool = False
    ) -> PlateauGapRead:
        """What leaves, arrives and stays between plateau `a` and plateau `b`.

        `only_in_a` is what leaves, `only_in_b` what arrives, `common` what stays;
        `gaps` lists the gap elements already drawn between the two. This reads
        and writes nothing unless `create_gap` is true, in which case it also
        creates the `gap` element, associated to both plateaus (refused if one
        already links them) — confirm with the person you work for first.
        """
        service = get_service()
        if create_gap:
            await service.create_gap(a, b)
        return PlateauGapRead.of(await service.plateau_gap(a, b))
```

- [ ] **Step 4: Relancer, PASS** — `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/e2e/test_mcp_endpoint.py -x` ; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): list_plateaus and gap_between tools" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 15: Intégration du gap, ADR « écart calculé » et vérification du lot 3

**Files:**
- Create: `backend/tests/integration/test_plateau_gap_graph.py`
- Create: `docs/adr/0044-ecart-entre-plateaux-calcule.md`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)

**Interfaces:** client TS régénéré : `PlateauRead`, `PlateauGapRead`, `GapCreate`, `RoadmapRead`, `RoadmapItemRead`, chemins `/plateaus`, `/plateaus/gap`, `/roadmap`.

- [ ] **Step 1: Test d'intégration** — `backend/tests/integration/test_plateau_gap_graph.py` :

```python
"""The gap between plateaus on real PostgreSQL: names, associations and the unique name."""

from __future__ import annotations

from datetime import date

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateGapError
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_the_gap_is_stored_and_found_again_from_either_plateau(
    graph_service: ArchitectureService,
) -> None:
    as_is = await graph_service.create_element(element_type=E.PLATEAU, name="As-Is")
    to_be = await graph_service.create_element(element_type=E.PLATEAU, name="To-Be")
    new = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="New")
    await graph_service.connect(relationship_type=R.COMPOSITION, source_id=to_be.id, target_id=new.id)

    gap = await graph_service.create_gap(as_is.id, to_be.id)

    forward = await graph_service.plateau_gap(as_is.id, to_be.id)
    backward = await graph_service.plateau_gap(to_be.id, as_is.id)
    assert [g.id for g in forward.gaps] == [gap.id] == [g.id for g in backward.gaps]
    assert [e.name for e in forward.diff.only_in_b] == ["New"]
    with pytest.raises(DuplicateGapError):
        await graph_service.create_gap(to_be.id, as_is.id)


async def test_the_roadmap_reads_the_dates_back_from_jsonb(
    graph_service: ArchitectureService,
) -> None:
    await graph_service.create_element(
        element_type=E.WORK_PACKAGE,
        name="Migration",
        properties={"valid_from": "2026-06-01", "valid_until": "2027-12-31"},
    )

    roadmap = await graph_service.roadmap()

    assert [(i.element.name, i.lifespan.start, i.lifespan.end) for i in roadmap.items] == [
        ("Migration", date(2026, 6, 1), date(2027, 12, 31))
    ]
```

- [ ] **Step 2: Rédiger l'ADR** — `docs/adr/0044-ecart-entre-plateaux-calcule.md` :

````markdown
---
titre: L'écart entre plateaux est calculé, un gap est un élément ordinaire
date: 2026-10-10
statut: Proposition
affects: domain/timeline.py, services/architecture.py, api/timeline.py, mcp/server.py
---

# L'écart entre plateaux est calculé, un gap est un élément ordinaire

## Contexte

Modéliser l'évolution du SI demande de comparer deux états : ce qui sort, ce qui arrive, ce qui reste, et de garder trace de l'écart décidé. Le métamodèle a déjà tout le vocabulaire : un `plateau` est un conteneur (`_CONTAINERS`), `plateau → plateau` en `triggering` est permis, `work_package —realization→ deliverable` est dans `_EXTRA_ALLOWED`, `association` est universelle. *As-Is* et *To-Be* ne sont pas des types : ce sont deux plateaux pris à deux dates.

## Décision

- **Aucune table, aucune règle de métamodèle nouvelle.** Un plateau « contient » ce dont il est la source d'une `composition` ou d'une `aggregation` : l'appartenance est **calculée**, jamais stockée en double (même philosophie que l'IPAM, ADR 0020).
- **L'écart est calculé** par la fonction pure `diff_plateaus(a, b)` : `only_in_a` (sorties), `only_in_b` (arrivées), `common`. Il n'est pas persisté.
- **Un `gap` est un élément ordinaire**, créé par `POST /plateaus/gap` (ou l'outil MCP `gap_between(create_gap=True)`) et relié par deux `association` (source le gap, cible chaque plateau). « Existe déjà pour ce couple » = un `gap` associé aux deux plateaux, dans n'importe quel ordre → 409 `duplicate_gap`. Si la seconde association échoue, le gap créé est supprimé : deux écritures, deux transactions, aucun gap orphelin.
- **Les cas d'usage vivent dans `ArchitectureService`** (`list_plateaus`, `plateau_gap`, `create_gap`, `roadmap`) : un service de plus aurait imposé un cinquième fournisseur à l'adaptateur MCP pour rien.
- **La feuille de route** (`GET /roadmap`) renvoie plateaux, work packages et deliverables avec leurs `Lifespan`, les arêtes `triggering` / `realization` entre eux et leur plateau d'appartenance ; fenêtre par défaut = bornes des données, bornée à 20 ans (7320 jours) ; au plus 1000 éléments, sinon 422 plutôt qu'une troncature silencieuse. Les éléments sans date sont toujours renvoyés (le SPA les range dans une bande « non daté »).
- Lecture : `require_caller()` ; `POST /plateaus/gap` : `require_editor()`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Écart calculé, gap = élément | Rien à synchroniser ; visible partout (catalogue, voisinage, MCP) | Le « déjà créé » se déduit des associations | Retenu |
| Table `plateau_gaps` | Contrainte d'unicité native | Seconde vérité à tenir d'accord avec les compositions | Écarté |
| Plateau défini par ses dates | Automatique | Un plateau est un choix d'architecte, pas un filtre ; une aide « proposer les membres actifs à la date » reste une suggestion | Écarté |
| Service dédié `TimelineService` | Séparation | Cinquième fournisseur MCP, câblage de chaque test | Écarté |

## Conséquences

- Un plateau dont personne n'a composé les membres donne un écart vide : c'est exact, et le SPA le dit.
- Plateaux : une requête par plateau pour compter ses membres (borné par `MAX_PLATEAUS = 500`) ; à regrouper si le nombre de plateaux grossit.
- La création du gap n'est pas atomique (deux transactions) ; le nettoyage couvre l'échec, pas un arrêt brutal entre les deux écritures — ce cas laisse un gap non relié, visible et supprimable comme tout élément.

## Références

- ADR liés : [0042](0042-temps-de-validite-dans-les-proprietes.md), [0043](0043-le-parametre-as-of-des-lectures.md), [0020](0020-adressage-ip-dans-les-proprietes.md)
- Code concerné : `backend/src/ea/domain/timeline.py`, `backend/src/ea/services/architecture.py`, `backend/src/ea/api/timeline.py`
````

- [ ] **Step 3: Régénérer le contrat et vérifier le lot** — `make openapi` ; `make openapi-check` ; `make check` ; `make test-integration`. Corriger les noms de fichiers d'ADR liés (`ls docs/adr`).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/test_plateau_gap_graph.py docs/adr/0044-ecart-entre-plateaux-calcule.md backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "docs(adr): gap between plateaus is computed; regenerate the client" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 4 — SPA : sélecteur `?asof=`, champs de date, panneau d'écart

### Task 16: `lib/asof.ts` et garde de propagation du routeur

**Files:**
- Create: `frontend/src/lib/asof.ts`
- Modify: `frontend/src/router/index.ts` (dans `createAppRouter`, après le `beforeEach` existant, l. 48-65)
- Test: `frontend/tests/asof.spec.ts`

**Interfaces:**
- Consumes: `vue-router`.
- Produces (TypeScript) :
  - `parseAsOf(value: unknown): string | undefined` — `YYYY-MM-DD` réel ou `undefined`
  - `useAsOf(): { asOf: ComputedRef<string | undefined>; setAsOf: (day: string) => void }` — `setAsOf('')` retire `asof` ; `replace`

- [ ] **Step 1: Test qui échoue** — `frontend/tests/asof.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'
import { defineComponent, h } from 'vue'
import { render } from '@testing-library/vue'

import { parseAsOf, useAsOf } from '../src/lib/asof'
import { createAppRouter } from '../src/router'

describe('parseAsOf', () => {
  it('keeps a real day and nothing else', () => {
    expect(parseAsOf('2028-01-01')).toBe('2028-01-01')
    expect(parseAsOf('2028-02-30')).toBeUndefined()
    expect(parseAsOf('2028-1-1')).toBeUndefined()
    expect(parseAsOf('demain')).toBeUndefined()
    expect(parseAsOf(['2028-01-01'])).toBeUndefined()
    expect(parseAsOf(undefined)).toBeUndefined()
  })
})

describe('the asof query parameter', () => {
  it('follows the user from one section to the next', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements?asof=2028-01-01')

    await router.push('/relations')

    expect(router.currentRoute.value.path).toBe('/relations')
    expect(router.currentRoute.value.query.asof).toBe('2028-01-01')
  })

  it('does not invent a date nobody asked for', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements')

    await router.push('/relations')

    expect(router.currentRoute.value.query.asof).toBeUndefined()
  })

  it('lets a section ask for its own date', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements?asof=2028-01-01')

    await router.push('/relations?asof=2030-05-05')

    expect(router.currentRoute.value.query.asof).toBe('2030-05-05')
  })

  it('does not bring the date back after it was cleared on the same page', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements?asof=2028-01-01')
    await router.isReady()
    let api: ReturnType<typeof useAsOf> | undefined
    render(
      defineComponent({
        setup() {
          api = useAsOf()
          return () => h('div')
        },
      }),
      { global: { plugins: [router] } },
    )

    api?.setAsOf('')
    await router.isReady()
    await new Promise((resolve) => setTimeout(resolve))

    expect(router.currentRoute.value.query.asof).toBeUndefined()
  })

  it('reads the day from the URL and writes it back with replace', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements?asof=2028-01-01&element=abc')
    await router.isReady()
    let api: ReturnType<typeof useAsOf> | undefined
    render(
      defineComponent({
        setup() {
          api = useAsOf()
          return () => h('div')
        },
      }),
      { global: { plugins: [router] } },
    )
    expect(api?.asOf.value).toBe('2028-01-01')

    api?.setAsOf('2030-02-02')
    await new Promise((resolve) => setTimeout(resolve))

    expect(router.currentRoute.value.query).toEqual({ asof: '2030-02-02', element: 'abc' })
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/asof.spec.ts` → `Failed to resolve import "../src/lib/asof"`.

- [ ] **Step 3: Implémenter.**

`frontend/src/lib/asof.ts` :

```ts
// The day the whole catalogue is read on: `?asof=YYYY-MM-DD` in the URL, or
// nothing — "today, no filter". It is URL state like the depth of a traversal
// (docs/adr/0010): a link carries it, the back button undoes it, and changing it
// replaces the history entry instead of burying the page under dozens of dates.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** A real day written YYYY-MM-DD, or `undefined` — a hand-typed URL never reaches the API. */
export function parseAsOf(value: unknown): string | undefined {
  if (typeof value !== 'string' || !DAY.test(value)) {
    return undefined
  }
  const day = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== value ? undefined : value
}

export function useAsOf() {
  const route = useRoute()
  const router = useRouter()

  const asOf = computed(() => parseAsOf(route.query.asof))

  /** Read the catalogue on `day`; an empty or invalid day goes back to "today". */
  function setAsOf(day: string): void {
    const { asof: _dropped, ...rest } = route.query
    const valid = parseAsOf(day)
    void router.replace({ query: valid ? { ...rest, asof: valid } : rest })
  }

  return { asOf, setAsOf }
}
```

`router/index.ts` — importer `parseAsOf` (`import { parseAsOf } from '../lib/asof'`) et, juste avant `return router`, ajouter :

```ts
  // The date the catalogue is read on follows the user from section to section
  // (docs/adr/0043): the menu and every internal link land on the next section
  // with `?asof=` still on. Only a change of path carries it, so clearing the
  // date on the page you are on stays cleared.
  router.beforeEach((to, from) => {
    const kept = parseAsOf(from.query.asof)
    if (kept && to.query.asof === undefined && to.path !== from.path && !to.meta.public) {
      return { ...to, query: { ...to.query, asof: kept }, replace: true }
    }
    return true
  })
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/asof.spec.ts tests/router.spec.ts` ; `npm run lint && npm run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/asof.ts frontend/src/router/index.ts frontend/tests/asof.spec.ts
git commit -m "feat(spa): ?asof= in the URL, carried from section to section" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 17: Sélecteur de date et bandeau dans le shell

**Files:**
- Create: `frontend/src/components/AsOfPicker.vue`
- Modify: `frontend/src/App.vue` (script l. 1-9, template l. 11-27)
- Test: `frontend/tests/AsOfPicker.spec.ts`

**Interfaces:**
- Consumes: `useAsOf` (Task 16).
- Produces : composant `AsOfPicker` (champ `<input type="date">` libellé « Vue au », bouton « Aujourd'hui » quand une date est posée) ; bandeau `role="status"` « Vue au <date> » dans `App.vue`.

- [ ] **Step 1: Test qui échoue** — `frontend/tests/AsOfPicker.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import App from '../src/App.vue'
import AsOfPicker from '../src/components/AsOfPicker.vue'
import { createAppRouter } from '../src/router'

async function open(path: string, component = AsOfPicker) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      return Promise.resolve(
        new Response(JSON.stringify(url.endsWith('/health') ? { status: 'ok' } : []), { status: 200 }),
      )
    }),
  )
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  render(component, { global: { plugins: [router] } })
  return router
}

describe('AsOfPicker', () => {
  it('shows no date by default: today, no filter', async () => {
    await open('/elements')

    expect(screen.getByLabelText('Vue au')).toHaveValue('')
    expect(screen.queryByRole('button', { name: /aujourd/i })).toBeNull()
  })

  it('writes the chosen day into the URL without adding a history entry', async () => {
    const router = await open('/elements?element=abc')

    await fireEvent.update(screen.getByLabelText('Vue au'), '2028-01-01')

    await waitFor(() => expect(router.currentRoute.value.query.asof).toBe('2028-01-01'))
    expect(router.currentRoute.value.query.element).toBe('abc')
  })

  it('reads the day from the URL and clears it on request', async () => {
    const router = await open('/elements?asof=2028-01-01')
    expect(screen.getByLabelText('Vue au')).toHaveValue('2028-01-01')

    await fireEvent.click(screen.getByRole('button', { name: /aujourd/i }))

    await waitFor(() => expect(router.currentRoute.value.query.asof).toBeUndefined())
  })
})

describe('the shell banner', () => {
  it('reminds that the catalogue is filtered while a day is chosen', async () => {
    await open('/elements?asof=2028-01-01', App)

    expect(
      await screen.findByText('Vue au 2028-01-01 — le catalogue est filtré à cette date.'),
    ).toBeInTheDocument()
  })

  it('says nothing when no day is chosen', async () => {
    await open('/elements', App)

    expect(screen.queryByText(/le catalogue est filtré/i)).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/AsOfPicker.spec.ts` → import introuvable.

- [ ] **Step 3: Implémenter.** `frontend/src/components/AsOfPicker.vue` :

```vue
<script setup lang="ts">
// "And in 2028?" is a question every view has, so the date is a control of the
// shell and not of a screen. A native date input: no picker library.
import { useAsOf } from '../lib/asof'

const { asOf, setAsOf } = useAsOf()

function onChange(event: Event): void {
  setAsOf((event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="asof">
    <label for="asof-day">Vue au</label>
    <input id="asof-day" type="date" :value="asOf ?? ''" @change="onChange" />
    <button v-if="asOf" type="button" class="asof__today" @click="setAsOf('')">
      Aujourd'hui
    </button>
  </div>
</template>

<style scoped>
.asof {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
.asof input {
  font: inherit;
  padding: 0.25rem 0.45rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
.asof__today {
  font: inherit;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
}
</style>
```

`App.vue` : script — ajouter `import AsOfPicker from './components/AsOfPicker.vue'` et `import { useAsOf } from './lib/asof'` puis `const { asOf } = useAsOf()` ; template — dans `.app__status`, avant `<BackendStatus />` : `<AsOfPicker />` ; dans `<main class="app__main">`, avant `<RouterView />` :

```vue
        <p v-if="asOf" class="app__asof" role="status">
          Vue au {{ asOf }} — le catalogue est filtré à cette date.
        </p>
```
style scoped :

```css
.app__asof {
  margin: 0 0 1rem;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 0.9rem;
}
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/AsOfPicker.spec.ts tests/App.spec.ts` ; lint et typecheck.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AsOfPicker.vue frontend/src/App.vue frontend/tests/AsOfPicker.spec.ts
git commit -m "feat(spa): global as-of date picker and banner" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 18: Le catalogue, le voisinage et l'impact lisent `?asof=`

**Files:**
- Modify: `frontend/src/features/elements/useElementCatalogue.ts` (signature l. 27, `load` l. 36-57)
- Modify: `frontend/src/features/elements/ElementCatalogue.vue` (script l. 24-30)
- Modify: `frontend/src/features/neighbourhood/useNeighbourhood.ts` (`Question` l. 34-38, requête l. 56-63)
- Modify: `frontend/src/features/neighbourhood/NeighbourhoodSection.vue` (`load` + `watch`, l. 66-77)
- Modify: `frontend/src/features/impact/useImpact.ts` (`Question`, requête)
- Modify: `frontend/src/features/impact/ImpactSection.vue` (`load` + `watch`, l. 95-109)
- Test: `frontend/tests/useNeighbourhood.spec.ts`, `frontend/tests/useImpact.spec.ts`, `frontend/tests/useElementCatalogue.spec.ts`, `frontend/tests/NeighbourhoodSection.spec.ts` (ajouts)

**Interfaces:**
- Consumes: `useAsOf`.
- Produces (TypeScript) :
  - `useElementCatalogue(options?: { asOf?: () => string | undefined })` — l'API inchangée par ailleurs
  - `Question.asOf?: string` dans `useNeighbourhood` et `useImpact` → paramètre de requête `as_of`

- [ ] **Step 1: Tests qui échouent.**

Dans `tests/useNeighbourhood.spec.ts` :

```ts
  it('reads the graph on the day it is given, and on none by default', async () => {
    const calls = stubApi([ROUTE])
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 1 })
    expect(calls.at(-1)?.url.searchParams.has('as_of')).toBe(false)

    await neighbourhood.explore(SUBJECT.id, { depth: 1, asOf: '2028-01-01' })
    expect(calls.at(-1)?.url.searchParams.get('as_of')).toBe('2028-01-01')
  })
```
Dans `tests/useImpact.spec.ts` (réutiliser ses helpers `stubApi` / sujets ; même forme, appel `impact.analyse(<id>, { depth: 5, asOf: '2028-01-01' })` et assertions sur `searchParams`). Dans `tests/useElementCatalogue.spec.ts` :

```ts
  it('asks for the catalogue on the day the shell chose', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([]) }])
    let day: string | undefined
    const catalogue = useElementCatalogue({ asOf: () => day })

    await catalogue.load()
    expect(calls.at(-1)?.url.searchParams.has('as_of')).toBe(false)

    day = '2028-01-01'
    await catalogue.load()
    expect(calls.at(-1)?.url.searchParams.get('as_of')).toBe('2028-01-01')
  })
```
Dans `tests/NeighbourhoodSection.spec.ts` :

```ts
  it('asks the traversal for the day named in the URL, and asks again when it changes', async () => {
    const calls = stubApi(ROUTES)
    const router = await open(`?element=${SUBJECT.id}&asof=2028-01-01`)
    await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)
    expect(calls.filter((c) => c.url.pathname.endsWith('/neighbourhood')).at(-1)?.url.searchParams.get('as_of')).toBe('2028-01-01')

    await router.replace({ query: { element: SUBJECT.id, asof: '2029-02-02' } })

    await waitFor(() =>
      expect(calls.filter((c) => c.url.pathname.endsWith('/neighbourhood')).at(-1)?.url.searchParams.get('as_of')).toBe('2029-02-02'),
    )
  })
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/useNeighbourhood.spec.ts tests/useElementCatalogue.spec.ts tests/NeighbourhoodSection.spec.ts` → `searchParams.get('as_of')` est `null`.

- [ ] **Step 3: Implémenter.**

`useElementCatalogue.ts` : signature `export function useElementCatalogue(options: { asOf?: () => string | undefined } = {})` ; dans `load`, ajouter dans l'objet `query` : `...(options.asOf?.() ? { as_of: options.asOf() } : {}),` (lue au moment de construire la requête, comme les filtres).

`ElementCatalogue.vue` : `import { useAsOf } from '../../lib/asof'` ; `const { asOf } = useAsOf()` ; `const catalogue = useElementCatalogue({ asOf: () => asOf.value })` ; et, après le `onMounted`, `watch(asOf, () => catalogue.search())`.

`useNeighbourhood.ts` : `Question` gagne `asOf?: string` ; la requête : `query: { depth: question.depth, ...(question.relationshipType ? {...} : {}), ...(question.asOf ? { as_of: question.asOf } : {}) }`.
`NeighbourhoodSection.vue` : `import { useAsOf } from '../../lib/asof'` ; `const { asOf } = useAsOf()` ; `load` passe `asOf: asOf.value` ; `watch([focusId, depth, relationshipType, asOf], load, { immediate: true })`.
`useImpact.ts` / `ImpactSection.vue` : mêmes modifications (`analyse(..., { depth, relationshipType, asOf })`, `watch([subjectId, depth, relationshipType, asOf], load, ...)`).

Les pickers d'éléments de ces deux sections gardent `useElementCatalogue()` **sans** `asOf` : on choisit parmi tous les éléments.

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run` (toute la suite) ; `npm run lint && npm run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/elements frontend/src/features/neighbourhood frontend/src/features/impact frontend/tests/useNeighbourhood.spec.ts frontend/tests/useImpact.spec.ts frontend/tests/useElementCatalogue.spec.ts frontend/tests/NeighbourhoodSection.spec.ts
git commit -m "feat(spa): catalogue, neighbourhood and impact read the as-of day" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 19: Les diagrammes grisent les boîtes hors période

**Files:**
- Modify: `frontend/src/features/diagrams/useDiagram.ts` (état l. 38-48, `open` l. 105-124, `close` l. 126-134, retour l. 185-205)
- Modify: `frontend/src/features/diagrams/DiagramCanvas.vue` (props l. 25-33, `<g class="canvas__node">` l. 291-296, styles l. 452-470)
- Modify: `frontend/src/features/diagrams/DiagramsSection.vue` (watch l. 61-76, `<DiagramCanvas>` l. 247)
- Test: `frontend/tests/useDiagram.spec.ts`, `frontend/tests/DiagramCanvas.spec.ts` (ajouts)

**Interfaces:**
- Consumes: `DiagramRead.inactive_element_ids`, `inactive_relationship_ids` (client régénéré).
- Produces (TypeScript) :
  - `useDiagram().open(id: string, asOf?: string): Promise<void>` ; `useDiagram().inactiveIds: Ref<string[]>` (éléments)
  - `DiagramCanvas` prop `inactiveIds?: string[]` ; classe `canvas__node--inactive` + `aria-label` complété par « — hors période »

- [ ] **Step 1: Tests qui échouent.** `tests/useDiagram.spec.ts` :

```ts
  it('asks the diagram on a day, and keeps every box while flagging the ones out of period', async () => {
    const calls = stubApi([
      {
        path: `/diagrams/${DIAGRAM_ID}`,
        body: {
          id: DIAGRAM_ID, name: 'Vente', description: '', created_at: '2026-09-07T12:00:00Z', updated_at: '2026-09-07T12:00:00Z',
          nodes: [
            { element_id: A.id, x: 0, y: 0, width: 132, height: 46 },
            { element_id: B.id, x: 200, y: 0, width: 132, height: 46 },
          ],
          elements: [A, B], relationships: [],
          inactive_element_ids: [B.id], inactive_relationship_ids: [],
        },
      },
    ])
    const editor = useDiagram()

    await editor.open(DIAGRAM_ID, '2028-01-01')

    expect(calls.at(-1)?.url.searchParams.get('as_of')).toBe('2028-01-01')
    expect(editor.boxes.value).toHaveLength(2)
    expect(editor.inactiveIds.value).toEqual([B.id])
  })
```
(utiliser les constantes `A`, `B`, `DIAGRAM_ID` déjà définies dans ce fichier de spec, ou les y déclarer.) `tests/DiagramCanvas.spec.ts` :

```ts
  it('greys the boxes whose element is out of period without removing them', () => {
    draw({ inactiveIds: [B.id] })

    const grey = screen.getByRole('button', { name: /Commandes.*hors période/ })
    expect(grey).toHaveClass('canvas__node--inactive')
    expect(screen.getByRole('button', { name: /^Facturation/ })).not.toHaveClass('canvas__node--inactive')
  })
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/useDiagram.spec.ts tests/DiagramCanvas.spec.ts` → `inactiveIds` indéfini / bouton introuvable.

- [ ] **Step 3: Implémenter.**

`useDiagram.ts` : état `const inactiveIds = ref<string[]>([])` ; `async function open(id: string, asOf?: string)` : la requête devient

```ts
        unwrap(
          await api.GET('/diagrams/{diagram_id}', {
            params: { path: { diagram_id: id }, query: asOf ? { as_of: asOf } : {} },
            signal,
          }),
        ),
```
et dans le callback `apply` : `inactiveIds.value = answer.inactive_element_ids` ; `close()` remet `inactiveIds.value = []` ; `inactiveIds` est ajouté à l'objet retourné. La sauvegarde de la mise en page (`persist`/`saveNow`) lit `nodes`, non filtrées : rien d'autre à changer.

`DiagramCanvas.vue` : prop `inactiveIds?: string[]` ; `const inactive = computed(() => new Set(props.inactiveIds ?? []))` ; sur le `<g class="canvas__node">` : `:class="{ 'canvas__node--selected': ..., 'canvas__node--inactive': inactive.has(box.element.id) }"` et `:aria-label="`${box.element.name} — ${typeLabel(box.element.element_type)}${inactive.has(box.element.id) ? ' — hors période' : ''}`"` ; style : `.canvas__node--inactive { opacity: 0.35; }`.

`DiagramsSection.vue` : `import { useAsOf } from '../../lib/asof'` ; `const { asOf } = useAsOf()` ; le watcher de `diagramId` devient `watch([diagramId, asOf], async ([id]) => { … editor.open(id, asOf.value) … })` (la logique `link.value = null`, `editor.close()`, `list.load()` reste pour `!id`) ; `<DiagramCanvas … :inactive-ids="editor.inactiveIds.value" …>`.

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run` ; lint ; typecheck.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/diagrams frontend/tests/useDiagram.spec.ts frontend/tests/DiagramCanvas.spec.ts
git commit -m "feat(spa): diagrams grey out boxes that are out of period" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 20: Champs de date du formulaire d'élément

**Files:**
- Modify: `frontend/src/features/elements/ElementForm.vue` (script l. 33-92 ; template, avant le `<fieldset class="properties">`)
- Test: `frontend/tests/ElementForm.spec.ts` (ajouts)

**Interfaces:**
- Consumes: rien de neuf.
- Produces : deux `<input type="date">` « En service à partir du » (`#element-valid-from`) et « Jusqu'au (inclus) » (`#element-valid-until`) ; ils sont **retirés des lignes d'attributs libres** et fusionnés dans `properties` à l'envoi s'ils sont non vides.

- [ ] **Step 1: Tests qui échouent** — ajouter à `frontend/tests/ElementForm.spec.ts` :

```ts
  it('sends the two dates among the properties, and nothing when they are empty', async () => {
    const rendered = renderForm()
    await fill(/nom/i, 'Facturation')

    await fireEvent.submit(screen.getByRole('form'))
    expect(rendered.emitted().submit[0]).toEqual([
      { element_type: 'application_component', name: 'Facturation', description: '', documentation: '', properties: {} },
    ])

    await fill(/en service à partir du/i, '2024-03-01')
    await fill(/jusqu'au/i, '2027-06-30')
    await fireEvent.submit(screen.getByRole('form'))
    expect(rendered.emitted().submit[1]).toEqual([
      {
        element_type: 'application_component', name: 'Facturation', description: '', documentation: '',
        properties: { valid_from: '2024-03-01', valid_until: '2027-06-30' },
      },
    ])
  })

  it('keeps the dates of the element being edited when it is saved without touching them', async () => {
    const element = anElement({
      properties: { owner: 'finance', valid_from: '2024-03-01', valid_until: '2027-06-30' },
    })
    const rendered = renderForm({ element })

    expect(screen.getByLabelText(/en service à partir du/i)).toHaveValue('2024-03-01')
    expect(screen.queryByLabelText("Clé de l'attribut 2")).toBeNull() // dates are not free rows
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit[0]).toEqual([
      {
        name: element.name, description: '', documentation: '',
        properties: { owner: 'finance', valid_from: '2024-03-01', valid_until: '2027-06-30' },
      },
    ])
  })

  it('removes a date that was emptied rather than sending a blank', async () => {
    const element = anElement({ properties: { valid_until: '2027-06-30' } })
    const rendered = renderForm({ element })

    await fill(/jusqu'au/i, '')
    await fireEvent.submit(screen.getByRole('form'))

    expect((rendered.emitted().submit[0] as unknown[])[0]).toMatchObject({ properties: {} })
  })
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/ElementForm.spec.ts` → `Unable to find a label with the text of: /en service à partir du/i`.

- [ ] **Step 3: Implémenter** — `ElementForm.vue` :

Script : constantes et refs

```ts
// The validity dates (docs/adr/0042) are properties like any other for the API,
// but they get their own date inputs here: a `PATCH` replaces the whole map, so
// a form that dropped them on save would silently erase them.
const VALID_FROM = 'valid_from'
const VALID_UNTIL = 'valid_until'

const validFrom = ref(props.element?.properties?.[VALID_FROM] ?? '')
const validUntil = ref(props.element?.properties?.[VALID_UNTIL] ?? '')
```
`rowsOf` ignore les deux clés :

```ts
function rowsOf(element?: ElementRead | null): PropertyRow[] {
  return Object.entries(element?.properties ?? {})
    .filter(([key]) => key !== VALID_FROM && key !== VALID_UNTIL)
    .map(([key, value]) => ({ key, value }))
}
```
Le `watch` de `props.element` remet aussi `validFrom.value = element?.properties?.[VALID_FROM] ?? ''` et `validUntil.value = …`. Dans `onSubmit`, après `const collected = collectProperties()` et le test `null` :

```ts
  if (validFrom.value) {
    collected[VALID_FROM] = validFrom.value
  }
  if (validUntil.value) {
    collected[VALID_UNTIL] = validUntil.value
  }
```
Template, juste avant `<fieldset class="properties">` :

```vue
    <div class="field">
      <label for="element-valid-from">En service à partir du</label>
      <input id="element-valid-from" v-model="validFrom" type="date" />
    </div>

    <div class="field">
      <label for="element-valid-until">Jusqu'au (inclus)</label>
      <input id="element-valid-until" v-model="validUntil" type="date" />
      <p class="hint">Laisser vide : sans date. L'API reste juge du format.</p>
    </div>
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/ElementForm.spec.ts tests/ElementCatalogue.spec.ts` ; lint ; typecheck. Les tests existants de l'`ElementForm` (payload sans dates) restent verts.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/elements/ElementForm.vue frontend/tests/ElementForm.spec.ts
git commit -m "feat(spa): date inputs for valid_from and valid_until in the element form" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 21: Composable `useGap` et panneau « Comparer deux plateaux »

**Files:**
- Create: `frontend/src/features/roadmap/useGap.ts`
- Create: `frontend/src/features/roadmap/GapPanel.vue`
- Create: `frontend/src/features/roadmap/RoadmapSection.vue` (squelette : le panneau seul, la frise vient au lot 5)
- Modify: `frontend/src/router/sections.ts` (entrée, groupe `analysis`, après « Analyse d’impact »)
- Modify: `frontend/tests/support/api.ts` (helper `aPlateau`)
- Test: `frontend/tests/useGap.spec.ts`, `frontend/tests/GapPanel.spec.ts`

**Interfaces:**
- Consumes: `/plateaus`, `/plateaus/gap` (client régénéré), `useMe().canWrite`, `useLatestRequest`.
- Produces (TypeScript) :
  - `type PlateauRead`, `type PlateauGapRead`, `type ElementRead` (alias de `components['schemas'][…]`)
  - `useGap(): { plateaus: Ref<PlateauRead[]>; comparison: Ref<PlateauGapRead | null>; status; error; loadPlateaus(): Promise<void>; compare(a: string, b: string): Promise<void>; clear(): void; createGap(a: string, b: string): Promise<void>; createError: Ref<string>; creating: Ref<boolean> }`
  - `GapPanel.vue` : lit `?a=` / `?b=`, deux `<select>` « Plateau de départ » / « Plateau d'arrivée », trois listes « Sorties », « Arrivées », « Communs », gaps existants, bouton « Créer l'écart » (éditeur et aucun gap existant)
  - `aPlateau(overrides?: Partial<PlateauRead>): PlateauRead` (support de tests)
  - section : `{ path: '/analyse/feuille-de-route', name: 'roadmap', label: 'Feuille de route', summary: "Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.", group: 'analysis', view: () => import('../features/roadmap/RoadmapSection.vue') }`

- [ ] **Step 1: Tests qui échouent.** Ajouter à `frontend/tests/support/api.ts` :

```ts
type PlateauRead = components['schemas']['PlateauRead']

/** A plateau as `/plateaus` returns it. */
export function aPlateau(overrides: Partial<PlateauRead> = {}): PlateauRead {
  return {
    id: 'cccccccc-1111-4111-8111-111111111111',
    name: 'As-Is',
    valid_from: '2024-01-01',
    valid_until: null,
    element_count: 0,
    ...overrides,
  }
}
```

`frontend/tests/useGap.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useGap } from '../src/features/roadmap/useGap'
import { anElement, aPlateau, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const AS_IS = aPlateau({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'As-Is' })
const TO_BE = aPlateau({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'To-Be' })
const OLD = anElement({ id: '11111111-aaaa-4aaa-8aaa-111111111111', name: 'Legacy' })
const GAP = anElement({ id: '22222222-aaaa-4aaa-8aaa-222222222222', name: 'Écart As-Is → To-Be', element_type: 'gap', layer: 'implementation_migration', aspect: 'passive_structure' })

const COMPARISON = {
  a: anElement({ id: AS_IS.id, name: 'As-Is', element_type: 'plateau' }),
  b: anElement({ id: TO_BE.id, name: 'To-Be', element_type: 'plateau' }),
  only_in_a: [OLD], only_in_b: [], common: [], gaps: [],
}

describe('useGap', () => {
  it('lists the plateaus', async () => {
    stubApi([{ path: '/plateaus', body: [AS_IS, TO_BE] }])
    const gap = useGap()

    await gap.loadPlateaus()

    expect(gap.plateaus.value.map((p) => p.name)).toEqual(['As-Is', 'To-Be'])
  })

  it('compares two plateaus by their ids', async () => {
    const calls = stubApi([{ path: '/plateaus/gap', body: COMPARISON }])
    const gap = useGap()

    await gap.compare(AS_IS.id, TO_BE.id)

    expect(calls.at(-1)?.url.searchParams.get('a')).toBe(AS_IS.id)
    expect(calls.at(-1)?.url.searchParams.get('b')).toBe(TO_BE.id)
    expect(gap.comparison.value?.only_in_a[0].name).toBe('Legacy')
  })

  it('reports a refused comparison in words and shows no stale comparison', async () => {
    stubApi([{ path: '/plateaus/gap', body: COMPARISON }])
    const gap = useGap()
    await gap.compare(AS_IS.id, TO_BE.id)

    stubApi([{ path: '/plateaus/gap', status: 422, body: { error: 'invalid_input', detail: 'a plateau cannot be compared with itself' } }])
    await gap.compare(AS_IS.id, AS_IS.id)

    expect(gap.status.value).toBe('error')
    expect(gap.error.value).toBe('a plateau cannot be compared with itself')
    expect(gap.comparison.value).toBeNull()
  })

  it('shows the comparison asked last, whichever answer arrives first', async () => {
    const calls = deferApi()
    const gap = useGap()

    const first = gap.compare(AS_IS.id, TO_BE.id)
    const second = gap.compare(TO_BE.id, AS_IS.id)
    calls[1].answer({ ...COMPARISON, only_in_a: [], only_in_b: [OLD] })
    calls[0].answer(COMPARISON)
    await Promise.all([first, second])

    expect(gap.comparison.value?.only_in_b[0].name).toBe('Legacy')
  })

  it('creates the gap and then reads the comparison again', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/plateaus/gap', status: 201, body: GAP },
      { path: '/plateaus/gap', body: { ...COMPARISON, gaps: [GAP] } },
    ])
    const gap = useGap()

    await gap.createGap(AS_IS.id, TO_BE.id)

    expect(calls[0].method).toBe('POST')
    expect(calls[0].body).toEqual({ a: AS_IS.id, b: TO_BE.id })
    expect(gap.comparison.value?.gaps).toHaveLength(1)
    expect(gap.createError.value).toBe('')
  })

  it("keeps the API's refusal of a duplicate gap", async () => {
    stubApi([{ method: 'POST', path: '/plateaus/gap', status: 409, body: { error: 'duplicate_gap', detail: "'Écart' already links 'As-Is' and 'To-Be'" } }])
    const gap = useGap()

    await gap.createGap(AS_IS.id, TO_BE.id)

    expect(gap.createError.value).toContain('already links')
  })
})
```

`frontend/tests/GapPanel.spec.ts` :

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import GapPanel from '../src/features/roadmap/GapPanel.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { anElement, aPlateau, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const AS_IS = aPlateau({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'As-Is' })
const TO_BE = aPlateau({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'To-Be', valid_from: '2028-01-01' })
const LEAVING = anElement({ id: '11111111-aaaa-4aaa-8aaa-111111111111', name: 'Legacy' })
const ARRIVING = anElement({ id: '22222222-aaaa-4aaa-8aaa-222222222222', name: 'Nouveau' })
const KEPT = anElement({ id: '33333333-aaaa-4aaa-8aaa-333333333333', name: 'Socle' })

const PLATEAUS: Route = { path: '/plateaus', body: [AS_IS, TO_BE] }
const COMPARISON: Route = {
  path: '/plateaus/gap',
  body: {
    a: anElement({ id: AS_IS.id, name: 'As-Is', element_type: 'plateau' }),
    b: anElement({ id: TO_BE.id, name: 'To-Be', element_type: 'plateau' }),
    only_in_a: [LEAVING], only_in_b: [ARRIVING], common: [KEPT], gaps: [],
  },
}

async function open(query = '', routes: Route[] = [PLATEAUS, COMPARISON]) {
  stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/feuille-de-route${query}`)
  await router.isReady()
  render(GapPanel, { global: { plugins: [router] } })
  return router
}

describe('GapPanel', () => {
  it('asks for two plateaus before comparing anything', async () => {
    await open()

    expect(await screen.findByText(/choisis deux plateaux/i)).toBeInTheDocument()
  })

  it('keeps the two plateaus in the URL', async () => {
    const router = await open()
    await screen.findByRole('option', { name: 'As-Is' })

    await fireEvent.update(screen.getByLabelText('Plateau de départ'), AS_IS.id)
    await fireEvent.update(screen.getByLabelText("Plateau d'arrivée"), TO_BE.id)

    await waitFor(() => {
      expect(router.currentRoute.value.query).toMatchObject({ a: AS_IS.id, b: TO_BE.id })
    })
  })

  it('lists what leaves, what arrives and what stays', async () => {
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)

    expect(await screen.findByRole('heading', { name: /sorties/i })).toBeInTheDocument()
    expect(screen.getByText('Legacy')).toBeInTheDocument()
    expect(screen.getByText('Nouveau')).toBeInTheDocument()
    expect(screen.getByText('Socle')).toBeInTheDocument()
  })

  it('offers an editor to create the gap', async () => {
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)

    expect(await screen.findByRole('button', { name: "Créer l'écart" })).toBeInTheDocument()
  })

  it('hides the button from a reader', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      ...useMe(),
      canWrite: { value: false } as never,
    })
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)
    await screen.findByRole('heading', { name: /sorties/i })

    expect(screen.queryByRole('button', { name: "Créer l'écart" })).toBeNull()
  })

  it('does not offer a second gap when one already links the pair', async () => {
    const withGap: Route = {
      path: '/plateaus/gap',
      body: {
        ...(COMPARISON.body as object),
        gaps: [anElement({ id: '44444444-aaaa-4aaa-8aaa-444444444444', name: 'Écart As-Is → To-Be', element_type: 'gap' })],
      },
    }
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`, [PLATEAUS, withGap])

    expect(await screen.findByText('Écart As-Is → To-Be')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Créer l'écart" })).toBeNull()
  })

  it("shows the API's refusal when the same plateau is chosen twice", async () => {
    const refused: Route = {
      path: '/plateaus/gap', status: 422,
      body: { error: 'invalid_input', detail: 'a plateau cannot be compared with itself' },
    }
    await open(`?a=${AS_IS.id}&b=${AS_IS.id}`, [PLATEAUS, refused])

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be compared with itself')
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/useGap.spec.ts tests/GapPanel.spec.ts` → imports introuvables.

- [ ] **Step 3: Implémenter.**

`frontend/src/features/roadmap/useGap.ts` :

```ts
// Two plateaus and what separates them.
//
// The difference is the server's to compute — departures, arrivals, what stays,
// and the gaps already drawn — because membership of a plateau is a graph
// question (docs/adr/0044). Here: the list to choose from, the answer for one
// pair, and the one write (draw the gap). Asking again drops the answer still
// in flight, so the screen shows the pair chosen last.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type PlateauRead = components['schemas']['PlateauRead']
export type PlateauGapRead = components['schemas']['PlateauGapRead']
export type ElementRead = components['schemas']['ElementRead']

export function useGap() {
  const plateaus = ref<PlateauRead[]>([])
  const comparison = ref<PlateauGapRead | null>(null)
  const listing = useLatestRequest()
  const comparing = useLatestRequest()
  const creating = ref(false)
  const createError = ref('')

  async function loadPlateaus(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/plateaus', { signal })),
      (answer) => {
        plateaus.value = answer
      },
    )
  }

  async function compare(a: string, b: string): Promise<void> {
    await comparing.run(
      async (signal) =>
        unwrap(await api.GET('/plateaus/gap', { params: { query: { a, b } }, signal })),
      (answer) => {
        comparison.value = answer
      },
      () => {
        comparison.value = null
      },
    )
  }

  function clear(): void {
    comparing.cancel()
    comparison.value = null
  }

  async function createGap(a: string, b: string): Promise<void> {
    creating.value = true
    createError.value = ''
    try {
      unwrap(await api.POST('/plateaus/gap', { body: { a, b } }))
      await compare(a, b)
    } catch (caught) {
      createError.value = messageOf(caught)
    } finally {
      creating.value = false
    }
  }

  return {
    plateaus,
    comparison,
    status: comparing.status,
    error: comparing.error,
    loadPlateaus,
    compare,
    clear,
    createGap,
    creating,
    createError,
  }
}
```

`frontend/src/features/roadmap/GapPanel.vue` :

```vue
<script setup lang="ts">
// "Comparer deux plateaux": the two plateaus live in the URL (`?a=`, `?b=`), so
// a comparison is a link to send and the back button walks it back. The server
// computes the difference; this panel only reads it out.
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useMe } from '../../lib/me'
import type { ElementRead } from './useGap'
import { useGap } from './useGap'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const gap = useGap()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const a = computed(() => one(route.query.a))
const b = computed(() => one(route.query.b))

onMounted(() => gap.loadPlateaus())

watch(
  [a, b],
  async ([from, to]) => {
    if (from && to) {
      await gap.compare(from, to)
    } else {
      gap.clear()
    }
  },
  { immediate: true },
)

function choose(which: 'a' | 'b', event: Event): void {
  const chosen = (event.target as HTMLSelectElement).value
  void router.replace({ query: { ...route.query, [which]: chosen || undefined } })
}

const existing = computed(() => gap.comparison.value?.gaps ?? [])

const groups = computed<{ id: string; title: string; items: ElementRead[] }[]>(() => {
  const found = gap.comparison.value
  return found
    ? [
        { id: 'out', title: 'Sorties', items: found.only_in_a },
        { id: 'in', title: 'Arrivées', items: found.only_in_b },
        { id: 'same', title: 'Communs', items: found.common },
      ]
    : []
})
</script>

<template>
  <section class="gap" aria-labelledby="gap-title">
    <header>
      <h3 id="gap-title">Comparer deux plateaux</h3>
      <p class="hint">Ce qui sort, ce qui arrive et ce qui reste entre deux états du SI.</p>
    </header>

    <div class="controls">
      <div class="field">
        <label for="gap-a">Plateau de départ</label>
        <select id="gap-a" :value="a" @change="choose('a', $event)">
          <option value="">Choisis un plateau…</option>
          <option v-for="plateau in gap.plateaus.value" :key="plateau.id" :value="plateau.id">
            {{ plateau.name }}
          </option>
        </select>
      </div>
      <div class="field">
        <label for="gap-b">Plateau d'arrivée</label>
        <select id="gap-b" :value="b" @change="choose('b', $event)">
          <option value="">Choisis un plateau…</option>
          <option v-for="plateau in gap.plateaus.value" :key="plateau.id" :value="plateau.id">
            {{ plateau.name }}
          </option>
        </select>
      </div>
    </div>

    <p v-if="!a || !b" class="hint">Choisis deux plateaux pour voir ce qui les sépare.</p>
    <p v-else-if="gap.status.value === 'error'" class="banner banner--error" role="alert">
      {{ gap.error.value }}
    </p>
    <p v-else-if="gap.status.value === 'loading'" class="hint">Comparaison en cours…</p>

    <template v-else-if="gap.comparison.value">
      <div class="columns">
        <section v-for="group in groups" :key="group.id" :aria-labelledby="`gap-${group.id}`">
          <h4 :id="`gap-${group.id}`">{{ group.title }} ({{ group.items.length }})</h4>
          <ul v-if="group.items.length">
            <li v-for="item in group.items" :key="item.id">{{ item.name }}</li>
          </ul>
          <p v-else class="hint">Aucun élément.</p>
        </section>
      </div>

      <section v-if="existing.length" aria-labelledby="gap-existing">
        <h4 id="gap-existing">Écarts déjà décrits</h4>
        <ul>
          <li v-for="item in existing" :key="item.id">{{ item.name }}</li>
        </ul>
      </section>
      <div v-else-if="canWrite" class="actions">
        <button type="button" :disabled="gap.creating.value" @click="gap.createGap(a, b)">
          Créer l'écart
        </button>
        <p v-if="gap.createError.value" class="banner banner--error" role="alert">
          {{ gap.createError.value }}
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.gap {
  display: grid;
  gap: 0.9rem;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
.field {
  display: grid;
  gap: 0.3rem;
}
select {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1rem;
}
h3,
h4 {
  margin: 0 0 0.3rem;
}
.hint {
  margin: 0;
  font-size: 0.9rem;
  opacity: 0.75;
}
.banner--error {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid currentColor;
  border-radius: 8px;
}
</style>
```

`frontend/src/features/roadmap/RoadmapSection.vue` (squelette, le lot 5 y ajoute la frise) :

```vue
<script setup lang="ts">
// The `/analyse/feuille-de-route` section. For now the comparison of two
// plateaus; the roadmap itself is drawn above it from the next lot.
import GapPanel from './GapPanel.vue'
</script>

<template>
  <section class="roadmap">
    <header>
      <h2>Feuille de route</h2>
      <p class="hint">Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.</p>
    </header>
    <GapPanel />
  </section>
</template>

<style scoped>
.roadmap {
  display: grid;
  gap: 1.5rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
</style>
```

`sections.ts` — entrée après « Analyse d’impact » :

```ts
  {
    path: '/analyse/feuille-de-route',
    name: 'roadmap',
    label: 'Feuille de route',
    summary: "Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.",
    group: 'analysis',
    view: () => import('../features/roadmap/RoadmapSection.vue'),
  },
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/useGap.spec.ts tests/GapPanel.spec.ts tests/router.spec.ts tests/AppNav.spec.ts` ; lint ; typecheck. Pour le test « lecteur », si le mock global de `useMe` (voir `tests/setup.ts`) s'override autrement dans les specs existantes, copier la forme qu'utilise `tests/ElementCatalogue.spec.ts` pour son cas lecteur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/roadmap frontend/src/router/sections.ts frontend/tests/support/api.ts frontend/tests/useGap.spec.ts frontend/tests/GapPanel.spec.ts
git commit -m "feat(spa): compare two plateaus and draw the gap" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 22: Vérification du lot 4

**Files:** aucun nouveau (vérification).

- [ ] **Step 1: Régénération et cohérence du client** — `make openapi-check` (le client TS doit déjà contenir `as_of`, `PlateauGapRead`, `inactive_*` depuis les Tasks 10 et 15).
- [ ] **Step 2: Suite complète** — `make check` ; attendu : lint, types, openapi-check, tests backend (≥ 90 %), Vitest, `pipelines-check` verts.
- [ ] **Step 3: Parcours manuel** — `make run`, ouvrir `http://localhost:5173` : poser une date dans « Vue au », vérifier qu'elle suit un changement de section, qu'un élément `valid_until` passé disparaît du catalogue, qu'un diagramme grise sa boîte, que « Feuille de route » compare deux plateaux. (À défaut de données : créer deux plateaux, y composer des éléments, dater l'un d'eux.)
- [ ] **Step 4: Rien à committer** si tout est vert ; sinon corriger dans la tâche concernée.

---

# Lot 5 — SPA : la frise « Feuille de route »

### Task 23: `lib/roadmapLayout.ts` (géométrie pure)

**Files:**
- Create: `frontend/src/lib/roadmapLayout.ts`
- Test: `frontend/tests/roadmapLayout.spec.ts`

**Interfaces:**
- Consumes: `components['schemas']['RoadmapItemRead']`.
- Produces (TypeScript) :
  - `type RoadmapItemRead`, `type DayWindow = { from: string; to: string }`
  - `LABEL_WIDTH = 190`, `ROW_HEIGHT = 30`, `AXIS_HEIGHT = 28`
  - `dayNumber(iso: string): number`
  - `type RoadmapMark = { item: RoadmapItemRead; kind: 'bar' | 'milestone'; row: number; x: number; width: number; y: number; clippedStart: boolean; clippedEnd: boolean }`
  - `type Tick = { x: number; label: string }`
  - `type RoadmapLayout = { marks: RoadmapMark[]; undated: RoadmapItemRead[]; ticks: Tick[]; today: number | null; width: number; height: number }`
  - `layoutRoadmap(items: RoadmapItemRead[], window: DayWindow, width: number, asOf?: string): RoadmapLayout`

- [ ] **Step 1: Test qui échoue** — `frontend/tests/roadmapLayout.spec.ts` :

```ts
import { describe, expect, it } from 'vitest'

import {
  AXIS_HEIGHT,
  LABEL_WIDTH,
  ROW_HEIGHT,
  dayNumber,
  layoutRoadmap,
  type RoadmapItemRead,
} from '../src/lib/roadmapLayout'

function item(overrides: Partial<RoadmapItemRead> = {}): RoadmapItemRead {
  return {
    id: 'id-' + (overrides.name ?? 'x'),
    name: 'x',
    element_type: 'work_package',
    valid_from: null,
    valid_until: null,
    plateau_ids: [],
    ...overrides,
  }
}

// A ten-day window drawn on a 1000-unit plot: one day is exactly 100 units.
const WINDOW = { from: '2026-01-01', to: '2026-01-10' }
const WIDTH = LABEL_WIDTH + 1000 + 16

describe('layoutRoadmap', () => {
  it('counts days without drifting across months and leap years', () => {
    expect(dayNumber('2028-03-01') - dayNumber('2028-02-28')).toBe(2)
    expect(dayNumber('2027-03-01') - dayNumber('2027-02-28')).toBe(1)
  })

  it('puts a bar where its first and last day are, the last day included', () => {
    const layout = layoutRoadmap(
      [item({ name: 'Phase', valid_from: '2026-01-03', valid_until: '2026-01-04' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks).toHaveLength(1)
    expect(layout.marks[0]).toMatchObject({
      kind: 'bar',
      row: 0,
      x: LABEL_WIDTH + 200,
      width: 200,
      y: AXIS_HEIGHT,
      clippedStart: false,
      clippedEnd: false,
    })
  })

  it('cuts a bar that leaves the window and says so', () => {
    const layout = layoutRoadmap(
      [item({ name: 'Long', valid_from: '2025-12-01', valid_until: '2026-03-01' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks[0]).toMatchObject({ x: LABEL_WIDTH, width: 1000, clippedStart: true, clippedEnd: true })
  })

  it('draws an open end up to the edge of the window', () => {
    const layout = layoutRoadmap([item({ name: 'Open', valid_from: '2026-01-06' })], WINDOW, WIDTH)

    expect(layout.marks[0]).toMatchObject({ x: LABEL_WIDTH + 500, width: 500, clippedEnd: true })
  })

  it('leaves out what lies wholly outside the window', () => {
    const layout = layoutRoadmap(
      [
        item({ name: 'Before', valid_until: '2025-12-31' }),
        item({ name: 'After', valid_from: '2026-02-01' }),
      ],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks).toEqual([])
    expect(layout.undated).toEqual([])
  })

  it('draws a plateau as a milestone on the day it is reached', () => {
    const layout = layoutRoadmap(
      [item({ name: 'To-Be', element_type: 'plateau', valid_from: '2026-01-06', valid_until: '2026-12-31' })],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks[0]).toMatchObject({ kind: 'milestone', x: LABEL_WIDTH + 500, width: 0 })
  })

  it('lists an undated element apart instead of hiding it', () => {
    const undated = item({ name: 'Runbook', element_type: 'deliverable' })

    const layout = layoutRoadmap([undated, item({ name: 'Dated', valid_from: '2026-01-02' })], WINDOW, WIDTH)

    expect(layout.undated).toEqual([undated])
    expect(layout.marks.map((mark) => mark.item.name)).toEqual(['Dated'])
  })

  it('stacks plateaus first, then work packages, then deliverables, each by start then name', () => {
    const layout = layoutRoadmap(
      [
        item({ name: 'Livrable', element_type: 'deliverable', valid_from: '2026-01-01' }),
        item({ name: 'B', valid_from: '2026-01-05' }),
        item({ name: 'A', valid_from: '2026-01-05' }),
        item({ name: 'Early', valid_from: '2026-01-02' }),
        item({ name: 'To-Be', element_type: 'plateau', valid_from: '2026-01-09' }),
      ],
      WINDOW,
      WIDTH,
    )

    expect(layout.marks.map((mark) => [mark.item.name, mark.row])).toEqual([
      ['To-Be', 0],
      ['Early', 1],
      ['A', 2],
      ['B', 3],
      ['Livrable', 4],
    ])
    expect(layout.marks[4].y).toBe(AXIS_HEIGHT + 4 * ROW_HEIGHT)
    expect(layout.height).toBe(AXIS_HEIGHT + 5 * ROW_HEIGHT)
  })

  it('marks the as-of day with a vertical line, only when it is in the window', () => {
    expect(layoutRoadmap([], WINDOW, WIDTH, '2026-01-06').today).toBe(LABEL_WIDTH + 500)
    expect(layoutRoadmap([], WINDOW, WIDTH, '2027-01-01').today).toBeNull()
    expect(layoutRoadmap([], WINDOW, WIDTH).today).toBeNull()
  })

  it('graduates the axis by quarter on a short window and by year on a long one', () => {
    const short = layoutRoadmap([], { from: '2026-01-01', to: '2027-06-30' }, WIDTH)
    const long = layoutRoadmap([], { from: '2024-01-01', to: '2030-12-31' }, WIDTH)

    expect(short.ticks.map((tick) => tick.label)).toEqual(['2026 T1', '2026 T2', '2026 T3', '2026 T4', '2027 T1', '2027 T2'])
    expect(long.ticks.map((tick) => tick.label)).toEqual(['2024', '2025', '2026', '2027', '2028', '2029', '2030'])
    expect(short.ticks[0].x).toBe(LABEL_WIDTH)
  })

  it('is deterministic: the same input draws the same way', () => {
    const items = [
      item({ name: 'A', valid_from: '2026-01-02', valid_until: '2026-01-05' }),
      item({ name: 'B', element_type: 'plateau', valid_from: '2026-01-08' }),
    ]

    expect(layoutRoadmap(items, WINDOW, WIDTH, '2026-01-04')).toEqual(
      layoutRoadmap([...items].reverse(), WINDOW, WIDTH, '2026-01-04'),
    )
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/roadmapLayout.spec.ts` → import introuvable.

- [ ] **Step 3: Implémenter** — `frontend/src/lib/roadmapLayout.ts` :

```ts
// Where a roadmap goes on the canvas.
//
// Pure geometry, like `graphLayout.ts`: dates in, coordinates out, nothing
// animated, nothing random, so a test can assert an abscissa and the same
// roadmap always draws the same way. A day is a unit of the x axis: the window
// `[from, to]` (both days included) is stretched over the plot, and a bar runs
// from the start of its first day to the end of its last.
import type { components } from '../api/schema'

export type RoadmapItemRead = components['schemas']['RoadmapItemRead']

/** The days drawn, both included, as YYYY-MM-DD. */
export type DayWindow = { from: string; to: string }

/** Room for the names, on the left of the plot. */
export const LABEL_WIDTH = 190
export const ROW_HEIGHT = 30
export const AXIS_HEIGHT = 28
const PLOT_MARGIN = 16
const DAY_MS = 86_400_000

export type RoadmapMark = {
  item: RoadmapItemRead
  /** A work package or a deliverable is a bar; a plateau is a diamond on the day it is reached. */
  kind: 'bar' | 'milestone'
  row: number
  x: number
  /** 0 for a milestone. */
  width: number
  /** The top of its row. */
  y: number
  /** The bar goes on past the edge of the window, or has no start at all. */
  clippedStart: boolean
  clippedEnd: boolean
}

export type Tick = { x: number; label: string }

export type RoadmapLayout = {
  marks: RoadmapMark[]
  /** Items with no date at all: listed apart, never hidden. */
  undated: RoadmapItemRead[]
  ticks: Tick[]
  /** The x of the as-of day, or null when none is asked or it is off the window. */
  today: number | null
  width: number
  height: number
}

const KIND_ORDER: Record<string, number> = { plateau: 0, work_package: 1, deliverable: 2 }

/** The number of whole days since 1970-01-01 (UTC, so a clock change moves nothing). */
export function dayNumber(iso: string): number {
  return Math.round(Date.parse(`${iso}T00:00:00Z`) / DAY_MS)
}

function startOf(item: RoadmapItemRead): number | null {
  return item.valid_from ? dayNumber(item.valid_from) : null
}

function byRow(left: RoadmapItemRead, right: RoadmapItemRead): number {
  const kind = (KIND_ORDER[left.element_type] ?? 3) - (KIND_ORDER[right.element_type] ?? 3)
  if (kind !== 0) {
    return kind
  }
  const a = startOf(left)
  const b = startOf(right)
  if (a !== b) {
    return a === null ? 1 : b === null ? -1 : a - b
  }
  return left.name.localeCompare(right.name) || left.id.localeCompare(right.id)
}

function ticksOf(from: number, to: number, scale: (day: number) => number): Tick[] {
  const years = (to - from) / 365 > 3
  const first = new Date(from * DAY_MS).getUTCFullYear()
  const last = new Date((to - 1) * DAY_MS).getUTCFullYear()
  const ticks: Tick[] = []
  for (let year = first; year <= last; year += 1) {
    for (const month of years ? [0] : [0, 3, 6, 9]) {
      const day = Date.UTC(year, month, 1) / DAY_MS
      if (day >= from && day < to) {
        ticks.push({ x: scale(day), label: years ? `${year}` : `${year} T${month / 3 + 1}` })
      }
    }
  }
  return ticks
}

export function layoutRoadmap(
  items: RoadmapItemRead[],
  window: DayWindow,
  width: number,
  asOf?: string,
): RoadmapLayout {
  const from = dayNumber(window.from)
  // Exclusive upper bound: the last day of the window is drawn in full.
  const to = dayNumber(window.to) + 1
  const span = Math.max(1, to - from)
  const plot = Math.max(1, width - LABEL_WIDTH - PLOT_MARGIN)
  const scale = (day: number) => LABEL_WIDTH + ((Math.min(Math.max(day, from), to) - from) / span) * plot

  const marks: RoadmapMark[] = []
  const undated: RoadmapItemRead[] = []

  for (const item of [...items].sort(byRow)) {
    const start = startOf(item)
    // `valid_until` is inclusive: the bar ends where the next day begins.
    const end = item.valid_until ? dayNumber(item.valid_until) + 1 : null
    if (start === null && end === null) {
      undated.push(item)
      continue
    }
    // The row is taken only once the item is known to be drawn, so rows stay consecutive.
    const row = marks.length
    const y = AXIS_HEIGHT + row * ROW_HEIGHT
    if (item.element_type === 'plateau') {
      const day = start ?? (end as number) - 1
      if (day >= from && day < to) {
        marks.push({ item, kind: 'milestone', row, x: scale(day), width: 0, y, clippedStart: false, clippedEnd: false })
      }
      continue
    }
    const first = start ?? from
    const after = end ?? to
    if (after <= from || first >= to) {
      continue
    }
    marks.push({
      item,
      kind: 'bar',
      row,
      x: scale(first),
      width: Math.max(2, scale(after) - scale(first)),
      y,
      clippedStart: start === null || start < from,
      clippedEnd: end === null || end > to,
    })
  }

  const asOfDay = asOf ? dayNumber(asOf) : null
  return {
    marks,
    undated,
    ticks: ticksOf(from, to, scale),
    today: asOfDay !== null && asOfDay >= from && asOfDay < to ? scale(asOfDay) : null,
    width,
    height: AXIS_HEIGHT + Math.max(1, marks.length) * ROW_HEIGHT,
  }
}
```
Note : `row = marks.length` est lu avant le `push` mais un `continue` n'incrémente rien, donc les rangs restent consécutifs ; le test « stacks » attend `height = AXIS_HEIGHT + 5 * ROW_HEIGHT` pour 5 marques.

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/roadmapLayout.spec.ts` ; lint ; typecheck. Valeurs de contrôle pour les ticks : fenêtre 2026-01-01 → 2027-06-30 (≈ 1,5 an) donne six trimestres ; 2024 → 2030 (> 3 ans) donne sept années.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/roadmapLayout.ts frontend/tests/roadmapLayout.spec.ts
git commit -m "feat(spa): pure roadmap layout" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 24: `useRoadmap` — la lecture de la frise

**Files:**
- Create: `frontend/src/features/roadmap/useRoadmap.ts`
- Modify: `frontend/tests/support/api.ts` (helper `aRoadmapItem`)
- Test: `frontend/tests/useRoadmap.spec.ts`

**Interfaces:**
- Consumes: `GET /roadmap` (client régénéré), `useLatestRequest`.
- Produces (TypeScript) :
  - `type RoadmapRead`, `type RoadmapItemRead`
  - `useRoadmap(): { roadmap: Ref<RoadmapRead | null>; window: ComputedRef<DayWindow | null>; status; error; load(query?: { from?: string; to?: string }): Promise<void> }` — `window` est `null` quand le serveur n'a trouvé aucune date
  - `aRoadmapItem(overrides?: Partial<RoadmapItemRead>): RoadmapItemRead`

- [ ] **Step 1: Test qui échoue.** `frontend/tests/support/api.ts` :

```ts
type RoadmapItemRead = components['schemas']['RoadmapItemRead']

/** A roadmap item as `/roadmap` returns it: an undated work package unless told otherwise. */
export function aRoadmapItem(overrides: Partial<RoadmapItemRead> = {}): RoadmapItemRead {
  return {
    id: 'dddddddd-1111-4111-8111-111111111111',
    name: 'Migration',
    element_type: 'work_package',
    valid_from: null,
    valid_until: null,
    plateau_ids: [],
    ...overrides,
  }
}
```
`frontend/tests/useRoadmap.spec.ts` :

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useRoadmap } from '../src/features/roadmap/useRoadmap'
import { aRoadmapItem, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const BODY = {
  items: [aRoadmapItem({ valid_from: '2026-06-01', valid_until: '2027-12-31' })],
  edges: [],
  window_from: '2026-06-01',
  window_to: '2027-12-31',
}

describe('useRoadmap', () => {
  it('asks for the window it is given, and for none by default', async () => {
    const calls = stubApi([{ path: '/roadmap', body: BODY }])
    const roadmap = useRoadmap()

    await roadmap.load()
    expect(calls.at(-1)?.url.searchParams.has('from')).toBe(false)

    await roadmap.load({ from: '2026-01-01', to: '2026-12-31' })
    expect(calls.at(-1)?.url.searchParams.get('from')).toBe('2026-01-01')
    expect(calls.at(-1)?.url.searchParams.get('to')).toBe('2026-12-31')
  })

  it('exposes the window the server resolved', async () => {
    stubApi([{ path: '/roadmap', body: BODY }])
    const roadmap = useRoadmap()

    await roadmap.load()

    expect(roadmap.window.value).toEqual({ from: '2026-06-01', to: '2027-12-31' })
  })

  it('has no window when nothing is dated', async () => {
    stubApi([{ path: '/roadmap', body: { items: [aRoadmapItem()], edges: [], window_from: null, window_to: null } }])
    const roadmap = useRoadmap()

    await roadmap.load()

    expect(roadmap.window.value).toBeNull()
    expect(roadmap.roadmap.value?.items).toHaveLength(1)
  })

  it("says why the API refused a window", async () => {
    stubApi([{ path: '/roadmap', status: 422, body: { error: 'invalid_input', detail: 'the roadmap window is wider than 20 years' } }])
    const roadmap = useRoadmap()

    await roadmap.load({ from: '2000-01-01', to: '2030-01-01' })

    expect(roadmap.status.value).toBe('error')
    expect(roadmap.error.value).toContain('20 years')
    expect(roadmap.roadmap.value).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/useRoadmap.spec.ts` → import introuvable.

- [ ] **Step 3: Implémenter** — `frontend/src/features/roadmap/useRoadmap.ts` :

```ts
// The roadmap as the server answers it: plateaus, work packages and
// deliverables with their lifespans, and the window they span. Which elements
// overlap the window, and which window the data itself implies, are the
// server's to decide; the drawing is `lib/roadmapLayout.ts`'s.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { DayWindow } from '../../lib/roadmapLayout'

export type RoadmapRead = components['schemas']['RoadmapRead']
export type { RoadmapItemRead } from '../../lib/roadmapLayout'

export function useRoadmap() {
  const roadmap = ref<RoadmapRead | null>(null)
  const request = useLatestRequest()

  const window = computed<DayWindow | null>(() => {
    const found = roadmap.value
    return found?.window_from && found.window_to
      ? { from: found.window_from, to: found.window_to }
      : null
  })

  async function load(query: { from?: string; to?: string } = {}): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/roadmap', {
            params: {
              query: {
                ...(query.from ? { from: query.from } : {}),
                ...(query.to ? { to: query.to } : {}),
              },
            },
            signal,
          }),
        ),
      (answer) => {
        roadmap.value = answer
      },
      () => {
        roadmap.value = null
      },
    )
  }

  return { roadmap, window, status: request.status, error: request.error, load }
}
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/useRoadmap.spec.ts` ; lint ; typecheck.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/roadmap/useRoadmap.ts frontend/tests/support/api.ts frontend/tests/useRoadmap.spec.ts
git commit -m "feat(spa): roadmap composable" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 25: `RoadmapChart.vue` et intégration dans la section

**Files:**
- Create: `frontend/src/components/RoadmapChart.vue`
- Modify: `frontend/src/features/roadmap/RoadmapSection.vue`
- Test: `frontend/tests/RoadmapChart.spec.ts`, `frontend/tests/RoadmapSection.spec.ts`

**Interfaces:**
- Consumes: `layoutRoadmap` (Task 23), `useRoadmap` (Task 24), `useAsOf`, `LAYER_COLOURS`, `BOX_TEXT` (`features/metamodel/useMetamodel`).
- Produces :
  - `RoadmapChart` props `{ items: RoadmapItemRead[]; window: DayWindow; asOf?: string }`, émet `open: [id: string]` ; chaque barre / jalon est un `<g role="button" tabindex="0" aria-label="<nom> — du <début ou …> au <fin ou …>">` ; bande « Non daté » listant les éléments sans date, chaque entrée est un `<button>`
  - `RoadmapSection` : charge `/roadmap`, dessine la frise puis le panneau d'écart ; un clic ouvre `{ name: 'elements', query: { element: id } }`

- [ ] **Step 1: Tests qui échouent.** `frontend/tests/RoadmapChart.spec.ts` :

```ts
import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import RoadmapChart from '../src/components/RoadmapChart.vue'
import { aRoadmapItem } from './support/api'

const WINDOW = { from: '2026-01-01', to: '2026-12-31' }
const PHASE = aRoadmapItem({ id: 'p1', name: 'Phase 1', valid_from: '2026-02-01', valid_until: '2026-05-31' })
const TO_BE = aRoadmapItem({ id: 'p2', name: 'To-Be', element_type: 'plateau', valid_from: '2026-09-01' })
const RUNBOOK = aRoadmapItem({ id: 'p3', name: 'Runbook', element_type: 'deliverable' })

function draw(props: Record<string, unknown> = {}) {
  return render(RoadmapChart, { props: { items: [PHASE, TO_BE, RUNBOOK], window: WINDOW, ...props } })
}

describe('RoadmapChart', () => {
  it('draws a bar per dated work package and a diamond per plateau, each a focusable control', () => {
    draw()

    const bar = screen.getByRole('button', { name: 'Phase 1 — du 2026-02-01 au 2026-05-31' })
    const milestone = screen.getByRole('button', { name: 'To-Be — du 2026-09-01 au …' })
    expect(bar).toHaveAttribute('tabindex', '0')
    expect(milestone).toHaveAttribute('tabindex', '0')
    expect(document.querySelector('polygon')).not.toBeNull()
  })

  it('opens the detail of an element on click and on Enter', async () => {
    const { emitted } = draw()

    await fireEvent.click(screen.getByRole('button', { name: /Phase 1/ }))
    await fireEvent.keyDown(screen.getByRole('button', { name: /To-Be/ }), { key: 'Enter' })

    expect(emitted().open).toEqual([['p1'], ['p2']])
  })

  it('lists an undated element in its own band instead of hiding it', async () => {
    const { emitted } = draw()

    const band = screen.getByRole('region', { name: /non daté/i })
    await fireEvent.click(screen.getByRole('button', { name: 'Runbook' }))

    expect(band).toHaveTextContent('Runbook')
    expect(emitted().open).toEqual([['p3']])
  })

  it('draws the as-of day as a vertical line', () => {
    draw({ asOf: '2026-06-01' })

    expect(screen.getByTestId('asof-line')).toBeInTheDocument()
  })

  it('draws no line when no day is asked', () => {
    draw()

    expect(screen.queryByTestId('asof-line')).toBeNull()
  })
})
```
`frontend/tests/RoadmapSection.spec.ts` :

```ts
import { render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import RoadmapSection from '../src/features/roadmap/RoadmapSection.vue'
import { createAppRouter } from '../src/router'
import { aPlateau, aRoadmapItem, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const PHASE = aRoadmapItem({ id: 'eeeeeeee-1111-4111-8111-111111111111', name: 'Phase 1', valid_from: '2026-02-01', valid_until: '2026-05-31' })

async function open(body: unknown, query = '') {
  stubApi([
    { path: '/roadmap', body },
    { path: '/plateaus', body: [aPlateau()] },
  ])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/feuille-de-route${query}`)
  await router.isReady()
  render(RoadmapSection, { global: { plugins: [router] } })
  return router
}

describe('RoadmapSection', () => {
  it('draws the roadmap and keeps the comparison below it', async () => {
    await open({ items: [PHASE], edges: [], window_from: '2026-02-01', window_to: '2026-05-31' })

    expect(await screen.findByRole('button', { name: /Phase 1/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /comparer deux plateaux/i })).toBeInTheDocument()
  })

  it('says so when nothing carries a date', async () => {
    await open({ items: [aRoadmapItem({ name: 'Runbook', element_type: 'deliverable' })], edges: [], window_from: null, window_to: null })

    expect(await screen.findByText(/aucune date saisie/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Runbook' })).toBeInTheDocument()
  })

  it('opens the element detail in the catalogue when a bar is chosen', async () => {
    const router = await open({ items: [PHASE], edges: [], window_from: '2026-02-01', window_to: '2026-05-31' })

    ;(await screen.findByRole('button', { name: /Phase 1/ })).dispatchEvent(new MouseEvent('click', { bubbles: true }))

    await waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/elements')
      expect(router.currentRoute.value.query.element).toBe(PHASE.id)
    })
  })

  it('shows the as-of line when the URL names a day inside the window', async () => {
    await open({ items: [PHASE], edges: [], window_from: '2026-02-01', window_to: '2026-05-31' }, '?asof=2026-03-15')

    expect(await screen.findByTestId('asof-line')).toBeInTheDocument()
  })

  it("shows the API's refusal", async () => {
    stubApi([
      { path: '/roadmap', status: 422, body: { error: 'invalid_input', detail: 'more than 1000 plateaus, work packages and deliverables' } },
      { path: '/plateaus', body: [] },
    ])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/analyse/feuille-de-route')
    await router.isReady()
    render(RoadmapSection, { global: { plugins: [router] } })

    expect(await screen.findByRole('alert')).toHaveTextContent('more than 1000')
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** — `cd frontend && npm test -- --run tests/RoadmapChart.spec.ts tests/RoadmapSection.spec.ts` → composant introuvable.

- [ ] **Step 3: Implémenter.**

`frontend/src/components/RoadmapChart.vue` :

```vue
<script setup lang="ts">
// The roadmap, drawn by hand in SVG over `lib/roadmapLayout.ts`: one row per
// plateau, work package and deliverable, diamonds for the day a plateau is
// reached, bars for the rest, a vertical line for the as-of day. Every mark is
// a real focusable control that opens the element; whatever carries no date is
// listed below rather than dropped.
import { computed } from 'vue'

import { BOX_TEXT, LAYER_COLOURS } from '../features/metamodel/useMetamodel'
import {
  AXIS_HEIGHT,
  ROW_HEIGHT,
  layoutRoadmap,
  type DayWindow,
  type RoadmapItemRead,
  type RoadmapMark,
} from '../lib/roadmapLayout'

const WIDTH = 960
const DIAMOND = 9

const props = defineProps<{ items: RoadmapItemRead[]; window: DayWindow; asOf?: string }>()
const emit = defineEmits<{ open: [id: string] }>()

const drawing = computed(() => layoutRoadmap(props.items, props.window, WIDTH, props.asOf))

function name(mark: RoadmapMark): string {
  const { valid_from: from, valid_until: until } = mark.item
  return `${mark.item.name} — du ${from ?? '…'} au ${until ?? '…'}`
}

function diamond(mark: RoadmapMark): string {
  const cy = mark.y + ROW_HEIGHT / 2
  return [
    [mark.x, cy - DIAMOND],
    [mark.x + DIAMOND, cy],
    [mark.x, cy + DIAMOND],
    [mark.x - DIAMOND, cy],
  ]
    .map(([x, y]) => `${x},${y}`)
    .join(' ')
}
</script>

<template>
  <figure class="roadmap-chart">
    <svg
      :viewBox="`0 0 ${drawing.width} ${drawing.height}`"
      :width="drawing.width"
      :height="drawing.height"
      role="group"
      aria-label="Feuille de route"
    >
      <g class="roadmap-chart__axis">
        <g v-for="tick in drawing.ticks" :key="tick.label">
          <line :x1="tick.x" :x2="tick.x" :y1="AXIS_HEIGHT - 6" :y2="drawing.height" />
          <text :x="tick.x + 3" :y="AXIS_HEIGHT - 10">{{ tick.label }}</text>
        </g>
      </g>

      <line
        v-if="drawing.today !== null"
        data-testid="asof-line"
        class="roadmap-chart__asof"
        :x1="drawing.today"
        :x2="drawing.today"
        y1="0"
        :y2="drawing.height"
      />

      <g
        v-for="mark in drawing.marks"
        :key="mark.item.id"
        class="roadmap-chart__mark"
        role="button"
        tabindex="0"
        :aria-label="name(mark)"
        @click="emit('open', mark.item.id)"
        @keydown.enter.prevent="emit('open', mark.item.id)"
      >
        <title>{{ name(mark) }}</title>
        <text :x="6" :y="mark.y + ROW_HEIGHT / 2 + 4" class="roadmap-chart__label">
          {{ mark.item.name }}
        </text>
        <polygon
          v-if="mark.kind === 'milestone'"
          :points="diamond(mark)"
          :fill="LAYER_COLOURS.implementation_migration"
        />
        <rect
          v-else
          :x="mark.x"
          :y="mark.y + 5"
          :width="mark.width"
          :height="ROW_HEIGHT - 10"
          rx="4"
          :fill="LAYER_COLOURS.implementation_migration"
          :stroke-dasharray="mark.clippedStart || mark.clippedEnd ? '4 2' : undefined"
        />
        <text
          v-if="mark.kind === 'bar' && mark.width > 70"
          :x="mark.x + 6"
          :y="mark.y + ROW_HEIGHT / 2 + 4"
          class="roadmap-chart__inside"
          :fill="BOX_TEXT"
        >
          {{ mark.item.name }}
        </text>
      </g>
    </svg>

    <section v-if="drawing.undated.length" aria-labelledby="roadmap-undated" class="roadmap-chart__undated">
      <h3 id="roadmap-undated">Non daté</h3>
      <ul>
        <li v-for="item in drawing.undated" :key="item.id">
          <button type="button" @click="emit('open', item.id)">{{ item.name }}</button>
        </li>
      </ul>
    </section>
  </figure>
</template>

<style scoped>
.roadmap-chart {
  margin: 0;
  overflow-x: auto;
}
svg {
  max-width: 100%;
  height: auto;
}
.roadmap-chart__axis line {
  stroke: var(--border);
}
.roadmap-chart__axis text,
.roadmap-chart__label {
  font-size: 11px;
  fill: currentColor;
}
.roadmap-chart__asof {
  stroke: currentColor;
  stroke-width: 2;
  stroke-dasharray: 6 3;
}
.roadmap-chart__mark {
  cursor: pointer;
}
.roadmap-chart__mark rect,
.roadmap-chart__mark polygon {
  stroke: var(--border);
}
.roadmap-chart__mark:focus-visible rect,
.roadmap-chart__mark:hover rect,
.roadmap-chart__mark:focus-visible polygon,
.roadmap-chart__mark:hover polygon {
  stroke: currentColor;
  stroke-width: 2;
}
.roadmap-chart__inside {
  font-size: 11px;
  pointer-events: none;
}
.roadmap-chart__undated h3 {
  margin: 1rem 0 0.4rem;
  font-size: 1rem;
}
.roadmap-chart__undated ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
}
.roadmap-chart__undated button {
  font: inherit;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
</style>
```
Le test « region name /non daté/ » s'appuie sur `aria-labelledby` de la `<section>`.

`RoadmapSection.vue` — remplacer le squelette :

```vue
<script setup lang="ts">
// The `/analyse/feuille-de-route` section: the roadmap on top, the comparison of
// two plateaus below. The day read on (`?asof=`) is the shell's; here it only
// becomes a vertical line, because a roadmap shows every date at once — it does
// not filter by it.
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import RoadmapChart from '../../components/RoadmapChart.vue'
import { useAsOf } from '../../lib/asof'
import GapPanel from './GapPanel.vue'
import { useRoadmap } from './useRoadmap'

const router = useRouter()
const { asOf } = useAsOf()
const roadmap = useRoadmap()

onMounted(() => roadmap.load())

function open(id: string): void {
  void router.push({ name: 'elements', query: { element: id } })
}
</script>

<template>
  <section class="roadmap">
    <header>
      <h2>Feuille de route</h2>
      <p class="hint">Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.</p>
    </header>

    <p v-if="roadmap.status.value === 'error'" class="banner banner--error" role="alert">
      {{ roadmap.error.value }}
    </p>
    <p v-else-if="roadmap.status.value === 'loading'" class="hint">Chargement de la feuille de route…</p>

    <template v-else-if="roadmap.roadmap.value">
      <RoadmapChart
        v-if="roadmap.window.value"
        :items="roadmap.roadmap.value.items"
        :window="roadmap.window.value"
        :as-of="asOf"
        @open="open"
      />
      <template v-else>
        <p class="hint">
          Aucune date saisie : donne une date de début (<code>valid_from</code>) ou de fin
          (<code>valid_until</code>) à un plateau, un work package ou un livrable pour le voir ici.
        </p>
        <RoadmapChart
          :items="roadmap.roadmap.value.items"
          :window="{ from: '2000-01-01', to: '2000-01-01' }"
          @open="open"
        />
      </template>
    </template>

    <GapPanel />
  </section>
</template>

<style scoped>
.roadmap {
  display: grid;
  gap: 1.5rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner--error {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid currentColor;
  border-radius: 8px;
}
</style>
```

- [ ] **Step 4: Relancer, PASS** — `cd frontend && npm test -- --run tests/RoadmapChart.spec.ts tests/RoadmapSection.spec.ts tests/GapPanel.spec.ts` ; puis `npm test -- --run`, `npm run lint`, `npm run typecheck`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/RoadmapChart.vue frontend/src/features/roadmap/RoadmapSection.vue frontend/tests/RoadmapChart.spec.ts frontend/tests/RoadmapSection.spec.ts
git commit -m "feat(spa): roadmap chart with undated band and as-of line" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 26: Clôture — `CLAUDE.md`, section, vérification finale

**Files:**
- Modify: `CLAUDE.md` (section « Project status », avant le paragraphe `**Not yet scaffolded**` ; nombre d'outils MCP)
- Vérifier : `frontend/src/router/sections.ts` (entrée « Feuille de route », ajoutée en Task 21), `docs/adr/0042`–`0044`

**Interfaces:** aucune.

- [ ] **Step 1: Une phrase factuelle dans `CLAUDE.md`** — insérer, juste avant `**Not yet scaffolded**` :

```
Elements and relationships carry a validity time in their properties (`valid_from` / `valid_until`, `YYYY-MM-DD`, both inclusive, validated in `domain/timeline.py` on every write): `?as_of=` filters the lists and the two traversals (a neighbour out of service cuts the walk), `/plateaus`, `/plateaus/gap` and `/roadmap` compute the difference between two plateaus and the dated roadmap, the MCP adds `list_plateaus` and `gap_between`, and the SPA's *Feuille de route* section draws it under a global `?asof=` date (see `docs/adr/0042`, `0043`, `0044`).
```
et, dans le même fichier, corriger « thirty-four tools » en « thirty-six tools » (`rg "thirty-four" CLAUDE.md`).

- [ ] **Step 2: Vérifier l'entrée de section** — `rg "feuille-de-route" frontend/src/router/sections.ts` (une occurrence, groupe `analysis`, `view` présent) ; `cd frontend && npm test -- --run tests/router.spec.ts tests/AppNav.spec.ts`.

- [ ] **Step 3: Vérification finale** — `make openapi-check` ; `make check` ; `make test-integration` ; `make audit` (réseau). Tous verts. Parcours manuel de la Task 22 sur la feuille de route (une barre, un jalon, la ligne `asof`, la bande « Non daté »).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record validity time, as_of and the roadmap in CLAUDE.md" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revue

**Couverture de la spec.** §3 domaine : `Lifespan`/`read`/`covers`/`overlaps`, `validate_lifespan_properties` (T1), `Roadmap`/`RoadmapItem` (T11), `PlateauGap`/`diff_plateaus` (T11), appartenance calculée (T12 `_members_of`). §4 données : index d'expression ×4 + contrôle des données, réversible (T3). §5 API : `as_of` sur les cinq lectures (T5-T7), quatre endpoints (T13), dates par `PATCH` existant (T2). §6 MCP : `list_plateaus`, `gap_between`, `as_of` ×3, `INSTRUCTIONS`, assertion de liste (T8, T14). §7 SPA : sélecteur + bandeau + garde (T16-T17), lecture `asof` (T18), diagrammes grisés (T19), champs de date (T20), panneau d'écart `?a=` `?b=` + `useLatestRequest` + bouton éditeur (T21), frise `layoutRoadmap` + `RoadmapChart` + bande « non daté » + ligne `asof` (T23-T25). §8 sécurité : gardes AST (`create_gap` dans `WRITES`, T12), `as_of` typé et lié (T5), fenêtre 20 ans (T11/T13), lecteur 403 (T13). §9 tests : unitaires, intégration (index `EXPLAIN`, up/down peuplé, `as_of` ×3), API 401/403/200/422/409, Vitest ; E2E Playwright absent : le scénario est couvert par `RoadmapSection.spec.ts` (composant). §10 ADR : trois, rédigés en français (T4, T10, T15). §12 lots : cinq lots, chacun finissant par une tâche de vérification (T4, T10, T15, T22, T26). Cas demandés : dates invalides / `valid_until < valid_from` (T1, T2), sans date toujours visible (T5, T7, T9), extrémité invisible (T9), `as_of` absent inchangé (T5, T6, T7, T9).

**Placeholders.** Aucun « TBD »/« TODO ». Les noms de fichiers d'ADR liés (`0020-…`, `0031-…`, `0033-…`, `0010-…`) sont à vérifier par `ls docs/adr` (Tasks 4, 10, 15), car seul leur numéro est connu ; c'est une vérification d'exécution, pas un trou de contenu.

**Cohérence des noms.** `Lifespan`, `validate_lifespan_properties`, `InvalidLifespanError` (T1) repris par T2, T5, T7, T11 ; `visible_at` (T5) repris par T6 ; `ElementFilter.as_of` (T5) repris par T7 ; `AsOf` (T7) repris par T8 et T13 ; `PlateauSummary`, `PlateauComparison`, `PlateauGap`, `Roadmap*` (T11) repris par T12-T14 ; `DuplicateGapError` (T12) repris par T13/T14 ; côté TypeScript `parseAsOf`/`useAsOf` (T16), `layoutRoadmap`/`DayWindow`/`RoadmapItemRead` (T23), `useGap`/`useRoadmap` (T21/T24), `aPlateau`/`aRoadmapItem` (T21/T24), `inactiveIds` (T19), `AsOfPicker` (T17), `RoadmapChart` (T25). Les noms de champs JSON (`only_in_a`, `window_from`, `inactive_element_ids`, `plateau_ids`, `element_count`) sont identiques dans les schémas Pydantic (T7, T13), les tests e2e et les specs Vitest.

**Points à surveiller à l'exécution.** (1) Les noms de fichiers d'ADR et la numérotation `0009`/`0042`-`0044`. (2) L'enveloppe `result` du SDK MCP pour un outil qui renvoie une liste (Task 14). (3) Le mock global de `useMe` pour le cas « lecteur » du `GapPanel` (Task 21). (4) La fragilité de l'assertion sur l'alias d'extrémité lointaine dans la compilation des CTE (Task 6, repli indiqué). (5) L'`EXPLAIN` n'est prouvé que pour `valid_from <= :d`, pas pour le prédicat de visibilité (écart n°5).
