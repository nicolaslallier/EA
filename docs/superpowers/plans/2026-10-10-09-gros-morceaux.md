# Gros morceaux (C3 exigences, A4 données, A3 notations, C6 simulation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer d'abord C3 (exigences et traçabilité : une couverture calculée côté serveur, un outil MCP, un écran SPA), puis cadrer par des spikes à sortie documentée A4 (ERD/DDL), A3 (BPMN, UML classes) et C6 (simulation), dont chacun aura son plan dédié une fois le sous-projet 6 livré et le chantier choisi.

**Architecture:** Un seul métamodèle ArchiMate (option 3 de la spec : profils + vues, aucun type étranger). C3 n'ajoute ni table, ni migration, ni dépendance : un `requirement`/`constraint` est un élément, son statut est une clé de `properties`, un test est un `deliverable`/`artifact` portant `test_result` et reliant l'exigence par `realization`. La couverture est une lecture pure (`domain/requirements.py`) alimentée par une requête (`traceability`) et servie par `RequirementService` → `GET /requirements/coverage` → outil MCP `get_coverage` → section SPA `/analyse/couverture`.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2 async (lecture seule, aucune migration), `mcp` SDK 2.x, Vue 3 `<script setup>` + TypeScript + Vitest/Testing Library, client OpenAPI généré (`make openapi`), `uv`, `ruff`, `mypy --strict`.

**Spec:** docs/superpowers/specs/2026-10-10-09-gros-morceaux-design.md

## Global Constraints

- Branche `feat/requirements-coverage` depuis `main` ; commits Conventional Commits en anglais, chacun terminé par `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Dépendance : `api → services → domain ← repositories` ; `mcp/` à côté de `api/`, jamais un dépôt, jamais HTTP vers soi-même ; `domain/` sans import de FastAPI/SQLAlchemy.
- Clés de propriété (regex réelle de `domain/model.py` : `^[A-Za-z][A-Za-z0-9_]{0,62}$`, donc pas de point) : `req_id`, `req_status`, `req_priority`, `req_source`, `test_result`, `test_last_run`.
- Statuts stockés : `proposed`, `approved`, `implemented`, `verified`, `rejected` ; priorités : `must`, `should`, `could` ; résultats : `not_run`, `passed`, `failed`, `blocked` ; valeur absente ou illisible : `unset`.
- Types d'exigence : `requirement`, `constraint` ; types de test : `deliverable`, `artifact` portant la clé `test_result` ; lien test/conception → exigence : `realization`.
- Une exigence au statut `rejected` ne compte ni dans les taux ni dans les écarts ; un taux sur zéro exigence active vaut `null`, jamais 0 % ni 100 %.
- Écart `design` = aucune réalisation (directe ou via une chaîne d'exigences) par un élément de conception ; écart `verification` = réalisée mais sans test ; la recherche suit les `realization` entrantes à travers les exigences, avec un ensemble de « déjà vus » (les boucles terminent).
- Route `GET /requirements/coverage?status=&priority=` ; outil MCP `get_coverage(status, priority)` annoté `READS` ; route SPA `/analyse/couverture`, nom `coverage`, groupe `analysis`.
- Borne déclarée une fois dans `api/schemas.py` : `CoverageFilter = Annotated[str, Field(min_length=1, max_length=32)]`, importée par `mcp/server.py` et ajoutée à `TestTheSameBoundsAsTheHttpAdapter`.
- `RequirementService` : première instruction de chaque méthode publique `require_caller()` ; entrée `RequirementService: set()` dans `WRITES` de `tests/unit/test_service_guards.py`.
- Aucune migration, aucune table, aucune dépendance Python/npm ; aucune écriture : la couverture est calculée à la demande, jamais stockée.
- Tests sans `--cov` en boucle courte (`cd backend && uv run pytest tests/unit/<fichier> -x`) ; frontend `cd frontend && npm test -- --run tests/<fichier>`; plancher 90 % uniquement via `make test`.
- Le SPA ne copie aucune règle : libellés (`STATUS_LABELS`…) seulement ; les options de filtre viennent des clés de `by_status`/`by_priority` de la réponse.
- ADR en français selon `docs/adr/TEMPLATE.md`, numéro = prochain libre à l'exécution (0042 au moment de la rédaction) ; aucune édition d'un ADR existant.

## Review Focus

1. **Exigence sans réalisation** : doit sortir en écart `design`, jamais être comptée couverte — `test_a_requirement_nothing_realizes_is_a_design_gap` (Task 1), `test_the_gaps_are_reported` (Task 4).
2. **Boucle de réalisation** : A réalisée par B réalisée par A, sans conception, ne boucle pas et reste en écart ; avec une conception sur la boucle, les deux sont couvertes — `test_a_realization_loop_terminates_and_stays_a_gap`, `test_a_design_reaching_a_loop_covers_every_requirement_on_it` (Task 1).
3. **Exigence sans statut** : comptée sous `unset` (valeur absente ou illisible), la somme de `by_status` égale toujours le total — `test_a_requirement_without_a_usable_status_is_counted_as_unset` (Task 1).
4. **Couverture sur graphe vide** : `total = 0`, taux `null`, listes vides, pas de division par zéro — `test_an_empty_graph_has_no_rate` (Task 1), `test_an_empty_catalogue_has_no_rate` (Task 4), `shows a dash instead of a rate when there is nothing to measure` (Task 7).
5. **Les tests ne contaminent pas l'impact** : l'analyse d'impact d'un élément de conception ou d'une exigence n'atteint jamais ses tests (la `realization` va test → exigence, le long de la flèche) — `test_impact_never_reaches_the_tests_of_a_requirement` (Task 2, PostgreSQL jetable).

---

## Écarts constatés avec la spec

La spec a été écrite sans lire `rules.py`, `model.py`, `architecture_store.py` ni les ADR 0005/0012/0013/0031. Vérifié dans le code réel de ce worktree :

1. **Les clés de propriété ne peuvent pas contenir de point.** `domain/model.py` (`_PROPERTY_KEY`) refuse `req.status`, `erd.columns`, `bpmn.kind`, `sim.duration`… Tout le plan utilise `req_status`, `erd_columns`, `bpmn_kind`, `sim_duration`. Cela vaut aussi pour la spec 06 (clés « sans préfixe » comme l'IPAM) : le préfixe `req_` est un choix de ce plan pour éviter les collisions entre stéréotypes que la spec 06 refuse à l'application.
2. **Une jonction ne peut pas être un élément.** `Element.create` lève `ValueError` pour `ElementType.JUNCTION` (`model.py:79`). La spec A3 veut des portes BPMN en `junction` et C6 simule ces portes : A3 et C6 sont bloqués tant que ce point n'est pas tranché (spike A3-S1).
3. **`access` n'est pas permis depuis un composant.** `_permits_access` exige une source de comportement (`Aspect.BEHAVIOR`) : `application_component → data_object` est refusé. La spec A4 le propose pour les clés étrangères ; le plan retient `association` (toujours permise) entre `data_object`, et `access` seulement depuis un processus/fonction/service applicatif.
4. **Une relation ne peut pas être modifiée.** `api/architecture.py` n'expose que `POST`, `GET`, `DELETE` sur `/relationships` (pas de `PATCH`), et le sous-projet 6 pose les profils sur des éléments (`element_stereotypes`), pas sur des relations. `sim_probability` (C6) et `erd_fk_columns` (A4) sur un lien supposent soit supprimer/recréer, soit un `PATCH /relationships/{id}` (changement d'API, d'outil MCP et ADR) : spike C6-S3.
5. **L'analyse d'impact ne répond pas à « que casse-t-on si je change cette exigence ».** `ALONG_THE_ARROW` fait suivre `realization` de la source vers la cible : l'impact d'une exigence est ce qu'elle réalise (buts), pas les éléments de conception qui la réalisent. Ces derniers sont dans `designed_by`/`tested_by` de la couverture (Task 1) ; l'ADR le dit. Conséquence heureuse : avec la convention test → exigence, un test n'entre jamais dans les vagues d'impact d'une exigence ou de sa conception, donc **l'exclusion par défaut des éléments `req.test` de la spec n'est pas nécessaire** ; l'intégration l'épingle (Task 2).
6. **« Une CTE réutilisant `impacted_by`/`relations_of` »** : ces deux requêtes partent d'un seul élément (N requêtes pour N exigences). Le plan fait une lecture dédiée (`traceability`) et un calcul pur, comme la spec 04 le fait pour les contrôles de qualité. Non bornée volontairement (un taux calculé sur une page serait faux), comme l'inventaire IP.
7. **Le test est détecté par la présence de `test_result`**, pas par une jointure sur `element_stereotypes` : le cœur de C3 (Tasks 1 à 7) ne lit donc que `elements.properties` et ne dépend d'aucune table du sous-projet 6 ; le profil (Task 8) apporte la validation des valeurs et le formulaire. La conséquence, assumée dans l'ADR : un test sans `test_result` n'existe pas pour la couverture (le profil le déclare obligatoire).
8. **Valeurs de statut en codes ASCII anglais** (`approved`), libellés français (« Approuvée ») dans une table de traduction du SPA, comme `LAYER_LABELS`. La spec les écrit en français ; un code stocké avec accent est une source de comparaisons fausses.
9. **Contrainte d'unicité** : la spec (et l'ADR 0028) cite `element_name_unique_per_type` ; le code réel est `uq_elements_element_type_name` (`architecture_store.NAME_TAKEN`). Conséquence pour A4 : `UNIQUE(element_type, name)` impose des noms de table qualifiés (`schema.table`).
10. **Plafond de 500 caractères de la spec 06** pour une valeur de propriété déclarée : un `erd_columns` sérialisé de 30 colonnes le dépasse. Conflit à trancher en A4-S1.
11. **Nombre d'outils MCP** : « trente-quatre » est exact aujourd'hui (34 `@server.tool`). Les sous-projets 4 et 6 en ajouteront ; le décompte de CLAUDE.md est recompté à l'exécution (Task 10).
12. `build_mcp_server` prend quatre fournisseurs positionnels (`get_service`, `get_documents`, `get_ipam`, `get_files`) ; C3 en ajoute un cinquième (`get_requirements`) et met à jour les deux fixtures de `tests/unit/test_mcp_server.py` et l'appel de `main._mount_mcp`. Si les sous-projets 4/6 ont déjà changé cette signature, Task 0 la relève et Task 5 s'y adapte.

## Dépendances (qui attend quoi)

| Chantier | Ordre | Attend | Pourquoi | Peut avancer sans |
|---|---|---|---|---|
| **C3** exigences (M) | 1 | SP6 profils (Task 8), SP4 matrice (Task 9) | Validation et formulaire des valeurs ; filtre de matrice par profil | Tasks 1 à 7 : lisent seulement `elements.properties` (écart 7) |
| **A4** données (L) | 2 | SP6 profils ; ADR « notations étrangères » ; décision sur la taille des valeurs (A4-S1) | Profil `erd`, gabarit de vue ERD | A4.1 (domaine pur) et A4-S2 (introspection) |
| **A3** BPMN (M) + UML classes (S) | 3 | SP6 (profils, conteneurs, gabarits) ; **décision jonction (A3-S1)** ; A4 pour UML classes | Portes BPMN impossibles tant que `Element.create` refuse `junction` | A3-S2 (table de légalité) |
| **C6** simulation (L) | 4 | A3 (portes, événements) ; **décision `PATCH` relation (C6-S3)** | Paramètres de probabilité sur des liens | C6.1 (moteur pur) et ses spikes C6-S1/S2 |
| Écarté | — | — | SysML, séquence, états, temps : XL non justifié | — |

Tâche de garde (Task 0) : tout C3 attend la confirmation que les sous-projets 6 et 4 sont livrés ; si ce n'est pas le cas, l'utilisateur choisit entre attendre et n'exécuter que les Tasks 1 à 7 + 10 (le profil et la matrice viennent ensuite).

## Décisions à valider par l'utilisateur (pas des tâches)

1. **Abandonner SysML et UML séquence/états/temps** (recommandation de la spec §4) : XL, aucune équivalence dans le graphe ; on référence un fichier dessiné ailleurs via le panneau Fichiers. Si refusé : il faut un plan et un ADR propres, hors de ce document.
2. **Abandonner un second métamodèle** (options 1 et 2 de la spec §2) : confirmer l'option 3 (profils + vues), qui impose l'ADR « notations étrangères : profils et vues » avant tout code A3/A4.
3. **Abandonner le parseur DDL multi-dialectes et la migration synchronisante** : l'introspection PostgreSQL est le chemin principal d'A4 ; le parseur DDL texte n'est écrit que si A4-S5 conclut qu'elle ne suffit pas.
4. **Abandonner l'animation de C6 (C6.4b)** si le tableau de goulots suffit.
5. **Jonction (A3-S1)** : autoriser `junction` comme élément (modifier `Element.create`, ADR) ou renoncer aux portes (branches multiples de `triggering` avec condition) — conditionne A3 et C6.
6. **`PATCH /relationships/{id}` (C6-S3)** : accepter ce changement d'API pour porter les probabilités, ou supprimer/recréer le lien.
7. **Cible d'introspection (A4-S3)** : cibles nommées dans la configuration serveur plutôt qu'un DSN dans la requête (risque SSRF) — la spec dit « fourni à l'appel » ; ce plan recommande de ne pas le suivre.
8. **Ordre C3 → A4 → A3 → C6** et la règle « C3 d'abord, même sans profils » (écart 7).

---

# Partie 1 — C3 Exigences et traçabilité (plan détaillé)

### Task 0: Garde — sous-projets 6 et 4 livrés ?

**Files:** aucun fichier modifié (lecture seule). Les relevés sont notés dans la description de la PR.

**Interfaces:**
- Consumes: spec 06 (`profiles`, `profile_stereotypes`, `profile_properties`, `element_stereotypes`, `domain/profiles.py` avec `validate_stereotype_properties`, `missing_required` ; routes `GET /stereotypes`, `POST /profiles/import`, `PUT /elements/{id}/stereotypes`) ; spec 04 (`POST /matrix`, sélecteurs d'axe `{package_id | query_id | element_ids}`, `domain/quality.py`, `check_model`).
- Produces: un relevé de cinq faits utilisés par les Tasks 5, 8, 9, 10.

- [ ] **Step 1: Créer la branche**

```bash
git switch main && git pull --ff-only && git switch -c feat/requirements-coverage
```

- [ ] **Step 2: Vérifier la livraison du sous-projet 6 (profils)**

```bash
ls backend/src/ea/domain/profiles.py backend/src/ea/services/profiles.py
grep -n "element_stereotypes\|profile_stereotypes" backend/migrations/versions/*.py | head
grep -n "def validate_stereotype_properties\|def missing_required" backend/src/ea/domain/profiles.py
python3 -c "import json;d=json.load(open('backend/openapi.json'));print([p for p in d['paths'] if 'stereotype' in p or 'profile' in p])"
```

Critère : les deux fichiers existent, les deux fonctions sont trouvées, la liste de routes contient `/stereotypes`, `/profiles/import` et `/elements/{element_id}/stereotypes`.

- [ ] **Step 3: Vérifier la livraison du sous-projet 4 (matrice, contrôles)**

```bash
ls backend/src/ea/domain/matrix.py backend/src/ea/domain/quality.py backend/src/ea/services/analysis.py
python3 -c "import json;d=json.load(open('backend/openapi.json'));print([p for p in d['paths'] if p.startswith('/matrix') or p.startswith('/quality')])"
grep -n '"check_model"\|"get_relationship_matrix"' backend/tests/unit/test_mcp_server.py
```

Critère : les trois fichiers existent, `/matrix` et `/quality` sont dans le schéma, les deux outils sont dans l'assertion de liste.

- [ ] **Step 4: Relever les cinq faits d'intégration**

```bash
grep -n "^def build_mcp_server" -A8 backend/src/ea/mcp/server.py        # (a) signature courante
grep -n "^def create_app" -A14 backend/src/ea/main.py                   # (b) paramètres d'injection
grep -n "^WRITES" -A12 backend/tests/unit/test_service_guards.py        # (c) services gardés
ls docs/adr | tail -3                                                    # (d) prochain numéro d'ADR
grep -c "@server.tool" backend/src/ea/mcp/server.py                      # (e) nombre d'outils MCP
```

- [ ] **Step 5: Décider**

Si l'un des critères des Steps 2 et 3 échoue : **arrêter** et rapporter à l'utilisateur, avec le choix « attendre » ou « exécuter les Tasks 1 à 7 puis 10 seulement » (écart 7 : elles ne dépendent pas des tables du sous-projet 6). Ne rien committer.

---

### Task 1: Domaine pur — vocabulaire et calcul de couverture

**Files:**
- Create: `backend/src/ea/domain/requirements.py`
- Create: `backend/tests/unit/test_requirements.py`

**Interfaces:**
- Consumes: `ea.domain.model.Element`, `Relationship` ; `ea.domain.archimate.Aspect`, `ElementType`, `RelationshipType`.
- Produces (signatures exactes) :
  - constantes `IDENTIFIER_PROPERTY`, `STATUS_PROPERTY`, `PRIORITY_PROPERTY`, `SOURCE_PROPERTY`, `RESULT_PROPERTY`, `LAST_RUN_PROPERTY`, `REQUIREMENT_TYPES`, `TEST_TYPES`, `UNSET`
  - `class Status(StrEnum)`, `class Priority(StrEnum)`, `class VerificationResult(StrEnum)`, `class Gap(StrEnum)` (`DESIGN`, `VERIFICATION`, `NONE`)
  - `is_requirement(element: Element) -> bool`, `is_test(element: Element) -> bool`
  - `RequirementTrace` (`requirement`, `status`, `priority`, `designed_by`, `tested_by`, propriétés `identifier`, `gap`)
  - `Coverage` (`traces`, propriétés `total`, `active`, `designed`, `verified`, `design_rate`, `verification_rate`, `by_status`, `by_priority`)
  - `compute_coverage(elements: Iterable[Element], relationships: Iterable[Relationship], *, status: str | None = None, priority: str | None = None) -> Coverage`

- [ ] **Step 1: Écrire le test qui échoue**

Create `backend/tests/unit/test_requirements.py`:

```python
"""The coverage of requirements, as a pure reading of a graph."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.domain.requirements import (
    RESULT_PROPERTY,
    UNSET,
    Gap,
    compute_coverage,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(element_type: E, name: str, **properties: str) -> Element:
    return Element.create(element_type=element_type, name=name, now=NOW, properties=properties)


def a_requirement(name: str = "R1", **properties: str) -> Element:
    return an_element(E.REQUIREMENT, name, **properties)


def a_design(name: str = "Billing") -> Element:
    return an_element(E.APPLICATION_COMPONENT, name)


def a_test(name: str = "T1", result: str = "passed") -> Element:
    return an_element(E.DELIVERABLE, name, **{RESULT_PROPERTY: result})


def realizes(source: Element, target: Element) -> Relationship:
    return Relationship.between(R.REALIZATION, source, target, now=NOW)


def gaps(coverage: object) -> dict[str, Gap]:
    return {trace.requirement.name: trace.gap for trace in coverage.traces}  # type: ignore[attr-defined]


class TestGaps:
    def test_a_requirement_nothing_realizes_is_a_design_gap(self) -> None:
        requirement = a_requirement()

        coverage = compute_coverage([requirement], [])

        assert gaps(coverage) == {"R1": Gap.DESIGN}
        assert coverage.design_rate == 0.0

    def test_a_designed_requirement_without_a_test_is_a_verification_gap(self) -> None:
        requirement, design = a_requirement(), a_design()

        coverage = compute_coverage([requirement, design], [realizes(design, requirement)])

        (trace,) = coverage.traces
        assert trace.gap is Gap.VERIFICATION
        assert [element.name for element in trace.designed_by] == ["Billing"]

    def test_a_designed_and_tested_requirement_has_no_gap(self) -> None:
        requirement, design, test = a_requirement(), a_design(), a_test()

        coverage = compute_coverage(
            [requirement, design, test], [realizes(design, requirement), realizes(test, requirement)]
        )

        (trace,) = coverage.traces
        assert trace.gap is Gap.NONE
        assert [element.name for element in trace.tested_by] == ["T1"]
        assert coverage.design_rate == 1.0
        assert coverage.verification_rate == 1.0

    def test_a_deliverable_without_a_result_is_design_not_a_test(self) -> None:
        requirement = a_requirement()
        deliverable = an_element(E.DELIVERABLE, "Spec technique")

        coverage = compute_coverage([requirement, deliverable], [realizes(deliverable, requirement)])

        (trace,) = coverage.traces
        assert [element.name for element in trace.designed_by] == ["Spec technique"]
        assert trace.tested_by == ()
        assert trace.gap is Gap.VERIFICATION

    def test_a_realization_from_a_goal_side_element_is_neither_design_nor_test(self) -> None:
        requirement = a_requirement()
        stakeholder = an_element(E.STAKEHOLDER, "DSI")

        coverage = compute_coverage([requirement, stakeholder], [realizes(stakeholder, requirement)])

        assert gaps(coverage) == {"R1": Gap.DESIGN}

    def test_design_and_tests_are_found_through_a_chain_of_requirements(self) -> None:
        parent, child = a_requirement("Parent"), a_requirement("Child")
        design, test = a_design(), a_test()

        coverage = compute_coverage(
            [parent, child, design, test],
            [realizes(child, parent), realizes(design, child), realizes(test, child)],
        )

        assert gaps(coverage) == {"Child": Gap.NONE, "Parent": Gap.NONE}


class TestLoops:
    def test_a_realization_loop_terminates_and_stays_a_gap(self) -> None:
        a, b = a_requirement("A"), a_requirement("B")

        coverage = compute_coverage([a, b], [realizes(a, b), realizes(b, a)])

        assert gaps(coverage) == {"A": Gap.DESIGN, "B": Gap.DESIGN}

    def test_a_design_reaching_a_loop_covers_every_requirement_on_it(self) -> None:
        a, b, design = a_requirement("A"), a_requirement("B"), a_design()

        coverage = compute_coverage(
            [a, b, design], [realizes(a, b), realizes(b, a), realizes(design, b)]
        )

        assert gaps(coverage) == {"A": Gap.VERIFICATION, "B": Gap.VERIFICATION}


class TestStatus:
    def test_a_requirement_without_a_usable_status_is_counted_as_unset(self) -> None:
        coverage = compute_coverage(
            [
                a_requirement("None"),
                a_requirement("Garbled", req_status="banana"),
                a_requirement("Fine", req_status="approved"),
            ],
            [],
        )

        assert coverage.by_status[UNSET] == 2
        assert coverage.by_status["approved"] == 1
        assert sum(coverage.by_status.values()) == coverage.total == 3

    def test_every_status_is_listed_even_when_empty(self) -> None:
        coverage = compute_coverage([a_requirement()], [])

        assert set(coverage.by_status) == {
            "proposed",
            "approved",
            "implemented",
            "verified",
            "rejected",
            UNSET,
        }

    def test_a_rejected_requirement_is_in_no_rate_and_no_gap(self) -> None:
        rejected = a_requirement("Old", req_status="rejected")
        live, design = a_requirement("Live"), a_design()

        coverage = compute_coverage(
            [rejected, live, design], [realizes(design, live)]
        )

        assert coverage.total == 2
        assert coverage.active == 1
        assert coverage.design_rate == 1.0
        assert gaps(coverage)["Old"] is Gap.NONE

    def test_the_catalogue_can_be_narrowed_by_status_and_priority(self) -> None:
        approved = a_requirement("A", req_status="approved", req_priority="must")
        proposed = a_requirement("B", req_status="proposed", req_priority="could")

        by_status = compute_coverage([approved, proposed], [], status="approved")
        by_priority = compute_coverage([approved, proposed], [], priority="could")
        unset = compute_coverage([approved, a_requirement("C")], [], status=UNSET)

        assert [t.requirement.name for t in by_status.traces] == ["A"]
        assert [t.requirement.name for t in by_priority.traces] == ["B"]
        assert [t.requirement.name for t in unset.traces] == ["C"]

    def test_an_unknown_filter_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown status 'banana'"):
            compute_coverage([], [], status="banana")
        with pytest.raises(ValueError, match="unknown priority 'urgent'"):
            compute_coverage([], [], priority="urgent")


class TestEmptyAndOrder:
    def test_an_empty_graph_has_no_rate(self) -> None:
        coverage = compute_coverage([], [])

        assert coverage.total == 0
        assert coverage.active == 0
        assert coverage.design_rate is None
        assert coverage.verification_rate is None
        assert coverage.traces == ()
        assert set(coverage.by_status.values()) == {0}

    def test_only_rejected_requirements_have_no_rate_either(self) -> None:
        coverage = compute_coverage([a_requirement(req_status="rejected")], [])

        assert coverage.active == 0
        assert coverage.design_rate is None

    def test_the_most_important_requirements_come_first(self) -> None:
        coverage = compute_coverage(
            [
                a_requirement("Z-must", req_priority="must"),
                a_requirement("A-could", req_priority="could"),
                a_requirement("M-none"),
                a_requirement("B-should", req_priority="should"),
            ],
            [],
        )

        assert [t.requirement.name for t in coverage.traces] == [
            "Z-must",
            "B-should",
            "A-could",
            "M-none",
        ]

    def test_a_constraint_is_a_requirement_too(self) -> None:
        constraint = an_element(E.CONSTRAINT, "Hébergement en France")

        coverage = compute_coverage([constraint], [])

        assert coverage.total == 1
        assert coverage.traces[0].identifier == ""
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_requirements.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.requirements'`.

- [ ] **Step 3: Implémentation minimale**

Create `backend/src/ea/domain/requirements.py`:

```python
"""Requirements, and the traceability read from the graph (docs/adr/0042).

There is no requirement table. A requirement is a `requirement` or a
`constraint` element; its status, priority, source and readable identifier are
four keys of its `properties`, and a test case is a `deliverable` or an
`artifact` that carries a `test_result` and realizes the requirement it
demonstrates. Everything below is a *reading* of that graph, computed on
demand and never stored: a stored coverage would be a second truth, stale at
the next edit.

Pure: nothing here imports a framework, and time and randomness are not used.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.archimate import Aspect, ElementType, RelationshipType
from ea.domain.model import Element, Relationship

IDENTIFIER_PROPERTY: Final = "req_id"
STATUS_PROPERTY: Final = "req_status"
PRIORITY_PROPERTY: Final = "req_priority"
SOURCE_PROPERTY: Final = "req_source"
RESULT_PROPERTY: Final = "test_result"
LAST_RUN_PROPERTY: Final = "test_last_run"

REQUIREMENT_TYPES: Final = (ElementType.REQUIREMENT, ElementType.CONSTRAINT)
TEST_TYPES: Final = (ElementType.DELIVERABLE, ElementType.ARTIFACT)

#: What a missing or unreadable value is counted as.
UNSET: Final = "unset"


class Status(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class VerificationResult(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class Gap(StrEnum):
    """What is missing around a requirement, if anything."""

    DESIGN = "design"
    VERIFICATION = "verification"
    NONE = "none"


_PRIORITY_RANK: Final = {
    Priority.MUST.value: 0,
    Priority.SHOULD.value: 1,
    Priority.COULD.value: 2,
    UNSET: 3,
}


def is_requirement(element: Element) -> bool:
    return element.element_type in REQUIREMENT_TYPES


def is_test(element: Element) -> bool:
    """A test case is a deliverable or an artifact that records a result.

    The key's *presence* is the marker, as `ip_address` is for IPAM: it needs
    no table of the profile mechanism to be read.
    """
    return element.element_type in TEST_TYPES and RESULT_PROPERTY in element.properties


def _read(properties: Mapping[str, str], key: str, vocabulary: type[StrEnum]) -> str:
    value = properties.get(key, "").strip()
    return value if value in {member.value for member in vocabulary} else UNSET


def _by_name(element: Element) -> tuple[str, str]:
    return (element.name.casefold(), str(element.id))


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else part / whole


@dataclass(frozen=True, slots=True)
class RequirementTrace:
    """One requirement, what realizes it, and what tests it."""

    requirement: Element
    status: str
    priority: str
    designed_by: tuple[Element, ...]
    tested_by: tuple[Element, ...]

    @property
    def identifier(self) -> str:
        return self.requirement.properties.get(IDENTIFIER_PROPERTY, "").strip()

    @property
    def gap(self) -> Gap:
        if self.status == Status.REJECTED.value:
            return Gap.NONE
        if not self.designed_by:
            return Gap.DESIGN
        if not self.tested_by:
            return Gap.VERIFICATION
        return Gap.NONE


@dataclass(frozen=True, slots=True)
class Coverage:
    """The traces of the requirements asked about, and the rates over them."""

    traces: tuple[RequirementTrace, ...]

    @property
    def total(self) -> int:
        return len(self.traces)

    @property
    def active_traces(self) -> tuple[RequirementTrace, ...]:
        """Rejected requirements need neither a design nor a test."""
        return tuple(t for t in self.traces if t.status != Status.REJECTED.value)

    @property
    def active(self) -> int:
        return len(self.active_traces)

    @property
    def designed(self) -> int:
        return sum(1 for t in self.active_traces if t.designed_by)

    @property
    def verified(self) -> int:
        return sum(1 for t in self.active_traces if t.tested_by)

    @property
    def design_rate(self) -> float | None:
        return _rate(self.designed, self.active)

    @property
    def verification_rate(self) -> float | None:
        return _rate(self.verified, self.active)

    @property
    def by_status(self) -> dict[str, int]:
        counts = {status.value: 0 for status in Status} | {UNSET: 0}
        for trace in self.traces:
            counts[trace.status] += 1
        return counts

    @property
    def by_priority(self) -> dict[str, int]:
        counts = {priority.value: 0 for priority in Priority} | {UNSET: 0}
        for trace in self.traces:
            counts[trace.priority] += 1
        return counts


def _check_filter(value: str | None, vocabulary: type[StrEnum], name: str) -> None:
    allowed = [member.value for member in vocabulary] + [UNSET]
    if value is not None and value not in allowed:
        msg = f"unknown {name} {value!r}: expected one of {', '.join(allowed)}"
        raise ValueError(msg)


def _reach(
    start: UUID, incoming: Mapping[UUID, list[Element]]
) -> tuple[tuple[Element, ...], tuple[Element, ...]]:
    """The design and the tests behind a requirement, through chains of requirements.

    Breadth-first over incoming realizations with a set of what was already
    seen, so a loop (A realized by B realized by A) ends instead of spinning.
    A source that is a requirement is followed; a test is collected as a test;
    any other non-motivation element is design; the remaining motivation
    elements (a stakeholder, a driver) are neither.
    """
    designed: dict[UUID, Element] = {}
    tested: dict[UUID, Element] = {}
    seen = {start}
    queue = deque([start])
    while queue:
        for source in incoming.get(queue.popleft(), ()):
            if source.id in seen:
                continue
            seen.add(source.id)
            if is_requirement(source):
                queue.append(source.id)
            elif is_test(source):
                tested[source.id] = source
            elif source.element_type.aspect is not Aspect.MOTIVATION:
                designed[source.id] = source
    return (
        tuple(sorted(designed.values(), key=_by_name)),
        tuple(sorted(tested.values(), key=_by_name)),
    )


def compute_coverage(
    elements: Iterable[Element],
    relationships: Iterable[Relationship],
    *,
    status: str | None = None,
    priority: str | None = None,
) -> Coverage:
    """Who realizes and who tests each requirement, optionally narrowed.

    `status` and `priority` narrow which requirements are listed and counted;
    `UNSET` selects those with no usable value. Anything else outside the
    vocabularies is refused (a `ValueError`, a 422 at the door).
    """
    _check_filter(status, Status, "status")
    _check_filter(priority, Priority, "priority")
    elements = tuple(elements)
    by_id = {element.id: element for element in elements}
    incoming: dict[UUID, list[Element]] = {}
    for link in relationships:
        if link.relationship_type is not RelationshipType.REALIZATION:
            continue
        source = by_id.get(link.source_id)
        if source is not None:
            incoming.setdefault(link.target_id, []).append(source)

    traces: list[RequirementTrace] = []
    for element in elements:
        if not is_requirement(element):
            continue
        trace_status = _read(element.properties, STATUS_PROPERTY, Status)
        trace_priority = _read(element.properties, PRIORITY_PROPERTY, Priority)
        if status is not None and trace_status != status:
            continue
        if priority is not None and trace_priority != priority:
            continue
        designed, tested = _reach(element.id, incoming)
        traces.append(RequirementTrace(element, trace_status, trace_priority, designed, tested))
    traces.sort(key=lambda t: (_PRIORITY_RANK[t.priority], *_by_name(t.requirement)))
    return Coverage(tuple(traces))
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_requirements.py -x`
Expected: PASS (17 tests).

- [ ] **Step 5: Lint et types du fichier**

Run: `cd backend && uv run ruff format src/ea/domain/requirements.py tests/unit/test_requirements.py && uv run ruff check src/ea/domain/requirements.py tests/unit/test_requirements.py && uv run mypy src/ea/domain/requirements.py`
Expected: pas d'erreur (corriger `gaps()` si mypy demande un meilleur type : le paramètre devient `coverage: Coverage` avec l'import).

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/requirements.py backend/tests/unit/test_requirements.py
git commit -m "feat(domain): add requirements coverage as a pure reading of the graph

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Port, lecture PostgreSQL `traceability`, double en mémoire

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (ajouter `RequirementRepository` après `IpamRepository`)
- Modify: `backend/src/ea/repositories/architecture_store.py` (import + méthode `traceability`)
- Modify: `backend/tests/conftest.py` (méthode `traceability` de `InMemoryRepository` + import)
- Create: `backend/tests/integration/test_requirements_graph.py`

**Interfaces:**
- Consumes: `GraphView`, `ElementRecord`, `RelationshipRecord`, `element_from_row`, `relationship_from_row`, `REQUIREMENT_TYPES`.
- Produces: `RequirementRepository.traceability(self) -> GraphView` — tous les éléments de type exigence, les `realization` dont la cible est de type exigence, et les éléments source de ces liens.

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

Create `backend/tests/integration/test_requirements_graph.py`:

```python
"""The requirements read against a real PostgreSQL.

Two claims are about the database and cannot be made with a double: that the
read returns the requirements, the realizations pointing at one and the
elements at their other end — and nothing else — and that, with a test
realizing a requirement, the impact analysis never drags the tests into the
waves of a requirement or of its design.
"""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def realize(service: ArchitectureService, source: Element, target: Element) -> None:
    await service.connect(
        relationship_type=R.REALIZATION, source_id=source.id, target_id=target.id
    )


class TestTraceability:
    async def test_it_returns_the_requirements_and_what_realizes_them_only(
        self, graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
    ) -> None:
        requirement = await graph_service.create_element(element_type=E.REQUIREMENT, name="R1")
        design = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="D")
        test = await graph_service.create_element(
            element_type=E.DELIVERABLE, name="T", properties={"test_result": "passed"}
        )
        goal = await graph_service.create_element(element_type=E.GOAL, name="G")
        await graph_service.create_element(element_type=E.BUSINESS_ACTOR, name="Loner")
        await realize(graph_service, design, requirement)
        await realize(graph_service, test, requirement)
        await realize(graph_service, requirement, goal)  # target is a goal: not traced

        view = await graph_repository.traceability()

        assert {element.name for element in view.elements} == {"R1", "D", "T"}
        assert {link.source_id for link in view.relationships} == {design.id, test.id}

    async def test_an_empty_catalogue_gives_an_empty_view(
        self, graph_repository: PostgresArchitectureRepository
    ) -> None:
        view = await graph_repository.traceability()

        assert view.elements == ()
        assert view.relationships == ()


class TestImpact:
    async def test_impact_never_reaches_the_tests_of_a_requirement(
        self, graph_service: ArchitectureService
    ) -> None:
        """`realization` runs along the arrow, source to target; a test is a source.

        So the waves of a design element or of a requirement hold the
        requirement and the goal above it, never the tests beside it. What a
        failing test reaches is its requirement — pinned here so the
        convention of docs/adr/0042 is a tested fact, not a habit.
        """
        design = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="D")
        requirement = await graph_service.create_element(element_type=E.REQUIREMENT, name="R")
        goal = await graph_service.create_element(element_type=E.GOAL, name="G")
        test = await graph_service.create_element(
            element_type=E.DELIVERABLE, name="T", properties={"test_result": "failed"}
        )
        await realize(graph_service, design, requirement)
        await realize(graph_service, requirement, goal)
        await realize(graph_service, test, requirement)

        from_design = await graph_service.impact_of(design.id)
        from_requirement = await graph_service.impact_of(requirement.id)
        from_test = await graph_service.impact_of(test.id)

        assert {e.name for e in from_design.elements} == {"D", "R", "G"}
        assert {e.name for e in from_requirement.elements} == {"R", "G"}
        assert {e.name for e in from_test.elements} == {"T", "R", "G"}
```

- [ ] **Step 2: Lancer, échec attendu**

Run (PostgreSQL jetable, variables du recipe `test-postgres` du `Makefile`) : `make pg-up`, puis

```bash
cd backend && EA_DEBUG=true EA_POSTGRES_ENABLED=true EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=5433 EA_POSTGRES_PASSWORD="$(make -s -C .. -f Makefile --eval='p:;@echo $(POSTGRES_TEST_PASSWORD)' p)" EA_EMBEDDINGS_ENABLED=false uv run pytest tests/integration/test_requirements_graph.py -x
```

Si cette expansion de mot de passe ne fonctionne pas, copier les variables du recipe `test-postgres` (`Makefile` ligne ~559). Expected: FAIL — `AttributeError: 'PostgresArchitectureRepository' object has no attribute 'traceability'` (le premier test) ; les tests d'impact passent déjà et le resteront (ils épinglent le comportement existant).

- [ ] **Step 3: Ajouter le port**

Edit `backend/src/ea/domain/ports.py`, juste après la classe `IpamRepository` (avant `class AccessTokenVerifier`) :

```python
class RequirementRepository(Protocol):
    """The one read the requirements coverage needs and the catalogue lacks.

    A requirement is an element and a test or a design realizes it
    (docs/adr/0042), so nothing here is a new store: it is the query
    `ElementFilter` cannot express, because it follows a relationship.
    Declared apart from `ArchitectureRepository`, like the IPAM port, and
    satisfied by the same PostgreSQL class.
    """

    async def traceability(self) -> GraphView:
        """Every requirement, and the realizations pointing at one, with their sources.

        The whole set rather than a page: a coverage rate computed on a page of
        the requirements would be wrong, silently. It is bounded by how many
        requirements are modelled, not by how large the graph is.
        """
        ...
```

- [ ] **Step 4: Implémenter la lecture PostgreSQL**

Edit `backend/src/ea/repositories/architecture_store.py` :

1. Ajouter à la liste d'imports du domaine (après `from ea.domain.ports import ElementFilter, GraphView`) : `from ea.domain.requirements import REQUIREMENT_TYPES`
2. Ajouter à la fin de la classe `PostgresArchitectureRepository` (après `element_at`) :

```python
    # --- Requirements (docs/adr/0042) --------------------------------------

    async def traceability(self) -> GraphView:
        kinds = [kind.value for kind in REQUIREMENT_TYPES]
        links_statement = (
            select(RelationshipRecord)
            .where(
                RelationshipRecord.relationship_type == RelationshipType.REALIZATION.value,
                RelationshipRecord.target_type.in_(kinds),
            )
            .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
        )
        async with self._sessions() as session:
            links = tuple(
                relationship_from_row(row) for row in await session.scalars(links_statement)
            )
            sources = list({link.source_id for link in links})
            elements_statement = (
                select(ElementRecord)
                .where(or_(ElementRecord.element_type.in_(kinds), ElementRecord.id.in_(sources)))
                .order_by(ElementRecord.name, ElementRecord.id)
            )
            elements = tuple(
                element_from_row(row) for row in await session.scalars(elements_statement)
            )
        return GraphView(elements=elements, relationships=links)
```

- [ ] **Step 5: Ajouter la méthode au double en mémoire**

Edit `backend/tests/conftest.py` : ajouter `from ea.domain.requirements import REQUIREMENT_TYPES` parmi les imports `ea.domain`, puis dans `InMemoryRepository`, après `element_at` :

```python
    # --- Requirements (docs/adr/0042) ---

    async def traceability(self) -> GraphView:
        links = tuple(
            link
            for link in self.relationships.values()
            if link.relationship_type is R.REALIZATION and link.target_type in REQUIREMENT_TYPES
        )
        wanted = {link.source_id for link in links}
        return GraphView(
            elements=tuple(
                element
                for element in self.elements.values()
                if element.element_type in REQUIREMENT_TYPES or element.id in wanted
            ),
            relationships=links,
        )
```

- [ ] **Step 6: Relancer, PASS**

Run: la même commande qu'au Step 2. Expected: PASS (3 tests). Puis `cd backend && uv run pytest tests/unit -x -q` (le double a gagné une méthode : rien ne doit casser) et `uv run mypy src`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/architecture_store.py backend/tests/conftest.py backend/tests/integration/test_requirements_graph.py
git commit -m "feat(repositories): read the requirements traceability from PostgreSQL

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `RequirementService`

**Files:**
- Create: `backend/src/ea/services/requirements.py`
- Create: `backend/tests/unit/test_requirement_service.py`
- Modify: `backend/tests/unit/test_service_guards.py` (entrée `RequirementService: set()` dans `WRITES` + import)

**Interfaces:**
- Consumes: `RequirementRepository` (Task 2), `compute_coverage`, `Coverage` (Task 1), `require_caller`.
- Produces: `RequirementService(repository: RequirementRepository)` avec `async def coverage(self, *, status: str | None = None, priority: str | None = None) -> Coverage`.

- [ ] **Step 1: Écrire les tests qui échouent**

Create `backend/tests/unit/test_requirement_service.py`:

```python
"""The requirements use case, over the same in-memory graph as the others."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import NotAuthenticatedError
from ea.domain.requirements import Gap
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.requirements import RequirementService
from tests.conftest import InMemoryRepository, a_reader


@pytest.fixture
def requirements(repository: InMemoryRepository) -> RequirementService:
    return RequirementService(repository)


@pytest.mark.asyncio
class TestCoverage:
    async def test_it_reads_what_the_architecture_service_wrote(
        self, service: ArchitectureService, requirements: RequirementService
    ) -> None:
        requirement = await service.create_element(
            element_type=E.REQUIREMENT, name="R1", properties={"req_status": "approved"}
        )
        design = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        await service.connect(
            relationship_type=R.REALIZATION, source_id=design.id, target_id=requirement.id
        )

        coverage = await requirements.coverage()

        (trace,) = coverage.traces
        assert [element.name for element in trace.designed_by] == ["Billing"]
        assert trace.gap is Gap.VERIFICATION

    async def test_the_filters_reach_the_computation(
        self, service: ArchitectureService, requirements: RequirementService
    ) -> None:
        await service.create_element(
            element_type=E.REQUIREMENT, name="A", properties={"req_status": "approved"}
        )
        await service.create_element(
            element_type=E.REQUIREMENT, name="B", properties={"req_status": "proposed"}
        )

        coverage = await requirements.coverage(status="proposed")

        assert [trace.requirement.name for trace in coverage.traces] == ["B"]

    async def test_a_reader_may_read_the_coverage(self, requirements: RequirementService) -> None:
        with acting_as(a_reader()):
            coverage = await requirements.coverage()

        assert coverage.total == 0

    async def test_nobody_calling_is_refused(
        self, requirements: RequirementService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await requirements.coverage()
```

Edit `backend/tests/unit/test_service_guards.py` : ajouter `from ea.services.requirements import RequirementService` après l'import d'`IpamService`, et dans `WRITES` la ligne :

```python
    RequirementService: set(),
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_requirement_service.py tests/unit/test_service_guards.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.services.requirements'`.

- [ ] **Step 3: Implémentation minimale**

Create `backend/src/ea/services/requirements.py`:

```python
"""The requirements coverage, as a reading of the architecture graph.

This service adds no store and writes nothing. A requirement is an element, a
test or a design realizes it, and the coverage is computed here on demand from
one repository read — see `docs/adr/0042`. Writing a requirement, its status or
its test result goes through `ArchitectureService`, so the ArchiMate rules and
the properties convention are enforced there and nowhere else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ea.domain.requirements import Coverage, compute_coverage
from ea.services.caller import require_caller

if TYPE_CHECKING:
    from ea.domain.ports import RequirementRepository


class RequirementService:
    """The requirements use cases."""

    def __init__(self, repository: RequirementRepository) -> None:
        self._repository = repository

    async def coverage(
        self, *, status: str | None = None, priority: str | None = None
    ) -> Coverage:
        """Which requirements are realized by a design, and tested.

        `status` and `priority` narrow the requirements listed and counted; an
        unknown value is a `ValueError`, answered with a 422.
        """
        require_caller()
        view = await self._repository.traceability()
        return compute_coverage(
            view.elements, view.relationships, status=status, priority=priority
        )
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_requirement_service.py tests/unit/test_service_guards.py -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/requirements.py backend/tests/unit/test_requirement_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(services): add RequirementService with a guarded coverage read

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Modèles de lecture, route HTTP et câblage

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (bornes + modèles de lecture, en fin de fichier)
- Create: `backend/src/ea/api/requirements.py`
- Modify: `backend/src/ea/api/dependencies.py` (fournisseur du service)
- Modify: `backend/src/ea/main.py` (routeur, lifespan, `create_app(requirements=...)`)
- Create: `backend/tests/e2e/test_requirements_api.py`

**Interfaces:**
- Consumes: `RequirementService.coverage`, `Coverage`, `RequirementTrace`, `Gap`, `RESULT_PROPERTY`.
- Produces: `CoverageFilter`, `TraceElementRead`, `RequirementTraceRead`, `CoverageRead.of(coverage: Coverage) -> CoverageRead` ; `requirement_service_of(app: FastAPI) -> RequirementService`, `Requirements` (dépendance annotée) ; route `GET /requirements/coverage` ; paramètre `create_app(..., requirements: RequirementRepository | None = None)`.

- [ ] **Step 1: Écrire le test e2e qui échoue**

Create `backend/tests/e2e/test_requirements_api.py`:

```python
"""The coverage endpoint, end to end against a doubled graph."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        requirements=repository,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def make(
    client: httpx.AsyncClient, element_type: str, name: str, **properties: str
) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def realize(client: httpx.AsyncClient, source: dict[str, Any], target: dict[str, Any]) -> None:
    response = await client.post(
        "/relationships",
        json={
            "relationship_type": "realization",
            "source_id": source["id"],
            "target_id": target["id"],
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
class TestCoverageEndpoint:
    async def test_an_empty_catalogue_has_no_rate(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/requirements/coverage")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["design_rate"] is None
        assert body["verification_rate"] is None
        assert body["requirements"] == []

    async def test_the_gaps_are_reported(self, client: httpx.AsyncClient) -> None:
        orphan = await make(client, "requirement", "Sans conception", req_id="REQ-1")
        untested = await make(client, "requirement", "Sans test", req_status="approved")
        done = await make(client, "requirement", "Complète", req_priority="must")
        design = await make(client, "application_component", "Coffre")
        test = await make(client, "deliverable", "Test chiffrement", test_result="passed")
        await realize(client, design, untested)
        await realize(client, design, done)
        await realize(client, test, done)

        body = (await client.get("/requirements/coverage")).json()

        gap_of = {item["name"]: item["gap"] for item in body["requirements"]}
        assert gap_of == {"Sans conception": "design", "Sans test": "verification", "Complète": "none"}
        assert body["total"] == 3
        assert body["designed"] == 2
        assert body["verified"] == 1
        assert body["design_rate"] == pytest.approx(2 / 3)
        done_item = next(item for item in body["requirements"] if item["name"] == "Complète")
        assert [e["name"] for e in done_item["designed_by"]] == ["Coffre"]
        assert done_item["tested_by"][0]["result"] == "passed"
        orphan_item = next(i for i in body["requirements"] if i["id"] == orphan["id"])
        assert orphan_item["identifier"] == "REQ-1"
        assert orphan_item["status"] == "unset"

    async def test_the_list_can_be_narrowed_by_status(self, client: httpx.AsyncClient) -> None:
        await make(client, "requirement", "A", req_status="approved")
        await make(client, "requirement", "B", req_status="proposed")

        body = (await client.get("/requirements/coverage", params={"status": "approved"})).json()

        assert [item["name"] for item in body["requirements"]] == ["A"]
        assert body["by_status"]["approved"] == 1
        assert body["by_status"]["proposed"] == 0

    async def test_an_unknown_status_is_a_422_with_the_typed_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/requirements/coverage", params={"status": "banana"})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_input"
        assert "banana" in response.json()["detail"]
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/e2e/test_requirements_api.py -x`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'requirements'`.

- [ ] **Step 3: Ajouter bornes et modèles de lecture**

Edit `backend/src/ea/api/schemas.py` : ajouter `from ea.domain.requirements import RESULT_PROPERTY, Coverage, Gap, RequirementTrace` parmi les imports `ea.domain` (ordre alphabétique de ruff), puis en fin de fichier :

```python
# --- Requirements and their traceability (docs/adr/0042) ---

#: A status or a priority to narrow the coverage to. Which values exist is
#: `domain/requirements.py`'s decision and the domain refuses the others; this
#: only stops a runaway argument, for both adapters.
CoverageFilter = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32,
        description="A value of the vocabulary, or `unset` for requirements that have none.",
    ),
]


class TraceElementRead(BaseModel):
    """An element on the other end of a realization of a requirement."""

    id: UUID
    element_type: ElementType
    name: str
    result: str | None = Field(
        description="For a test, the result it records (`passed`, `failed`…); absent otherwise."
    )

    @classmethod
    def of(cls, element: Element) -> TraceElementRead:
        return cls(
            id=element.id,
            element_type=element.element_type,
            name=element.name,
            result=element.properties.get(RESULT_PROPERTY),
        )


class RequirementTraceRead(BaseModel):
    """One requirement, what realizes it, what tests it, and what is missing."""

    id: UUID
    element_type: ElementType
    name: str
    identifier: str = Field(description="The readable identifier, empty when none is recorded.")
    status: str
    priority: str
    gap: Gap
    designed_by: list[TraceElementRead]
    tested_by: list[TraceElementRead]

    @classmethod
    def of(cls, trace: RequirementTrace) -> RequirementTraceRead:
        return cls(
            id=trace.requirement.id,
            element_type=trace.requirement.element_type,
            name=trace.requirement.name,
            identifier=trace.identifier,
            status=trace.status,
            priority=trace.priority,
            gap=trace.gap,
            designed_by=[TraceElementRead.of(element) for element in trace.designed_by],
            tested_by=[TraceElementRead.of(element) for element in trace.tested_by],
        )


class CoverageRead(BaseModel):
    """The coverage of the requirements asked about.

    The rates are over the *active* requirements (a rejected one needs neither
    a design nor a test) and are `null` when there is none: a rate over nothing
    is not 100 %.
    """

    total: int
    active: int
    designed: int
    verified: int
    design_rate: float | None
    verification_rate: float | None
    by_status: dict[str, int]
    by_priority: dict[str, int]
    requirements: list[RequirementTraceRead]

    @classmethod
    def of(cls, coverage: Coverage) -> CoverageRead:
        return cls(
            total=coverage.total,
            active=coverage.active,
            designed=coverage.designed,
            verified=coverage.verified,
            design_rate=coverage.design_rate,
            verification_rate=coverage.verification_rate,
            by_status=coverage.by_status,
            by_priority=coverage.by_priority,
            requirements=[RequirementTraceRead.of(trace) for trace in coverage.traces],
        )
```

- [ ] **Step 4: Le routeur**

Create `backend/src/ea/api/requirements.py`:

```python
"""The requirements coverage endpoint.

It reads the same graph `/elements` serves: a requirement is an element and a
test or a design realizes it (`docs/adr/0042`). The route adds no store, and
the rates are the server's answer: the SPA shows them and never recomputes one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ea.api.dependencies import Requirements
from ea.api.schemas import CoverageFilter, CoverageRead, ErrorResponse

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.get(
    "/coverage",
    response_model=CoverageRead,
    responses={422: {"model": ErrorResponse}},
)
async def read_coverage(
    service: Requirements,
    status: Annotated[CoverageFilter | None, Query()] = None,
    priority: Annotated[CoverageFilter | None, Query()] = None,
) -> CoverageRead:
    """Which requirements are realized by a design and tested, and which are not.

    `status` and `priority` narrow the requirements listed and counted. The
    rates leave out rejected requirements and are `null` when none is left.
    """
    return CoverageRead.of(await service.coverage(status=status, priority=priority))
```

- [ ] **Step 5: Fournisseur du service et câblage**

Edit `backend/src/ea/api/dependencies.py` : ajouter `from ea.services.requirements import RequirementService` après l'import d'`IpamService`, puis après `Ipam = ...` :

```python
def requirement_service_of(app: FastAPI) -> RequirementService:
    """The requirements use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled with a doubled architecture service and
    no repository behind it: the coverage needs the one read `ElementFilter`
    cannot express. A wiring fault, so a 500 — never an empty coverage, which a
    client could not tell from a catalogue with no requirement.
    """
    service: RequirementService | None = getattr(app.state, "requirement_service", None)
    if service is None:
        msg = "no requirement service on the application — was it assembled without a graph?"
        raise RuntimeError(msg)
    return service


def get_requirement_service(request: Request) -> RequirementService:
    return requirement_service_of(request.app)


Requirements = Annotated[RequirementService, Depends(get_requirement_service)]
```

Edit `backend/src/ea/main.py` :
1. importer `requirement_service_of` avec les autres fournisseurs de `ea.api.dependencies`, `from ea.api.requirements import router as requirements_router`, `RequirementRepository` avec `IpamRepository` (import depuis `ea.domain.ports`), `from ea.services.requirements import RequirementService` après `IpamService` ;
2. dans le lifespan, juste après `app.state.ipam_service = IpamService(app.state.architecture_service, repository)` : `app.state.requirement_service = RequirementService(repository)` ;
3. dans `create_app`, ajouter le paramètre `requirements: RequirementRepository | None = None` après `ipam`, la ligne de docstring « `requirements` does the same for the requirements coverage (docs/adr/0042). », et dans le bloc `if architecture_service is not None:` après le bloc `ipam` :

```python
        if requirements is not None:
            app.state.requirement_service = RequirementService(requirements)
```
4. après `app.include_router(ipam_router, ...)` : `app.include_router(requirements_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)`.

- [ ] **Step 6: Relancer, PASS**

Run: `cd backend && uv run pytest tests/e2e/test_requirements_api.py tests/unit/test_openapi.py -x && uv run mypy src && uv run ruff check src tests`
Expected: PASS ; mypy et ruff propres. (`test_openapi` ne doit pas casser : la nouvelle route déclare 401/403 par le routeur.)

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/api/schemas.py backend/src/ea/api/requirements.py backend/src/ea/api/dependencies.py backend/src/ea/main.py backend/tests/e2e/test_requirements_api.py
git commit -m "feat(api): serve the requirements coverage at GET /requirements/coverage

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Outil MCP `get_coverage`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports, `RequirementProvider`, 5e paramètre de `build_mcp_server`, outil, `INSTRUCTIONS`)
- Modify: `backend/src/ea/main.py` (`_mount_mcp` passe `lambda: requirement_service_of(app)`)
- Modify: `backend/tests/unit/test_mcp_server.py` (fixtures, liste complète, comportement, bornes)

**Interfaces:**
- Consumes: `RequirementService.coverage`, `CoverageRead`, `CoverageFilter` (Task 4), `READS`, `speaking_plainly`.
- Produces: `RequirementProvider = Callable[[], RequirementService]` ; `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_requirements, *, version=..., token_verifier=..., auth=...)` ; outil `get_coverage(status: CoverageStatus | None = None, priority: CoveragePriority | None = None) -> CoverageRead`.

Si Task 0 a relevé une signature différente (sous-projets 4/6 livrés), insérer `get_requirements` à la suite des fournisseurs existants et adapter les fixtures en conséquence.

- [ ] **Step 1: Écrire les tests qui échouent**

Edit `backend/tests/unit/test_mcp_server.py` :

1. Ajouter l'import `from ea.services.requirements import RequirementService`. Ajouter une fixture :

```python
@pytest.fixture
def requirements(repository: InMemoryRepository) -> RequirementService:
    return RequirementService(repository)
```

(importer `InMemoryRepository` depuis `tests.conftest` s'il ne l'est pas déjà), ajouter le paramètre `requirements: RequirementService` aux fixtures `server` et `server_without_an_index`, et le fournisseur `lambda: requirements` à la fin de leurs deux appels `build_mcp_server(...)`.

2. Ajouter `"get_coverage",` à l'ensemble de `test_it_exposes_the_whole_service_and_nothing_else` (après `"delete_file"`).

3. Ajouter une classe :

```python
@pytest.mark.asyncio
class TestCoverage:
    async def test_it_lists_the_gaps_an_agent_asks_about(self, server: MCPServer[Any]) -> None:
        requirement = await call(
            server,
            "create_element",
            element_type="requirement",
            name="Chiffrer",
            properties={"req_status": "approved"},
        )
        design = await an_element(server, "application_component", "Coffre")
        await call(
            server,
            "connect_elements",
            relationship_type="realization",
            source_id=design["id"],
            target_id=requirement["id"],
        )

        coverage = await call(server, "get_coverage", status="approved")

        assert coverage["total"] == 1
        (item,) = coverage["requirements"]
        assert item["gap"] == "verification"
        assert item["designed_by"][0]["name"] == "Coffre"

    async def test_an_unknown_status_comes_back_as_a_tool_error(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="banana"):
            await server.call_tool("get_coverage", {"status": "banana"})

    async def test_the_tool_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "get_coverage")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
```

4. Dans le `parametrize` de `test_a_query_parameter_and_its_tool_argument_agree`, ajouter :

```python
            ("get_coverage", "status", "/requirements/coverage", "status"),
            ("get_coverage", "priority", "/requirements/coverage", "priority"),
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — `TypeError: build_mcp_server() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Implémenter l'outil**

Edit `backend/src/ea/mcp/server.py` :

1. Imports : ajouter `CoverageFilter, CoverageRead` à l'import de `ea.api.schemas` (ordre alphabétique), `from ea.services.requirements import RequirementService` après `IpamService`.
2. Après `FileProvider = Callable[[], FileService]` : `RequirementProvider = Callable[[], RequirementService]`.
3. Après `FileContent` (arguments) :

```python
CoverageStatus = Annotated[
    CoverageFilter,
    Field(description="Only the requirements with this status (e.g. `approved`), or `unset`."),
]
CoveragePriority = Annotated[
    CoverageFilter,
    Field(description="Only the requirements with this priority (`must`, `should`, `could`)."),
]
```
4. Signature : ajouter `get_requirements: RequirementProvider,` après `get_files: FileProvider,` ; compléter la docstring de `build_mcp_server` : « `get_requirements` is required for the same reason as the others: the tool is listed and fails, rather than the list shrinking. »
5. Avant la section des documents ou à la fin des outils, ajouter :

```python
    # --- Requirements -----------------------------------------------------
    # A requirement is an element and a test or a design realizes it
    # (docs/adr/0042); this tool only reads that graph.

    @server.tool(annotations=READS)
    @speaking_plainly
    async def get_coverage(
        status: CoverageStatus | None = None,
        priority: CoveragePriority | None = None,
    ) -> CoverageRead:
        """Which requirements are realized by a design, and tested by a test.

        Call it before you claim a requirement is done, or to list the ones
        nobody realizes (`gap` = `design`) or nobody tests (`gap` =
        `verification`). Each requirement says what realizes it
        (`designed_by`) and what tests it (`tested_by`, with each test's
        result). The rates leave out rejected requirements and are null when
        there is none. To change a status or a result, use `update_element`
        (`req_status`, `test_result`).
        """
        return CoverageRead.of(
            await get_requirements().coverage(status=status, priority=priority)
        )
```
6. `INSTRUCTIONS` : avant le paragraphe « The server also keeps files… », ajouter :

```
Requirements live in the catalogue too: a `requirement` or `constraint` element
whose `req_status` (`proposed`, `approved`, `implemented`, `verified`,
`rejected`) and `req_priority` (`must`, `should`, `could`) are properties. A
design element or a test (a `deliverable` or `artifact` carrying `test_result`)
is linked to the requirement by `realization`, source to target. `get_coverage`
answers "is this requirement realized, and tested?" and lists the gaps;
`impact_of` is not that question — it follows what a requirement realizes, not
what realizes it.
```

Edit `backend/src/ea/main.py` : dans `_mount_mcp`, après `lambda: file_service_of(app),` ajouter `lambda: requirement_service_of(app),`.

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/e2e/test_mcp_endpoint.py -x && uv run mypy src tests/unit/test_mcp_server.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/src/ea/main.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): add the get_coverage tool for requirements traceability

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Régénérer le schéma OpenAPI et le client

**Files:**
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés, jamais édités à la main)

**Interfaces:**
- Consumes: la route et les modèles de la Task 4.
- Produces: `components['schemas']['CoverageRead']`, `RequirementTraceRead`, `TraceElementRead` côté TypeScript ; chemin `/requirements/coverage`.

- [ ] **Step 1: Vérifier que le contrôle échoue avant régénération**

Run: `make openapi-check`
Expected: FAIL — « backend/openapi.json est périmé. Lance : make openapi ».

- [ ] **Step 2: Régénérer**

Run: `make openapi`
Expected: « Schéma et client régénérés. »

- [ ] **Step 3: Vérifier**

Run: `make openapi-check && git diff --stat`
Expected: « Client généré à jour. » ; seuls `backend/openapi.json` et `frontend/src/api/schema.d.ts` ont changé, et `grep -c "RequirementTraceRead" frontend/src/api/schema.d.ts` donne au moins 1.

- [ ] **Step 4: Commit**

```bash
git add backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "chore(api): regenerate the OpenAPI schema and client for requirements

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Section SPA « Couverture des exigences »

**Files:**
- Create: `frontend/src/features/requirements/labels.ts`
- Create: `frontend/src/features/requirements/useCoverage.ts`
- Create: `frontend/src/features/requirements/CoverageSection.vue`
- Modify: `frontend/src/router/sections.ts` (entrée après `impact`)
- Modify: `frontend/tests/support/api.ts` (`aTrace`, `aCoverage`)
- Create: `frontend/tests/CoverageSection.spec.ts`

**Interfaces:**
- Consumes: `GET /requirements/coverage` via le client généré, `useLatestRequest`, `api`, `unwrap`, `createAppRouter`.
- Produces: `useCoverage()` → `{ coverage, status, error, load(filter?) }` ; `STATUS_LABELS`, `PRIORITY_LABELS`, `GAP_LABELS`, `labelOf(table, key)` ; vue `CoverageSection.vue` ; état d'URL `?status=` et `?priority=` (remplacés, pas empilés).

- [ ] **Step 1: Ajouter les gabarits de test**

Edit `frontend/tests/support/api.ts` : ajouter, avec le même style d'import de types que le fichier (ex. `type CoverageRead = components['schemas']['CoverageRead']`), puis à la suite des autres constructeurs :

```ts
export type CoverageRead = components['schemas']['CoverageRead']
export type RequirementTraceRead = components['schemas']['RequirementTraceRead']

export function aTrace(overrides: Partial<RequirementTraceRead> = {}): RequirementTraceRead {
  return {
    id: 'aaaaaaaa-1111-4111-8111-111111111111',
    element_type: 'requirement',
    name: 'Chiffrer les données',
    identifier: 'REQ-1',
    status: 'approved',
    priority: 'must',
    gap: 'design',
    designed_by: [],
    tested_by: [],
    ...overrides,
  }
}

export function aCoverage(
  requirements: RequirementTraceRead[] = [aTrace()],
  overrides: Partial<CoverageRead> = {},
): CoverageRead {
  return {
    total: requirements.length,
    active: requirements.length,
    designed: 0,
    verified: 0,
    design_rate: requirements.length === 0 ? null : 0,
    verification_rate: requirements.length === 0 ? null : 0,
    by_status: { proposed: 0, approved: requirements.length, implemented: 0, verified: 0, rejected: 0, unset: 0 },
    by_priority: { must: requirements.length, should: 0, could: 0, unset: 0 },
    requirements,
    ...overrides,
  }
}
```

- [ ] **Step 2: Écrire le test qui échoue**

Create `frontend/tests/CoverageSection.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import CoverageSection from '../src/features/requirements/CoverageSection.vue'
import { createAppRouter } from '../src/router'
import { aCoverage, aTrace, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SANS_CONCEPTION = aTrace({ name: 'Chiffrer les données', gap: 'design' })
const SANS_TEST = aTrace({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Journaliser les accès',
  identifier: 'REQ-2',
  gap: 'verification',
  designed_by: [
    { id: 'cccccccc-3333-4333-8333-333333333333', element_type: 'application_component', name: 'Coffre', result: null },
  ],
})

const COVERAGE: Route = {
  path: '/requirements/coverage',
  body: aCoverage([SANS_CONCEPTION, SANS_TEST], {
    designed: 1,
    verified: 0,
    design_rate: 0.5,
    verification_rate: 0,
  }),
}

async function open(query = '') {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/couverture${query}`)
  await router.isReady()
  render(CoverageSection, { global: { plugins: [router] } })
  return router
}

describe('CoverageSection', () => {
  it('lists the requirements with the gap each one has', async () => {
    stubApi([COVERAGE])
    await open()

    expect(await screen.findByText('Chiffrer les données')).toBeInTheDocument()
    expect(screen.getByText('Non réalisée')).toBeInTheDocument()
    expect(screen.getByText('Non testée')).toBeInTheDocument()
    expect(screen.getByText('Coffre')).toBeInTheDocument()
    expect(screen.getByText('50 %')).toBeInTheDocument()
  })

  it('links a requirement to its detail in the catalogue', async () => {
    stubApi([COVERAGE])
    await open()

    const link = await screen.findByRole('link', { name: 'Chiffrer les données' })

    expect(link).toHaveAttribute('href', `/elements?element=${SANS_CONCEPTION.id}`)
  })

  it('shows a dash instead of a rate when there is nothing to measure', async () => {
    stubApi([{ path: '/requirements/coverage', body: aCoverage([]) }])
    await open()

    expect(await screen.findByText('Aucune exigence dans le catalogue.')).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
  })

  it('offers the statuses the answer carries and puts the choice in the URL', async () => {
    const calls = stubApi([COVERAGE])
    const router = await open()
    await screen.findByText('Chiffrer les données')

    await fireEvent.update(screen.getByLabelText('Statut'), 'approved')

    await waitFor(() => expect(router.currentRoute.value.query.status).toBe('approved'))
    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('status')).toBe('approved'))
  })

  it('asks for the status named in the URL on arrival', async () => {
    const calls = stubApi([COVERAGE])
    await open('?status=proposed&priority=must')

    await screen.findByText('Chiffrer les données')

    expect(calls[0].url.searchParams.get('status')).toBe('proposed')
    expect(calls[0].url.searchParams.get('priority')).toBe('must')
  })

  it('says why it could not load', async () => {
    stubApi([
      { path: '/requirements/coverage', status: 500, body: { error: 'internal', detail: 'Le graphe est tombé.' } },
    ])
    await open()

    expect(await screen.findByRole('alert')).toHaveTextContent('Le graphe est tombé.')
  })
})
```

- [ ] **Step 3: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/CoverageSection.spec.ts`
Expected: FAIL — « Failed to resolve import "../src/features/requirements/CoverageSection.vue" ».

- [ ] **Step 4: Implémenter**

Create `frontend/src/features/requirements/labels.ts`:

```ts
// Translation tables, and not one rule: which statuses, priorities and gaps
// exist is the API's — the screen lists the keys the answer carries and only
// puts words on them, exactly as `LAYER_LABELS` does for the layers.

export const STATUS_LABELS: Record<string, string> = {
  proposed: 'Proposée',
  approved: 'Approuvée',
  implemented: 'Implémentée',
  verified: 'Vérifiée',
  rejected: 'Rejetée',
  unset: 'Sans statut',
}

export const PRIORITY_LABELS: Record<string, string> = {
  must: 'Indispensable',
  should: 'Importante',
  could: 'Souhaitable',
  unset: 'Sans priorité',
}

export const GAP_LABELS: Record<string, string> = {
  design: 'Non réalisée',
  verification: 'Non testée',
  none: 'Aucun écart',
}

/** The words for a key, or the key itself when a newer API sends one this table lacks. */
export function labelOf(table: Record<string, string>, key: string): string {
  return table[key] ?? key
}
```

Create `frontend/src/features/requirements/useCoverage.ts`:

```ts
// The coverage screen's state: the requirements, what realizes and tests each,
// and the rates — all computed by the server. Not one rate is recomputed here:
// a percentage worked out in the browser would be a second answer to a question
// the server has already answered, free to disagree with it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type CoverageRead = components['schemas']['CoverageRead']
export type RequirementTraceRead = components['schemas']['RequirementTraceRead']

const EMPTY: CoverageRead = {
  total: 0,
  active: 0,
  designed: 0,
  verified: 0,
  design_rate: null,
  verification_rate: null,
  by_status: {},
  by_priority: {},
  requirements: [],
}

export type CoverageFilter = { status?: string; priority?: string }

export function useCoverage() {
  const coverage = ref<CoverageRead>(EMPTY)
  // The filters change faster than the network answers: only the last question
  // may reach the screen.
  const request = useLatestRequest()

  async function load(filter: CoverageFilter = {}): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/requirements/coverage', {
            params: {
              query: { status: filter.status || undefined, priority: filter.priority || undefined },
            },
            signal,
          }),
        ),
      (answer) => {
        coverage.value = answer
      },
    )
  }

  return { coverage, status: request.status, error: request.error, load }
}
```

Create `frontend/src/features/requirements/CoverageSection.vue`:

```vue
<script setup lang="ts">
// The `/analyse/couverture` section: is each requirement realized by a design,
// and tested by a test?
//
// There is nothing here the catalogue does not already hold: a requirement is
// an element and its realizers are links (docs/adr/0042). This screen is a
// reading of the server's coverage. The two filters live in the URL, per the
// rule in CLAUDE.md: a colleague should be able to be sent "the approved
// requirements nobody tests".
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { GAP_LABELS, PRIORITY_LABELS, STATUS_LABELS, labelOf } from './labels'
import { useCoverage } from './useCoverage'

const route = useRoute()
const router = useRouter()
const { coverage, error, load } = useCoverage()

/** A query parameter is `string | string[] | null`; only one value means anything. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const status = computed(() => one(route.query.status))
const priority = computed(() => one(route.query.priority))

watch(
  [status, priority],
  () => load({ status: status.value, priority: priority.value }),
  { immediate: true },
)

async function narrow(key: 'status' | 'priority', value: string): Promise<void> {
  // A dial turned replaces the entry: it is the same question, refined.
  await router.replace({ query: { ...route.query, [key]: value || undefined } })
}

function onStatus(event: Event): void {
  void narrow('status', (event.target as HTMLSelectElement).value)
}

function onPriority(event: Event): void {
  void narrow('priority', (event.target as HTMLSelectElement).value)
}

/** `null` is "nothing to measure", which is not 0 % and not 100 %. */
function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)} %`
}

const names = (items: { name: string }[]) => items.map((item) => item.name).join(', ') || '—'
</script>

<template>
  <section class="coverage" aria-labelledby="coverage-title">
    <header>
      <h2 id="coverage-title">Couverture des exigences</h2>
      <p>Chaque exigence est-elle réalisée par la conception, et vérifiée par un test ?</p>
    </header>

    <p v-if="error" role="alert" class="banner">{{ error }}</p>

    <form class="filters" @submit.prevent>
      <label>
        Statut
        <select :value="status" @change="onStatus">
          <option value="">Tous</option>
          <option v-for="key in Object.keys(coverage.by_status)" :key="key" :value="key">
            {{ labelOf(STATUS_LABELS, key) }} ({{ coverage.by_status[key] }})
          </option>
        </select>
      </label>
      <label>
        Priorité
        <select :value="priority" @change="onPriority">
          <option value="">Toutes</option>
          <option v-for="key in Object.keys(coverage.by_priority)" :key="key" :value="key">
            {{ labelOf(PRIORITY_LABELS, key) }} ({{ coverage.by_priority[key] }})
          </option>
        </select>
      </label>
    </form>

    <dl class="tiles">
      <div>
        <dt>Réalisées par la conception</dt>
        <dd>{{ coverage.designed }} sur {{ coverage.active }}</dd>
        <dd class="rate">{{ percent(coverage.design_rate) }}</dd>
      </div>
      <div>
        <dt>Vérifiées par un test</dt>
        <dd>{{ coverage.verified }} sur {{ coverage.active }}</dd>
        <dd class="rate">{{ percent(coverage.verification_rate) }}</dd>
      </div>
    </dl>

    <p v-if="coverage.total === 0" class="empty">Aucune exigence dans le catalogue.</p>
    <table v-else>
      <caption>
        Exigences, les plus importantes d'abord
      </caption>
      <thead>
        <tr>
          <th scope="col">Identifiant</th>
          <th scope="col">Exigence</th>
          <th scope="col">Statut</th>
          <th scope="col">Priorité</th>
          <th scope="col">Réalisée par</th>
          <th scope="col">Testée par</th>
          <th scope="col">Écart</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="trace in coverage.requirements" :key="trace.id" :data-gap="trace.gap">
          <td>{{ trace.identifier || '—' }}</td>
          <td>
            <RouterLink :to="{ name: 'elements', query: { element: trace.id } }">{{ trace.name }}</RouterLink>
          </td>
          <td>{{ labelOf(STATUS_LABELS, trace.status) }}</td>
          <td>{{ labelOf(PRIORITY_LABELS, trace.priority) }}</td>
          <td>{{ names(trace.designed_by) }}</td>
          <td>{{ names(trace.tested_by) }}</td>
          <td>{{ labelOf(GAP_LABELS, trace.gap) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.coverage {
  display: grid;
  gap: 1rem;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}
.tiles dd {
  margin: 0;
}
.rate {
  font-size: 1.5rem;
  font-weight: 600;
}
.banner {
  padding: 0.5rem 0.75rem;
  border: 1px solid currentcolor;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--border, #8884);
}
</style>
```

Edit `frontend/src/router/sections.ts` : après l'entrée `impact`, ajouter

```ts
  {
    path: '/analyse/couverture',
    name: 'coverage',
    label: 'Couverture des exigences',
    summary: 'Les exigences sans réalisation et sans test.',
    group: 'analysis',
    view: () => import('../features/requirements/CoverageSection.vue'),
  },
```

- [ ] **Step 5: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/CoverageSection.spec.ts tests/router.spec.ts tests/AppNav.spec.ts`
Expected: PASS. Puis `cd frontend && npm run lint && npm run typecheck` : propres (si le linter réclame un autre ordre des attributs, suivre la règle active sans désactiver aucune règle).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/requirements frontend/src/router/sections.ts frontend/tests/CoverageSection.spec.ts frontend/tests/support/api.ts
git commit -m "feat(frontend): add the requirements coverage section

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Profils « Exigence » et « Cas de test » (dépend du sous-projet 6)

**Files:**
- Modify: `backend/src/ea/domain/requirements.py` (ajouter `requirements_profile()`)
- Modify: `backend/tests/unit/test_requirements.py` (ajouter `TestTheProfile`)
- Create: `backend/tests/e2e/test_requirements_profile.py`

**Interfaces:**
- Consumes: les constantes et énumérations de la Task 1 ; la route `POST /profiles/import` et le format `profile_format: 1` de la spec 06 (relever la forme exacte avec `GET /profiles/{id}/export` sur un profil d'essai, Task 0) ; la fixture de client e2e du sous-projet 6.
- Produces: `requirements_profile() -> dict[str, object]` — le document que l'import de profil accepte ; quatre stéréotypes (un seul `base_type` par stéréotype, règle de la spec 06) : `Exigence` (`requirement`), `Contrainte` (`constraint`), `Cas de test` (`deliverable`), `Cas de test (artefact)` (`artifact`).

La liste des valeurs vient des énumérations de la Task 1 : une seule source, pas de seconde liste.

- [ ] **Step 1: Écrire les tests qui échouent**

Append to `backend/tests/unit/test_requirements.py` (ajouter `re`, et `requirements_profile`, `IDENTIFIER_PROPERTY`, `LAST_RUN_PROPERTY`, `PRIORITY_PROPERTY`, `SOURCE_PROPERTY`, `STATUS_PROPERTY` aux imports, avec `from ea.domain.model import _PROPERTY_KEY`) :

```python
class TestTheProfile:
    def test_it_declares_one_stereotype_per_base_type(self) -> None:
        profile = requirements_profile()

        bases = {s["name"]: s["base_type"] for s in profile["stereotypes"]}  # type: ignore[index, union-attr]
        assert bases == {
            "Exigence": "requirement",
            "Contrainte": "constraint",
            "Cas de test": "deliverable",
            "Cas de test (artefact)": "artifact",
        }
        assert profile["profile_format"] == 1

    def test_every_key_is_a_legal_property_name_and_unique_per_stereotype(self) -> None:
        for stereotype in requirements_profile()["stereotypes"]:  # type: ignore[union-attr]
            keys = [p["key"] for p in stereotype["properties"]]  # type: ignore[index]
            assert len(keys) == len(set(keys))
            assert all(_PROPERTY_KEY.match(key) for key in keys), keys

    def test_the_enumerations_are_the_domain_vocabularies(self) -> None:
        by_name = {s["name"]: s for s in requirements_profile()["stereotypes"]}  # type: ignore[union-attr, index]
        requirement = {p["key"]: p for p in by_name["Exigence"]["properties"]}  # type: ignore[index]
        test = {p["key"]: p for p in by_name["Cas de test"]["properties"]}  # type: ignore[index]

        assert requirement[STATUS_PROPERTY]["allowed_values"] == [s.value for s in Status]
        assert requirement[PRIORITY_PROPERTY]["allowed_values"] == [p.value for p in Priority]
        assert test[RESULT_PROPERTY]["allowed_values"] == [r.value for r in VerificationResult]
        assert test[RESULT_PROPERTY]["required"] is True

    def test_the_test_marker_is_required_so_a_test_is_never_invisible(self) -> None:
        for stereotype in requirements_profile()["stereotypes"]:  # type: ignore[union-attr]
            if stereotype["base_type"] in ("deliverable", "artifact"):  # type: ignore[index]
                required = {p["key"] for p in stereotype["properties"] if p["required"]}  # type: ignore[index]
                assert RESULT_PROPERTY in required

    def test_no_key_collides_with_an_ipam_convention(self) -> None:
        from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY

        taken = {ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY}
        for stereotype in requirements_profile()["stereotypes"]:  # type: ignore[union-attr]
            assert not taken & {p["key"] for p in stereotype["properties"]}  # type: ignore[index]
        assert {IDENTIFIER_PROPERTY, SOURCE_PROPERTY, LAST_RUN_PROPERTY}  # exported names exist
```

Create `backend/tests/e2e/test_requirements_profile.py` en copiant la fixture `client` du fichier e2e du sous-projet 6 relevé en Task 0 (celui qui appelle `/profiles`), puis :

```python
"""The shipped profile is accepted by the profile import of sub-project 6."""

from __future__ import annotations

import httpx
import pytest

from ea.domain.requirements import requirements_profile


@pytest.mark.asyncio
async def test_the_profile_is_accepted_by_the_import_route(client: httpx.AsyncClient) -> None:
    response = await client.post("/profiles/import", json=requirements_profile())

    assert response.is_success, response.text


@pytest.mark.asyncio
async def test_a_status_outside_the_vocabulary_is_then_refused_on_an_element(
    client: httpx.AsyncClient,
) -> None:
    imported = await client.post("/profiles/import", json=requirements_profile())
    assert imported.is_success, imported.text
    stereotypes = (await client.get("/stereotypes")).json()
    exigence = next(s for s in stereotypes if s["name"] == "Exigence")
    element = (
        await client.post(
            "/elements",
            json={
                "element_type": "requirement",
                "name": "Chiffrer",
                "properties": {"req_status": "banana"},
            },
        )
    ).json()

    applied = await client.put(
        f"/elements/{element['id']}/stereotypes", json={"stereotype_ids": [exigence["id"]]}
    )

    assert applied.status_code == 422
```

(Si Task 0 a relevé d'autres noms de champs pour l'import ou la liste plate, corriger le builder ET ces assertions ensemble ; le test e2e est ce qui détecte l'écart.)

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_requirements.py -x -k TheProfile`
Expected: FAIL — `ImportError: cannot import name 'requirements_profile'`.

- [ ] **Step 3: Implémenter le builder**

Append to `backend/src/ea/domain/requirements.py` :

```python
#: A date as the last run of a test is written: `2026-10-10`.
_DATE_PATTERN: Final = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def _property(
    key: str,
    label: str,
    *,
    values: list[str] | None = None,
    pattern: str | None = None,
    required: bool = False,
    help_text: str = "",
) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "required": required,
        "kind": "enum" if values else ("pattern" if pattern else "free"),
        "allowed_values": values,
        "pattern": pattern,
        "help": help_text,
    }


def requirements_profile() -> dict[str, object]:
    """The profile that gives requirements and test cases their expected properties.

    A document in the `profile_format: 1` shape of the profile import
    (docs/adr/0042): the vocabularies are the enumerations above, so the
    profile and the coverage cannot name different values. `required` is not
    blocking at write time (a profile only reports what is missing), which is
    why the coverage detects a test by the *presence* of `test_result`.
    """
    requirement_properties = [
        _property(IDENTIFIER_PROPERTY, "Identifiant", help_text="Identifiant lisible et stable."),
        _property(
            STATUS_PROPERTY,
            "Statut",
            values=[status.value for status in Status],
            required=True,
        ),
        _property(PRIORITY_PROPERTY, "Priorité", values=[p.value for p in Priority]),
        _property(SOURCE_PROPERTY, "Source", help_text="D'où vient l'exigence."),
    ]
    test_properties = [
        _property(
            RESULT_PROPERTY,
            "Résultat",
            values=[result.value for result in VerificationResult],
            required=True,
            help_text="Sans ce champ, l'élément n'est pas compté comme un test.",
        ),
        _property(LAST_RUN_PROPERTY, "Dernière exécution", pattern=_DATE_PATTERN),
    ]

    def stereotype(
        name: str, base_type: ElementType, colour: str, properties: list[dict[str, object]]
    ) -> dict[str, object]:
        return {
            "name": name,
            "base_type": base_type.value,
            "colour": colour,
            "icon_key": None,
            "properties": properties,
        }

    return {
        "profile_format": 1,
        "name": "Exigences et traçabilité",
        "description": "Statut, priorité et résultat de test des exigences (docs/adr/0042).",
        "stereotypes": [
            stereotype("Exigence", ElementType.REQUIREMENT, "#B45309", requirement_properties),
            stereotype("Contrainte", ElementType.CONSTRAINT, "#B45309", requirement_properties),
            stereotype("Cas de test", ElementType.DELIVERABLE, "#0F766E", test_properties),
            stereotype("Cas de test (artefact)", ElementType.ARTIFACT, "#0F766E", test_properties),
        ],
    }
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_requirements.py tests/e2e/test_requirements_profile.py -x`
Expected: PASS. Si le test e2e échoue sur un nom de champ, il décrit l'écart avec la forme réelle d'import : corriger `requirements_profile()` et le test unitaire ensemble.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/requirements.py backend/tests/unit/test_requirements.py backend/tests/e2e/test_requirements_profile.py
git commit -m "feat(domain): ship the requirements profile for the profile import

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Matrice de traçabilité = matrice du sous-projet 4 filtrée par profil (dépend de 4 et 6)

**Files:** à relever en Task 0 (Step 3) : le résolveur d'axe de `backend/src/ea/services/analysis.py`, le sélecteur d'axe de `backend/src/ea/api/schemas.py`, le test e2e de `/matrix` du sous-projet 4. Le plan ne crée pas de matrice propre (spec §3.3 point 4).

**Interfaces:**
- Consumes: `POST /matrix` et ses sélecteurs d'axe `{package_id | query_id | element_ids}` (spec 04 §5) ; `element_stereotypes(element_id, stereotype_id)` et `profile_stereotypes(id, name)` (spec 06 §4).
- Produces: un sélecteur d'axe supplémentaire `{stereotype_id}` qui résout les éléments portant ce stéréotype ; une lecture de dépôt `element_ids_with_stereotype(stereotype_id: UUID) -> tuple[UUID, ...]`.

- [ ] **Step 1: Choisir la branche selon l'état réel (lecture)**

Run: `grep -n "stereotype" backend/src/ea/domain/*.py backend/src/ea/api/schemas.py | head -20` et lire le filtre structuré de `saved_queries` (sous-projet 1).
- **Branche A** — le filtre structuré d'une requête enregistrée sait déjà filtrer sur un stéréotype ou sur un type d'élément : aucun code. Écrire seulement le test ci-dessous (Step 2) en utilisant `query_id`, vérifier qu'il passe, et documenter dans l'ADR « une requête enregistrée *Exigences* alimente la matrice ».
- **Branche B** — sinon, Steps 2 à 5.

- [ ] **Step 2: Écrire le test qui échoue (branche B)**

Dans le fichier e2e de `/matrix` du sous-projet 4, ajouter (adapter le nom de la fixture `client` et les helpers de création à ceux du fichier) :

```python
@pytest.mark.asyncio
async def test_an_axis_can_be_the_elements_of_a_stereotype(client: httpx.AsyncClient) -> None:
    requirement = await make(client, "requirement", "Chiffrer")
    other = await make(client, "requirement", "Autre")
    design = await make(client, "application_component", "Coffre")
    stereotype_id = await import_the_requirements_profile_and_apply(client, requirement)

    response = await client.post(
        "/matrix",
        json={"rows": {"stereotype_id": stereotype_id}, "cols": {"element_ids": [design["id"]]}},
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["rows"]] == [requirement["id"]]
    assert other["id"] not in [row["id"] for row in response.json()["rows"]]
```

où `import_the_requirements_profile_and_apply` importe `requirements_profile()` (Task 8), lit `GET /stereotypes`, applique « Exigence » à `requirement` par `PUT /elements/{id}/stereotypes` et renvoie l'id du stéréotype.

- [ ] **Step 3: Lancer, échec attendu**

Run: le fichier e2e du sous-projet 4 avec `-k stereotype -x`. Expected: FAIL en 422 (le sélecteur d'axe refuse `stereotype_id`, `extra="forbid"`).

- [ ] **Step 4: Implémenter (branche B)**

1. Dépôt : ajouter, au port de lecture que `services/analysis.py` utilise déjà pour résoudre un axe, la méthode `element_ids_with_stereotype(self, stereotype_id: UUID) -> tuple[UUID, ...]`, implémentée par une seule requête SQLAlchemy Core : `select(element_stereotypes.c.element_id).where(element_stereotypes.c.stereotype_id == stereotype_id)` (valeur liée, aucun `text()`), avec son entrée dans le double en mémoire.
2. Schéma : ajouter `stereotype_id: UUID | None = None` au sélecteur d'axe, et la règle « un seul sélecteur à la fois » existante l'étend à ce champ.
3. Service : dans le résolveur d'axe, une branche `stereotype_id` → `element_ids_with_stereotype`, puis le plafond `MAX_MATRIX_AXIS` existant s'applique.
4. SPA du sous-projet 4 : la grammaire d'URL `?rows=` accepte `st:<id>` ; le sélecteur d'axe y ajoute « Profil » alimenté par `GET /stereotypes`.

- [ ] **Step 5: Relancer, PASS, commit**

Run: les tests e2e et Vitest de la matrice. Puis `make openapi` (le schéma a bougé) et :

```bash
git add backend/src/ea/services/analysis.py backend/src/ea/api/schemas.py backend/src/ea/repositories backend/tests backend/openapi.json frontend/src/api/schema.d.ts frontend/src/features/matrix frontend/tests
git commit -m "feat(matrix): let a matrix axis be the elements of a stereotype

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Critère de fin de la tâche : `POST /matrix` avec `rows = {stereotype_id}` ne renvoie que les éléments portant ce stéréotype (les exigences), et la matrice du sous-projet 4 reste l'unique matrice.

---

### Task 10: Vérification, ADR, CLAUDE.md

**Files:**
- Create: `docs/adr/0042-exigences-et-tracabilite.md` (numéro = prochain libre relevé en Task 0 ; 0042 à la rédaction)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: un ADR en statut *Proposition*, une phrase factuelle dans CLAUDE.md.

- [ ] **Step 1: Porte de qualité**

Run: `make check`
Expected: vert (lint-check, lint-fe, typecheck, openapi-check, test avec plancher 90 %, test-fe, pipelines-check). Puis `make test-integration` (conteneurs jetables) : vert. Un échec est corrigé dans la tâche qui possède le code, pas ici.

- [ ] **Step 2: Écrire l'ADR (en entier)**

Create `docs/adr/0042-exigences-et-tracabilite.md` (remplacer 0042 par le numéro relevé) :

```markdown
---
titre: Exigences et traçabilité — un profil, un test relié par realization, une couverture calculée côté serveur
date: 2026-10-10
statut: Proposition   # Proposition | Accepté | Supersédé par : NNNN
affects: domain/requirements.py, services/requirements.py, repositories/architecture_store.py, api/requirements.py, mcp/server.py, frontend/src/features/requirements/
---

# Exigences et traçabilité

## Contexte

ArchiMate porte déjà le noyau d'une gestion d'exigences : les types `requirement` et `constraint`, et la relation `realization` qui va de la conception vers l'exigence. Ce qui manquait était de répondre à « cette exigence est-elle réalisée, par quoi, et testée par quoi ? » sans parcourir le catalogue à la main. La spec du sous-projet 9 proposait un profil du sous-projet 6, un test en `deliverable`, une CTE de couverture et l'exclusion des tests des vagues d'impact.

La lecture du code a corrigé quatre points. Les clés de propriété ne peuvent pas contenir de point (`domain/model.py`), donc `req.status` devient `req_status`. L'analyse d'impact suit `realization` de la source vers la cible (ADR 0013) : l'impact d'une exigence est ce qu'elle réalise, non ce qui la réalise. `impacted_by` part d'un seul élément : une couverture de N exigences en ferait N requêtes. Enfin la présence d'une valeur est déjà, pour l'IPAM, le marqueur d'appartenance à une convention (ADR 0020).

## Décision

Aucune table, aucune migration, aucune dépendance. La couverture est une lecture, calculée à la demande, jamais stockée.

| Notion | Représentation |
|---|---|
| Exigence | élément `requirement` ou `constraint` |
| Statut, priorité, source, identifiant | clés `req_status`, `req_priority`, `req_source`, `req_id` de `properties` |
| Vocabulaires | `proposed, approved, implemented, verified, rejected` ; `must, should, could` ; une valeur absente ou illisible est comptée `unset` |
| Cas de test | élément `deliverable` ou `artifact` portant la clé `test_result` (`not_run, passed, failed, blocked`) et, facultativement, `test_last_run` |
| Lien de traçabilité | `realization`, de la conception ou du test vers l'exigence |

La couverture suit les `realization` entrantes à travers les chaînes d'exigences, avec un ensemble d'éléments déjà vus (une boucle se termine). Un élément non motivationnel qui n'est pas un test est de la conception ; un test est un `deliverable`/`artifact` avec `test_result` ; les autres éléments de motivation (partie prenante, pilote) ne comptent pour rien. Un écart `design` est une exigence sans conception, un écart `verification` une exigence réalisée sans test. Une exigence `rejected` n'entre ni dans les taux ni dans les écarts, et un taux sur zéro exigence active vaut `null`.

Une seule lecture de dépôt, `RequirementRepository.traceability()`, ramène les exigences, les `realization` qui les visent et leurs sources ; le calcul est pur (`domain/requirements.py`). Elle est volontairement non bornée : un taux calculé sur une page serait faux. `RequirementService.coverage` exige un appelant (`require_caller`), ne fait aucune écriture, et sert `GET /requirements/coverage?status=&priority=`, l'outil MCP `get_coverage` et la section SPA `/analyse/couverture`. Le SPA ne recalcule aucun taux et dérive les options de filtre des clés de la réponse.

Le profil « Exigences et traçabilité » (quatre stéréotypes, un par type de base) est fourni sous forme de document d'import par `requirements_profile()`, construit à partir des mêmes énumérations que la couverture. Il apporte la validation des valeurs et le formulaire ; la couverture, elle, ne lit aucune table du sous-projet 6.

L'exclusion des tests des vagues d'impact, prévue par la spec, est inutile : avec la convention test → exigence, un test est une source de `realization`, donc l'impact d'une exigence ou de sa conception ne l'atteint jamais. Un test intégration épingle ce comportement. Réponse à « que casse-t-on si je change cette exigence » : `designed_by` et `tested_by` de la couverture, pas l'impact.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Table `requirements` et `test_runs` | historique, campagnes | seconde vérité à côté du graphe, migration, ADR de plus | Écartée : un résultat = une propriété à jour |
| Détecter un test par jointure sur `element_stereotypes` | profil explicite | le cœur de C3 dépendrait du schéma du sous-projet 6 | Écartée : marqueur par présence de `test_result` |
| CTE par exigence avec `impacted_by` | réutilise l'existant | N requêtes, mauvaise direction (écart 5) | Écartée |
| Exclure les tests de l'impact | prudence | sans objet avec la direction des `realization` | Écartée, comportement épinglé par test |
| Couverture stockée et rafraîchie | lecture rapide | périmée à chaque modification | Écartée |

## Conséquences

Un test sans `test_result` n'existe pas pour la couverture : le profil le déclare obligatoire, mais `required` n'est pas bloquant à l'écriture (spec 06), donc un `deliverable` créé sans cette clé est compté comme de la conception. Il n'y a ni historique de résultats, ni campagne, ni workflow d'approbation. La lecture n'est pas paginée : au-delà de plusieurs milliers d'exigences il faudra une requête récursive ou un filtre par paquet (sous-projet 1). Les statuts sont des codes anglais ASCII, les libellés français vivent dans le SPA. L'outil MCP porte le décompte à un de plus. À surveiller : la définition de « conception » (tout élément non motivationnel qui n'est pas un test) peut devenir trop large si le catalogue contient des éléments de couche implémentation qui ne sont pas de la conception.

## Références

- ADR liés : [0005](0005-metamodele-archimate-complet.md), [0013](0013-analyse-d-impact.md), [0020](0020-adressage-ip-dans-les-proprietes.md), [0032](0032-authentification-keycloak.md)
- Code concerné : `backend/src/ea/domain/requirements.py`, `backend/src/ea/services/requirements.py`, `backend/src/ea/api/requirements.py`, `frontend/src/features/requirements/`
- Spec : `docs/superpowers/specs/2026-10-10-09-gros-morceaux-design.md`
```

Avant de committer : `ls docs/adr | grep -E "0005|0013|0020|0032"` et corriger les quatre noms de fichier des liens pour qu'ils correspondent aux fichiers réels.

- [ ] **Step 3: Mettre à jour CLAUDE.md**

Trois `Edit` sur `CLAUDE.md` :
1. Dans « Project status », remplacer « as thirty-four tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing and the files of the bucket » par « as thirty-five tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing, the files of the bucket and the requirements coverage » (recompter avec `grep -c "@server.tool" backend/src/ea/mcp/server.py` : l'entier est celui du moment, sous-projets 4 et 6 compris).
2. Ajouter à la fin du paragraphe « Project status » consacré au MCP : « `GET /requirements/coverage` and the tool `get_coverage` answer whether each requirement is realized by a design and tested, a pure reading of the graph with no table of its own (see `docs/adr/0042`). »
3. Dans « Repository layout », ajouter sous `domain/` : `requirements.py  # status vocabulary and coverage of requirements — pure, see docs/adr/0042`, et sous `services/` : `requirements.py # RequirementService: the coverage, read only`. Dans la section sur les fournisseurs MCP, « four service providers » devient « five ».

- [ ] **Step 4: Re-vérifier et committer**

Run: `make check`
Expected: vert.

```bash
git add docs/adr/0042-exigences-et-tracabilite.md CLAUDE.md
git commit -m "docs(adr): record the requirements traceability decision

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Critère de fin de C3 : `make check` et `make test-integration` verts ; `GET /requirements/coverage` liste sur le catalogue réel les exigences sans réalisation et sans test ; le client OpenAPI est régénéré ; l'ADR existe.

---

# Partie 2 — Cadrage de A4, A3, C6

> **À détailler dans un plan dédié quand le sous-projet 6 est livré et que ce chantier est choisi.** Ce qui suit fixe, pour chacun, les questions à trancher par un spike à sortie documentée, puis les tâches d'implémentation au niveau titre + fichiers + interfaces clés + critère de fin, sans code. Un spike ne laisse aucun fichier de code dans le dépôt : ses essais vivent dans le répertoire scratchpad de la session, et sa sortie est un paragraphe « Mesures » dans le brouillon de l'ADR indiqué.

## Tâche transverse X0 — ADR « Notations étrangères : profils et vues, jamais un second métamodèle »

À écrire **avant tout code de A3 et A4** (spec §5, ADR n°1).
- **Fichiers :** `docs/adr/00NN-notations-etrangeres-profils-et-vues.md` (statut Proposition).
- **Contenu requis :** précise « ArchiMate seul » (CLAUDE.md, ADR 0005) sans le superséder ; interdit tout `ElementType` hors métamodèle ; fixe « dessin pur dans la vue, fait dans le graphe » ; consigne l'écart 1 (clés sans point) et l'écart 4 (profil sur élément seulement).
- **Critère de fin :** l'ADR est relu et accepté par l'utilisateur ; sa phrase de décision est citée par les ADR de A3 et A4.

## A4 — Modélisation de données (L)

**Attend :** sous-projet 6 (profil `erd`, gabarit de vue), X0 ; utile : sous-projets 1 et 3. **Peut commencer sans :** A4-S1 à S4 et A4.1.

### Spikes

| ID | Ce qu'il faut prouver | Preuve (commande ou fichier) | Critère de réussite | Sortie |
|---|---|---|---|---|
| A4-S1 | Les colonnes tiennent dans une propriété | Script scratchpad `uv run python -I` sérialisant 30 puis 100 colonnes (nom, type, nullable, pk, défaut) en JSON compact ; comparer à 500 car. (plafond spec 06 §3.6) | Une table réelle de 40 colonnes reste sous un plafond que l'on peut nommer ; sinon décision écrite : plafond spécifique à la clé `erd_columns`, ou clé non déclarée au profil. Rappel : `PATCH /elements` remplace toute la map `properties` (lecture-modification-écriture obligatoire) | ADR n°2 « Mesures » |
| A4-S2 | L'introspection suffit comme chemin principal | `make pg-up`, appliquer la chaîne Alembic du dépôt, puis requêter `information_schema` et `pg_catalog.pg_constraint` depuis un script scratchpad ; **oracle** : `Base.metadata.tables` de `backend/src/ea/db` | Tables, colonnes, types, clés primaires et clés étrangères (composites comprises) égales à celles de `Base.metadata` pour les ~12 tables du dépôt | ADR n°3 « Mesures » |
| A4-S3 | La cible d'introspection n'ouvre pas un SSRF | Relire `core/config.py`, `core/logging.py` (`RedactingFilter`) et `tests/conftest.py` (garde de socket) ; esquisser `EA_INTROSPECTION_TARGETS` (noms → hôte/port/base/rôle, mot de passe en variable d'environnement) | Décision écrite : une cible se **nomme**, son DSN n'est jamais dans une requête HTTP/MCP, jamais journalisé ; liste des menaces (SSRF, secret dans les journaux, écriture) avec parade pour chacune ; la session s'ouvre en `READ ONLY` | ADR n°3 |
| A4-S4 | Les noms de tables ne collisionnent pas | Scratch sur PG jetable : créer deux `data_object` `users` de schémas différents via l'API ; relire `uq_elements_element_type_name` et `Name` (200 car.) | Règle de nommage écrite (`schema.table`), comportement documenté sur la réimportation (idempotence via `DuplicateElementError`) | ADR n°2 |
| A4-S5 | Faut-il un parseur DDL | Prendre `pg_dump --schema-only` de la base du dépôt (jetable) ; évaluer à la main un sous-ensemble `CREATE TABLE`/`ALTER TABLE … FOREIGN KEY` ; recenser les constructions rencontrées | Verdict go/no-go écrit ; défaut **no-go** tant qu'A4-S2 réussit ; si go, choix argumenté entre un parseur écrit à la main et une dépendance (qui exigerait son propre ADR et `pip-audit`) | ADR n°3 |
| A4-S6 | La convention de relations est légale | Ajouter en tête d'A4.1 un test d'ancrage `permits(R.ASSOCIATION, E.DATA_OBJECT, E.DATA_OBJECT)` vrai et `permits(R.ACCESS, E.APPLICATION_COMPONENT, E.DATA_OBJECT)` faux ; vérifier que les clés `fk_columns`/`ref_columns` passent `_PROPERTY_KEY` | Les deux assertions passent (elles épinglent l'écart 3) ; décision sur la modification d'une relation (écart 4) | ADR n°2 |

### Tâches d'implémentation (à détailler dans un plan dédié quand le sous-projet 6 est livré et que ce chantier est choisi)

1. **A4.1 `domain/erd.py` pur.** Fichiers : `backend/src/ea/domain/erd.py`, `backend/tests/unit/test_erd.py`. Interfaces clés : `Column`, `Table`, `ForeignKey`, `parse_columns(properties: Mapping[str, str]) -> tuple[Column, ...]`, `serialize_columns(columns) -> str`, `generate_ddl(tables, foreign_keys) -> str` (ordre topologique, cycle de clés étrangères rompu par `ALTER TABLE … ADD CONSTRAINT`). Fin : un DDL regénéré depuis le schéma du dépôt s'exécute sur le PostgreSQL jetable et, ré-introspecté, redonne la même structure.
2. **A4.2 `SchemaImportService`.** Fichiers : `backend/src/ea/services/schema_import.py`, `backend/src/ea/domain/ports.py` (port `SchemaSource`), tests unitaires. Interfaces : `plan(source) -> ImportPlan(created, existing, refused)` et `apply(plan)` ; `require_editor()` en première instruction ; l'écriture passe par `ArchitectureService.create_element`/`connect`, jamais un dépôt ; `dry_run` par défaut ; ligne dans `WRITES` de `test_service_guards.py`. Fin : un second import identique ne crée rien et rapporte tout en `existing`.
3. **A4.3 Introspection PostgreSQL.** Fichiers : `backend/src/ea/repositories/pg_introspection.py`, `backend/src/ea/core/config.py` (cibles nommées), test d'intégration. Interface : `SchemaSource` sur une session `READ ONLY`. Fin : égalité avec `Base.metadata` (l'oracle d'A4-S2) en test d'intégration contre le conteneur jetable.
4. **A4.4 Surface API et MCP.** Fichiers : `backend/src/ea/api/schema_imports.py`, `backend/src/ea/api/schemas.py` (bornes), `backend/src/ea/mcp/server.py` (outil `import_schema`, annotation d'ajout), tests e2e et MCP. Fin : `make openapi-check` vert, outil dans l'assertion de liste complète, cible inconnue → 422 typé.
5. **A4.5 Panneau de colonnes et gabarit ERD.** Fichiers : `frontend/src/lib/erdGeometry.ts` (pur), `frontend/src/features/data/ColumnsPanel.vue`, specs Vitest. Fin : géométrie pied de corbeau testée sans montage ; une table de 40 colonnes s'affiche et s'édite ; le SPA ne copie aucun type SQL (la liste vient de l'API).
6. **A4.6 (conditionnelle à A4-S5 = go) Parseur DDL.** Fichiers : `backend/src/ea/domain/ddl_parser.py` + tests. Fin : toute construction hors du sous-ensemble documenté est refusée avec son motif, jamais devinée.
7. **A4.7 UML classes réduit (A3.3) puis ADR n°2 et n°3** — voir A3.

## A3 — Notations : BPMN (M) puis UML classes réduit (S)

**Attend :** sous-projet 6 (profils, conteneurs/couloirs, gabarits de vue), X0, **décision A3-S1** ; UML classes attend A4.1. **Peut commencer sans :** A3-S2, A3-S4.

### Spikes

| ID | Ce qu'il faut prouver | Preuve | Critère de réussite | Sortie |
|---|---|---|---|---|
| A3-S1 | Comment représenter une porte BPMN | Relire `Element.create` (`model.py:79`), `rules.permits` (une jonction hérite de toute relation sauf `specialization`), `taxonomy.py` (`ElementType.JUNCTION`, aspect `CONNECTOR`) et `/metamodel` ; scratch : confirmer le `ValueError` à la création | Décision écrite entre (a) autoriser `junction` comme élément avec une propriété `junction_type` (`and`/`or`/`xor`) — modification de `Element.create`, des règles de validation, de l'ADR 0005 par ADR superseding partiel —, (b) renoncer aux portes (branches multiples de `triggering` avec condition en propriété), (c) autre. **Validée par l'utilisateur** avant A3.1 et C6 | ADR n°1 (note) + ADR n°4 |
| A3-S2 | Le mapping BPMN → ArchiMate est légal | Test pur table-driven `backend/tests/unit/test_bpmn_mapping.py` (jetable, puis conservé si utile) : chaque paire `(relation, source, cible)` du mapping (événement de début → tâche, tâche → tâche, tâche → événement de fin, acteur/rôle assigné à un processus, flux de messages) passe `permits(...)` | Toutes les paires du périmètre retenu sont légales ; les illégales sont listées avec la parade | ADR n°4 « Mesures » |
| A3-S3 | Comment une vue « sait dessiner autrement » | Lire ce que le sous-projet 6 a livré (`diagram_templates`, groupes, `STEREOTYPE_ICONS`) ; comparer une colonne `notation` sur `diagrams` à un gabarit porté par le profil | Décision : où vit la notation, comment l'API sert la liste de formes (jamais copiée dans le SPA) | ADR n°1 (note) |
| A3-S4 | Le périmètre BPMN | Modéliser à la main un vrai processus de 10 à 20 étapes dans le SPA du sous-projet 6 | Liste signée par l'utilisateur des formes supportées (tâche, événement début/fin, porte exclusive/parallèle, couloir, flux de séquence) et libellé d'interface « BPMN simplifié, sémantique ArchiMate » | ADR n°4 |
| A3-S5 | Les classes UML réutilisent le modèle d'attributs d'A4 | Relire `domain/erd.py` (A4.1) | `Column` suffit pour des attributs, les opérations sont du texte ; sinon, besoin listé | ADR n°4 |

### Tâches d'implémentation (à détailler dans un plan dédié quand le sous-projet 6 est livré et que ce chantier est choisi)

1. **A3.1 Profil BPMN.** Fichiers : `backend/src/ea/domain/bpmn.py` (constructeur de document d'import, comme `requirements_profile()`, clés `bpmn_kind`…), tests. Interfaces : stéréotypes `Tâche` (`business_process`), `Événement de début`/`de fin` (`business_event`), `Porte` selon la décision A3-S1. Fin : le profil est accepté par `POST /profiles/import` et ses énumérations viennent d'un `StrEnum` unique.
2. **A3.2 Gabarit de vue BPMN.** Fichiers : `frontend/src/lib/bpmnShapes.ts` (table de présentation pure), `frontend/src/features/diagrams/` (extension du gabarit), specs Vitest ; couloirs = groupes du sous-projet 6. Fin : un processus de 10 à 20 étapes, couloirs compris, se dessine de bout en bout, sans type hors ArchiMate (critère d'arrêt de la spec).
3. **A3.3 Règle de qualité « une porte a une entrée et une sortie ».** Fichiers : `backend/src/ea/domain/quality.py` (code `bpmn_gate_arity`, sous-projet 4). Fin : cas positif, négatif et de bord testés ; constat visible dans `check_model`.
4. **A3.4 Profil `uml_class` et gabarit de classes (après A4.1).** Fichiers : `backend/src/ea/domain/uml.py`, `frontend/src/lib/classShapes.ts`. Fin : une classe avec attributs et opérations se dessine, `realization`/`specialization`/`composition` posent les liens du graphe.
5. **A3.5 ADR n°4 (BPMN) et ADR de la décision A3-S1**, puis phrase dans CLAUDE.md. Fin : `make check` vert.

## C6 — Simulation de processus (L)

**Attend :** A3 (portes, événements de début/fin), sous-projet 6 (profil `sim`), décision C6-S3 ; utile : sous-projet 4 (simulabilité). **Peut commencer sans :** C6-S1, C6-S2, C6.1.

### Spikes

| ID | Ce qu'il faut prouver | Preuve | Critère de réussite | Sortie |
|---|---|---|---|---|
| C6-S1 | Un moteur à événements discrets, déterministe, donne un résultat juste | Script scratchpad `heapq` + `random.Random(seed)` : une étape à un serveur, arrivées exponentielles, durée exponentielle ; comparer le temps d'attente moyen à la formule M/M/1 (puis M/M/c pour `capacity > 1`) | Écart < 5 % sur 100 000 jetons ; deux exécutions à graine égale sont identiques à l'octet | ADR n°5 « Mesures » |
| C6-S2 | Les bornes | Mesurer le temps d'exécution du prototype pour 1 000, 10 000 et 100 000 jetons sur un graphe de 20 étapes | `MAX_SIMULATION_TOKENS` et `MAX_SIMULATION_HORIZON_SECONDS` choisis pour répondre en moins d'une seconde ; à déclarer dans `api/schemas.py` | ADR n°5 |
| C6-S3 | Où vit `sim_probability` | Relire `api/architecture.py` (pas de `PATCH` de relation, écart 4) et la spec 06 (profils sur éléments seulement) | Décision écrite entre (a) `PATCH /relationships/{id}` (API, outil MCP, garde de service, ADR), (b) supprimer/recréer le lien, (c) probabilité portée par un attribut de l'élément cible. **Validée par l'utilisateur** | ADR n°5 |
| C6-S4 | Quand un graphe est simulable | Écrire sur deux processus modélisés (A3) la liste de règles : un seul départ, au moins une fin, pas de cul-de-sac, arité des portes, probabilités sommant à 1 sur une porte exclusive | Chaque règle a un exemple positif et négatif ; la règle commune est dans `domain/simulation.py`, le contrôle riche reste au sous-projet 4 | ADR n°5 |
| C6-S5 | Ce qu'est « le processus » | Comparer trois délimitations : éléments d'une vue (`view_of`), paquet (sous-projet 1), composante connexe par `triggering`/`flow` depuis un `business_process` | Une définition choisie, vérifiée sur un exemple, avec son plafond | ADR n°5 |
| C6-S6 | La précision illusoire | Définir « scénario illustratif » : vrai dès qu'une `sim_duration` manque | Règle écrite, et texte d'interface qui affiche les hypothèses à côté des résultats | ADR n°5 |

### Tâches d'implémentation (à détailler dans un plan dédié quand le sous-projet 6 est livré et que ce chantier est choisi)

1. **C6.1 Moteur pur.** Fichiers : `backend/src/ea/domain/simulation.py`, `backend/tests/unit/test_simulation.py`. Interfaces : `SimulationModel`, `Step`, `Gate`, `Arrival`, `SimulationResult`, `run(model: SimulationModel, *, seed: int, horizon_seconds: int, max_tokens: int) -> SimulationResult` ; horloge simulée entière, `random.Random(seed)`, aucune I/O. Fin : à graine fixe deux exécutions sont identiques ; l'écart au M/M/1 de C6-S1 est épinglé en test ; une porte parallèle et une porte exclusive ont leurs tests.
2. **C6.2 Lecture du sous-graphe, API, MCP.** Fichiers : `backend/src/ea/services/simulation.py` (`require_caller`, aucune écriture), `backend/src/ea/api/simulations.py`, `backend/src/ea/api/schemas.py` (bornes), `backend/src/ea/mcp/server.py` (outil `simulate_process`, `READS`), tests. Fin : bornes importées par les deux adaptateurs et ajoutées à `TestTheSameBoundsAsTheHttpAdapter` ; un graphe non simulable est refusé en 422 avec le motif ; le goulot est correct sur trois processus réels, reproductible à graine fixe (critère d'arrêt de la spec).
3. **C6.3 Saisie des paramètres.** Fichiers : `backend/src/ea/domain/simulation.py` (constructeur de profil `sim`), panneau du profil côté SPA, et, selon C6-S3, `PATCH /relationships/{id}`. Fin : durée, capacité, arrivée et probabilités se saisissent et se relisent ; les valeurs sont validées côté API.
4. **C6.4a Tableau de résultats.** Fichiers : `frontend/src/features/simulation/`. Fin : tableau par étape (attente moyenne, occupation, file maximale) et globaux (débit, temps de cycle moyen et p90, goulot), avec les hypothèses saisies à côté et le bandeau « scénario illustratif » selon C6-S6.
5. **C6.4b (décision 4 : à abandonner si le tableau suffit) Animation.** Fichiers : `frontend/src/lib/simulationReplay.ts` (pur, rejoue la liste d'événements horodatés déjà calculée). Fin : vitesse réglable, aucun calcul côté navigateur.
6. **C6.5 ADR n°5 « Moteur de simulation pur »** (graine injectée, horloge simulée, bornes, aucune écriture), phrase dans CLAUDE.md. Fin : `make check` vert.

---

## Auto-revue (corrigée en ligne)

- **Couverture de la spec :** C3 (profil T8, couverture T1 à T4, MCP T5, SPA T7, matrice T9) ; A4, A3, C6 en cadrage avec leurs lots ; l'abandon de SysML/séquence/états est en « Décisions à valider », pas en tâche ; les cinq ADR de la spec §5 sont attribués (n°1 = X0, n°2 et n°3 = A4, n°4 = A3, n°5 = C6) et celui de C3 est écrit en entier (T10).
- **Corrigé après vérification du code :** la spec voulait `req.status` (clé illégale), des portes en `junction` (refusées par `Element.create`), un `access` composant → donnée (refusé par `_permits_access`), une modification de lien (inexistante) et une exclusion des tests de l'impact (inutile selon `ALONG_THE_ARROW`) ; chacun est dans « Écarts constatés » et traité dans les tâches concernées.
- **Noms vérifiés dans le code réel :** `Element.create`, `Relationship.between`, `permits`, `ElementType.aspect`, `GraphView`, `ElementRecord`/`RelationshipRecord.target_type`, `element_from_row`, `relationship_from_row`, `require_caller`, `READS`, `speaking_plainly`, `build_mcp_server` (4 fournisseurs), `create_app(ipam=…)`, `WRITES` de `test_service_guards.py`, `InMemoryRepository`, fixtures `service`/`repository`/`nobody_calling`, `stubApi`, `anElement`, `useLatestRequest().run`, `createAppRouter`, `SECTIONS`.
- **Risque assumé :** les Tasks 8 et 9 s'appuient sur des interfaces des sous-projets 4 et 6 qui n'existent pas encore dans l'arbre ; elles sont donc rédigées contre leurs specs, avec des tests qui échouent si la forme réelle diffère, et la Task 0 sert de garde.
- **Aucun** « TBD », « TODO », « gestion d'erreurs appropriée » ni « similaire à la tâche N » dans la partie détaillée.

---

## Décisions validées par l'utilisateur (2026-10-10)

1. **`junction` est autorisé** à la création (portes BPMN, ET/OU de processus) : l'élément cesse d'être refusé par `Element.create`; la modification passe par une tâche dédiée avec ADR dans le chantier A3/C6.
2. **`PATCH /relationships/{id}`** est ajouté (propriétés d'une relation modifiables), service + API + MCP + client régénéré; prérequis de `sim_probability` et `erd_fk_columns`.
3. **Introspection de base (A4)** : cibles nommées en configuration (`EA_INTROSPECTION_TARGETS`), jamais un DSN dans la requête (SSRF); la requête ne porte que le nom de la cible.
