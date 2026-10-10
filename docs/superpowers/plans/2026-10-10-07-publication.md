# Publication (rapports D2, publication web D3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Générer des rapports (HTML autonome, PDF, DOCX) d'un diagramme (puis d'un paquet ou d'une requête enregistrée quand le sous-projet 1 existe), les déposer dans le bucket sous `reports/`, et offrir un mode lecteur `/view/*` du SPA en lecture seule.

**Architecture:** Un seul document intermédiaire neutre (`ReportModel`) construit par une fonction pure du domaine à partir d'un périmètre lu par `ReportService`; trois sérialiseurs (HTML pur dans le domaine, PDF et DOCX dans `repositories/` parce qu'ils exigent des bibliothèques natives). Un seul dessin statique de diagramme (`domain/diagram_render.py`), comparé à la géométrie de l'éditeur SPA par un test de parité sur une fixture JSON partagée. Le service ne persiste que par `FileService.upload` (une seule porte, jamais d'URL présignée). Aucune table, aucune migration.

**Tech Stack:** FastAPI + services/domain existants; `markdown-it-py` (rendu markdown sûr), `weasyprint` (PDF, lot 2), `python-docx` (DOCX, lot 4); Vue 3 + Vitest côté SPA; Docker (paquets Debian Pango) pour l'image API.

**Spec:** `docs/superpowers/specs/2026-10-10-07-publication-design.md`

---

## Écarts constatés avec la spec (le plan suit le code réel)

1. **Sous-projets 1 et 6 absents du dépôt** (aucun `packages`, aucune `saved_queries`, diagrammes v1 seulement: `DiagramDetail` = `diagram`, `nodes` (x, y, width, height), `graph: GraphView`). Le lot 1 livre donc le sujet `diagram` seul. Le modèle « Inventaire d'un paquet » et les pages `/view/package/:id` sont des tâches gardées (Task 11, Task 25) qui commencent par vérifier que le sous-projet 1 est livré. Le sujet `query` n'a aucune tâche: sans `saved_queries`, il n'y a rien à exécuter.
2. **`DiagramView` neutre = `DiagramDetail` existant.** `domain/diagrams.py` définit déjà un objet pur qui porte tout ce que le dessin lit; `render_svg(detail: DiagramDetail)`. Le sous-projet 6 étendra ce type, pas un doublon.
3. **Pas de liste de chemins autorisés dans `domain/files.py`** (seulement `clean_key`, qui refuse les chemins ambigus). Le préfixe `reports/` passe sans modification du domaine des fichiers, et apparaît dans le navigateur de fichiers sans code SPA.
4. **La route du navigateur de fichiers est `/fichiers`** (pas `/files`): le renvoi après dépôt va vers `/fichiers?prefix=reports/`.
5. **`build_mcp_server` prend quatre fournisseurs positionnels** (`get_service`, `get_documents`, `get_ipam`, `get_files`). Ajouter les deux outils exige un cinquième fournisseur `get_reports`, obligatoire comme les autres (règle de CLAUDE.md: la liste d'outils appartient à l'adaptateur). Deux appelants de test et `main._mount_mcp` changent.
6. **Les modèles sont des dataclasses figées du domaine, pas du Pydantic.** Tout `domain/` est en dataclasses (aucun import de bibliothèque de validation). La forme reste celle qu'une table stockerait (champ `version`, sections discriminées par `kind`); la validation Pydantic `extra="forbid"` n'arrive qu'avec le lot 5, côté `api/`.
7. **Le rendeur markdown vit dans `domain/markdown_html.py`** (fonction pure, sans E/S, `markdown-it-py` déjà dans `uv.lock` via `rich`): `api/documents.py`, `domain/report_html.py` et les sérialiseurs l'importent sans traverser une frontière. Les rendus PDF/DOCX, eux, sont dans `repositories/` (bibliothèques natives).
8. **`LAYER_COLOURS` du SPA reste une constante synchrone** (cinq composants la lisent au rendu; la rendre asynchrone par `/metamodel` serait une refonte sans bénéfice). La « source unique » est tenue par un test: la palette du domaine (`LAYER_FILL`, `BOX_INK`) est exposée par `/metamodel` ET comparée à la constante du SPA par la fixture de parité. Une divergence casse un test des deux côtés.
9. **Sections livrées: `text`, `element_table`, `diagram`, `relations`, `documents`.** `matrix` n'est utilisée par aucun modèle intégré: elle n'est pas écrite (YAGNI, pas de squelette); l'ajouter plus tard = une dataclass + une branche du constructeur.
10. **`/view` comme « route d'arrivée proposée aux comptes sans `ea-editor` »** n'est pas câblée en redirection automatique: le test existant `router.spec.ts` attend que `/` atterrisse synchronieusement sur `HOME`. La section « Consultation » est dans le menu; la redirection par rôle est consignée dans l'ADR du lot 3 comme suite possible.
11. **Décision PNG/`cairosvg` (spec: « prise au lot 3 ») est prise au lot 4**, là où le DOCX est livré. Défaut du plan: repli sans `cairosvg` (pas de bibliothèque cairo de plus dans l'image).
12. **Le temps de génération est borné par une horloge monotone injectée**, vérifiée entre les étapes, plus `asyncio.wait_for` autour du thread. Un thread bloqué dans WeasyPrint ne peut pas être tué (limite documentée dans le code).
13. **Vérification du build d'image: sur la machine de l'utilisateur.** L'agent qui exécute ce plan n'a pas Docker (ni les bibliothèques Pango): les tests PDF se sautent proprement si WeasyPrint ne se charge pas, et la Task 16 donne les commandes à lancer par l'utilisateur.

## Global Constraints

- Dépendances: `api → services → domain ← repositories`; `mcp/` à côté de `api/`, jamais un client de `api/`. `domain/` n'importe ni FastAPI, ni SQLAlchemy, ni E/S.
- Toute méthode publique d'un service commence par `require_caller()` (lecture) ou `require_editor()` (écriture); `tests/unit/test_service_guards.py` le vérifie par AST (ajouter `ReportService` à `WRITES`).
- Lecture d'un rapport (`GET /reports/render`): tout appelant authentifié, rien n'est écrit. Dépôt (`POST /reports`, `generate_report`): `ea-editor` seulement.
- Plafonds exacts: `MAX_REPORT_ELEMENTS = 500`, `MAX_REPORT_DIAGRAMS = 20`, `MAX_REPORT_DOCUMENT_BYTES = 2 * 1024 * 1024` (cumulés), `REPORT_TIMEOUT_SECONDS = 30`. Dépassement = `ReportTooLargeError` → 422 `report_too_large`, jamais une troncature.
- Un seul rapport simultané par `sub` du jeton; au-delà `ReportBusyError` → 429 `report_busy`. Aucun cache de rendu partagé.
- Format indisponible (PDF/DOCX sans bibliothèque): `ReportFormatUnavailableError` → 501 `format_unavailable`; le HTML reste toujours livrable.
- Clé de dépôt exacte: `reports/<AAAA>/<AAAA-MM-JJ>-<slug-du-sujet>-<template>-<id8>.<ext>`; dépôt par `FileService.upload` seulement (ADR 0036), jamais d'URL présignée; ordre objet puis ligne (ADR 0039, déjà dans `upload`).
- Fiche `file_metadata` du dépôt: `title` = « Rapport <modèle> — <sujet> » (≤ 200 caractères), `description` = sujet (kind + id) + version du modèle + version de l'application, `tags` = `["rapport", template_id]`.
- Tout texte saisi est échappé (HTML/XML); le markdown passe par `render_markdown` (HTML brut désactivé, images désactivées, schémas `javascript:`/`data:` refusés); pas de gabarit évalué: substitution `{{subject.name|description|kind}}` par table fermée.
- WeasyPrint: récupérateur d'URL qui refuse tout (réseau, fichier, `data:`).
- `GET /diagrams/{id}/render.svg`: `Content-Type: image/svg+xml`, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`, `X-Content-Type-Options: nosniff`, `ETag` = hachage du contenu rendu, `Cache-Control: private, no-cache`.
- Les téléchargements de rapport sont toujours `Content-Disposition: attachment` avec nom assaini (`attachment()` de `api/files.py`), `nosniff`, CSP `default-src 'none'; style-src 'unsafe-inline'; sandbox`.
- Journal: une ligne `action=report_generated` (`template`, `subject`, `report_format`, `bytes`, `duration_ms`, `deposited`) dans `services/` (ADR 0021).
- Bornes d'entrée déclarées une fois dans `api/schemas.py` et importées par `mcp/server.py`; l'assertion de liste complète d'outils de `tests/unit/test_mcp_server.py` passe de 34 à 36 noms; `generate_report` ne renvoie jamais d'octets (plafond MCP `MAX_MCP_UPLOAD_BYTES` = 1 MiB).
- Contrat OpenAPI: `make openapi` après tout changement de schéma; `make openapi-check` vert; jamais d'interface TypeScript écrite à la main pour un modèle Pydantic.
- Le SPA ne contient aucune copie du métamodèle; `/view/*` n'émet aucune requête POST/PUT/PATCH/DELETE; l'API reste l'autorité.
- Chaque nouvelle dépendance a son ADR (`markdown-it-py`, `weasyprint`, `python-docx`), chaque ADR référence 0027, 0032, 0036 et 0039. Numéros d'ADR réservés par ce plan: 0042 et 0043 (lot 1), 0044 (lot 3); vérifier qu'ils sont libres avant la Task 1 (`ls docs/adr | tail -3`), sinon décaler les trois et corriger les références.
- TDD: test d'abord, échec constaté pour la bonne raison, puis le code minimal. Pas de `time.sleep`; horloges injectées. Aucun test ne quitte la machine (garde `socket`).
- Commandes: backend `cd backend && uv run pytest tests/unit/<fichier> -x` (sans `--cov`); frontend `cd frontend && npm test -- --run tests/<fichier>`; portail final `make check`.
- Commits: Conventional Commits en anglais, terminés par `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; ne jamais `--no-verify`.

## Review Focus

1. **Nom d'élément contenant du balisage** (`<script>`, `&`, `"`, `]]>`, caractères de contrôle `\x00`/`\x0b`): chaque sérialiseur a son test d'échappement: SVG (Task 2), HTML (Task 6), rapport complet de bout en bout (Task 7), DOCX (Task 29). Le texte substitué dans le markdown d'un modèle est échappé en markdown (Task 5).
2. **Rapport vide / diagramme sans éléments**: SVG vide valide (Task 2), phrases « Aucun élément. / Aucune relation. / Aucun document. » du constructeur (Task 5), 200 de bout en bout sur un diagramme vierge (Task 9).
3. **Caractères non latins dans le PDF** (grec, cyrillique, CJK): le rendu ne lève pas et produit un `%PDF` (Task 17); DejaVu couvre latin, grec, cyrillique; le CJK s'affiche en carrés vides, limite écrite dans l'ADR du lot 1.
4. **Lecteur qui tente `POST /reports`** (et `generate_report` en MCP, et la boîte du SPA): refus 403 / `ToolError`, aucun objet écrit (Task 8, Task 9, Task 19, Task 20).
5. **Plafonds et concurrence**: 501 éléments → 422 sans objet dans le bucket (Task 7, Task 12); délai dépassé par horloge injectée (Task 7); second rapport du même appelant → 429 (Task 7); document supprimé pendant le rendu → rapport cohérent sans lui (Task 7).

---

## Carte des fichiers

**Créés (backend)**
- `backend/src/ea/domain/diagram_render.py`: palette, géométrie, `render_svg`.
- `backend/src/ea/domain/markdown_html.py`: `render_markdown`.
- `backend/src/ea/domain/reports.py`: plafonds, gabarits, `ReportModel`, `build_model`, noms de fichier.
- `backend/src/ea/domain/report_html.py`: `to_html`.
- `backend/src/ea/services/reports.py`: `ReportService`.
- `backend/src/ea/api/reports.py`: routeur `/report-templates`, `/reports/render`, `/reports`.
- `backend/src/ea/repositories/report_renderers.py`: détection des formats, rendu PDF (lot 2) et DOCX (lot 4).
- Tests: `tests/unit/test_report_errors.py`, `test_diagram_render.py`, `test_markdown_html.py`, `test_reports_domain.py`, `test_report_html.py`, `test_report_service.py`, `test_report_renderers.py`; `tests/e2e/test_report_api.py`; `tests/integration/test_reports_deposit.py`.
- `frontend/tests/fixtures/diagram-parity.json`, `frontend/tests/diagramParity.spec.ts`.
- Lot 2: `frontend/src/features/reports/useReport.ts`, `ReportDialog.vue`, `frontend/tests/useReport.spec.ts`, `ReportDialog.spec.ts`.
- Lot 3: `frontend/src/features/view/ViewShell.vue`, `ViewHome.vue`, `ViewElement.vue`, `ViewDiagram.vue`, `frontend/tests/view.spec.ts`.
- ADR: `docs/adr/0042-generation-de-documents.md`, `0043-un-seul-dessin-de-diagramme.md` (lot 1), `0044-publication-en-lecture.md` (lot 3).

**Modifiés**
- `backend/src/ea/domain/errors.py`, `backend/src/ea/api/errors.py`, `backend/src/ea/api/schemas.py`, `backend/src/ea/api/dependencies.py`, `backend/src/ea/api/diagrams.py`, `backend/src/ea/api/documents.py`, `backend/src/ea/main.py`, `backend/src/ea/mcp/server.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/Dockerfile`, `.github/workflows/ci.yml`.
- `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés).
- `backend/tests/unit/test_service_guards.py`, `test_mcp_server.py`.
- `frontend/src/lib/diagramGeometry.ts`, `frontend/src/features/diagrams/DiagramCanvas.vue`, `DiagramsSection.vue`, `frontend/src/router/sections.ts`, `frontend/src/router/index.ts`, `frontend/src/App.vue`.
- `CLAUDE.md` (une phrase, Task 33).

---

# LOT 1: socle et HTML

### Task 1: Erreurs de domaine et statuts HTTP

**Files:**
- Modify: `backend/src/ea/domain/errors.py` (ajout en fin de fichier, après `FileStorageUnavailableError`, ~l.143-150)
- Modify: `backend/src/ea/api/errors.py` (imports l.23-50; table `_STATUS` l.59-108, avant le bloc `# --- Authentication`)
- Create: `backend/tests/unit/test_report_errors.py`

**Interfaces:**
- Consumes: `DomainError` (`ea.domain.errors`), `register_error_handlers` (`ea.api.errors`).
- Produces: `ReportTooLargeError`, `ReportFormatUnavailableError`, `ReportBusyError`, `UnknownReportTemplateError`, `UnsupportedReportSubjectError` (toutes `DomainError`), mappées 422 `report_too_large`, 501 `format_unavailable`, 429 `report_busy`, 404 `not_found`, 422 `unsupported_subject`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
"""Each report refusal reaches a client as its own typed status."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ea.api.errors import register_error_handlers
from ea.domain.errors import (
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnknownReportTemplateError,
    UnsupportedReportSubjectError,
)

pytestmark = pytest.mark.asyncio

CASES = {
    "too-large": (ReportTooLargeError, 422, "report_too_large"),
    "no-format": (ReportFormatUnavailableError, 501, "format_unavailable"),
    "busy": (ReportBusyError, 429, "report_busy"),
    "no-template": (UnknownReportTemplateError, 404, "not_found"),
    "no-subject": (UnsupportedReportSubjectError, 422, "unsupported_subject"),
}


@pytest.mark.parametrize("name", CASES)
async def test_a_report_refusal_has_its_own_status_and_code(name: str) -> None:
    error, expected_status, expected_code = CASES[name]
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error("the ceiling is 500 elements")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == expected_status
    assert response.json()["error"] == expected_code
    assert "500 elements" in response.json()["detail"]
```

- [ ] **Step 2: Lancer, échec attendu** (`ImportError: cannot import name 'ReportBusyError'`)

Run: `cd backend && uv run pytest tests/unit/test_report_errors.py -x`

- [ ] **Step 3: Implémenter**

Ajouter en fin de `backend/src/ea/domain/errors.py`:

```python
class ReportTooLargeError(DomainError):
    """A report would pass a ceiling: elements, diagrams, document bytes or time.

    The message names the ceiling and how to narrow the scope. A silent
    truncation would hand over a report that looks complete and is not.
    """


class ReportFormatUnavailableError(DomainError):
    """The format needs a native library this deployment does not carry."""


class ReportBusyError(DomainError):
    """The caller already has a report being built; one at a time."""


class UnknownReportTemplateError(DomainError):
    """No built-in template has this id."""


class UnsupportedReportSubjectError(DomainError):
    """This template does not take this kind of subject, or the kind is not delivered."""
```

Dans `backend/src/ea/api/errors.py`, ajouter à l'import (ordre alphabétique) `ReportBusyError, ReportFormatUnavailableError, ReportTooLargeError`, `UnknownReportTemplateError` (après `StoredFileNotFoundError`... garder l'ordre ruff `I`), `UnsupportedReportSubjectError`, puis dans `_STATUS`, juste avant `# --- Authentication (docs/adr/0032) ---`:

```python
    # --- Reports (docs/adr/0042) ---
    ReportTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "report_too_large"),
    ReportFormatUnavailableError: (status.HTTP_501_NOT_IMPLEMENTED, "format_unavailable"),
    ReportBusyError: (status.HTTP_429_TOO_MANY_REQUESTS, "report_busy"),
    UnknownReportTemplateError: (status.HTTP_404_NOT_FOUND, "not_found"),
    UnsupportedReportSubjectError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "unsupported_subject"),
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_errors.py -x && uv run ruff check src tests && uv run ruff format src tests`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/errors.py backend/src/ea/api/errors.py backend/tests/unit/test_report_errors.py
git commit -m "feat(reports): typed refusals for ceilings, formats, and concurrency

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Le dessin SVG unique (`domain/diagram_render.py`)

**Files:**
- Create: `backend/src/ea/domain/diagram_render.py`
- Create: `backend/tests/unit/test_diagram_render.py`

**Interfaces:**
- Consumes: `DiagramDetail`, `DiagramNode` (`ea.domain.diagrams`); `Element`, `Relationship` (`ea.domain.model`); `Layer`, `RelationshipType`, `AccessType` (`ea.domain.archimate`).
- Produces:
  - `LAYER_FILL: Mapping[Layer, str]`, `BOX_INK: str`, `CANVAS_MARGIN: float`, `RELATIONSHIP_VERBS: Mapping[RelationshipType, str]`, `ACCESS_VERBS: Mapping[str, str]`
  - `shorten(name: str, width: float) -> str`
  - `xml_safe(text: str) -> str` (retire les caractères interdits en XML 1.0; ne fait pas d'échappement)
  - `verb_of(relationship: Relationship) -> str`
  - `render_svg(detail: DiagramDetail) -> str`

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""The one static drawing of a diagram: exact, deterministic, escaped."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import UTC, datetime

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.diagram_render import (
    LAYER_FILL,
    RELATIONSHIP_VERBS,
    render_svg,
    shorten,
    xml_safe,
)
from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
FONT = 'font-family="DejaVu Sans, sans-serif"'
ARROW = (
    '<defs><marker id="ea-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M 0 0 L 10 5 L 0 10 z" fill="#4a4455"/></marker></defs>'
)


def an_element(name: str = "Facturation", kind: ElementType = ElementType.APPLICATION_COMPONENT):
    return Element.create(element_type=kind, name=name, now=NOW)


def a_link(source: Element, target: Element) -> Relationship:
    return Relationship.between(RelationshipType.ASSOCIATION, source, target, now=NOW)


def a_detail(
    placed: Sequence[tuple[Element, float, float, float, float]],
    links: Sequence[Relationship] = (),
    name: str = "Vente",
) -> DiagramDetail:
    return DiagramDetail(
        diagram=Diagram.create(name=name, now=NOW),
        nodes=tuple(DiagramNode(e.id, x, y, w, h) for e, x, y, w, h in placed),
        graph=GraphView(
            elements=tuple(e for e, *_ in placed), relationships=tuple(links)
        ),
    )


def test_every_layer_has_a_fill_and_every_relationship_a_verb() -> None:
    assert set(LAYER_FILL) == set(Layer)
    assert set(RELATIONSHIP_VERBS) == set(RelationshipType)


def test_a_name_is_shortened_by_the_editors_rule() -> None:
    assert shorten("court", 132) == "court"
    assert shorten("x" * 40, 132) == "x" * 16 + "…"
    assert shorten("abcdef", 20) == "abc…"


def test_one_box_is_drawn_exactly_where_it_is_stored() -> None:
    facturation = an_element()

    svg = render_svg(a_detail([(facturation, 40, 40, 132, 46)]))

    assert svg == "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" '
            'height="600" role="img" aria-label="Vente">',
            "<title>Vente</title>",
            ARROW,
            f'<g class="node" data-element-id="{facturation.id}">',
            "<title>Facturation — Application Component</title>",
            '<rect x="40" y="40" width="132" height="46" rx="6" fill="#b5ffff" '
            'stroke="#24202b" stroke-opacity="0.35"/>',
            f'<text class="name" x="106" y="61" text-anchor="middle" {FONT} '
            'font-size="13" fill="#24202b">Facturation</text>',
            f'<text class="type" x="106" y="76" text-anchor="middle" {FONT} '
            'font-size="10" fill="#24202b">Application Comp…</text>',
            "</g>",
            "</svg>",
        ]
    )


def test_two_calls_give_the_same_string() -> None:
    detail = a_detail([(an_element(), 10, 10, 132, 46)])

    assert render_svg(detail) == render_svg(detail)


def test_a_link_is_trimmed_to_the_borders_and_labelled() -> None:
    left, right = an_element("A"), an_element("B")

    svg = render_svg(a_detail([(left, 0, 0, 100, 40), (right, 400, 0, 100, 40)], [a_link(left, right)]))

    assert '<line x1="100" y1="20" x2="400" y2="20"' in svg
    assert 'x="250" y="16"' in svg
    assert ">est associé à</text>" in svg


def test_overlapping_boxes_have_no_stroke() -> None:
    left, right = an_element("A"), an_element("B")

    svg = render_svg(a_detail([(left, 0, 0, 100, 40), (right, 50, 10, 100, 40)], [a_link(left, right)]))

    assert "<line" not in svg


def test_a_link_to_an_element_that_is_not_drawn_is_skipped() -> None:
    drawn, absent = an_element("A"), an_element("B")

    svg = render_svg(a_detail([(drawn, 0, 0, 100, 40)], [a_link(drawn, absent)]))

    assert "<line" not in svg
    ET.fromstring(svg)


def test_an_empty_diagram_is_a_valid_blank_canvas() -> None:
    root = ET.fromstring(render_svg(a_detail([])))

    assert root.get("viewBox") == "0 0 800 600"
    assert root.findall(".//{http://www.w3.org/2000/svg}g") == []


def test_the_canvas_grows_to_hold_a_far_box() -> None:
    root = ET.fromstring(render_svg(a_detail([(an_element(), 2000, 100, 200, 50)])))

    assert root.get("viewBox") == "0 0 2360 600"


def test_markup_in_a_name_is_text_and_never_markup() -> None:
    nasty = an_element('<script>alert(1)</script> & "x" ]]>')

    svg = render_svg(a_detail([(nasty, 10, 10, 400, 46)], name='<img src=x onerror=1> "d"'))

    assert "<script" not in svg
    assert "<img" not in svg
    assert "&lt;script&gt;" in svg
    assert "&amp;" in svg
    assert "&quot;" in svg
    root = ET.fromstring(svg)  # still well-formed XML
    titles = [t.text for t in root.iter("{http://www.w3.org/2000/svg}title")]
    assert '<script>alert(1)</script> & "x" ]]> — Application Component' in titles


def test_characters_xml_forbids_are_dropped() -> None:
    assert xml_safe("A\x00B\x0bC\x1fD") == "ABCD"
    svg = render_svg(a_detail([(an_element("A\x00B\x0bC"), 0, 0, 132, 46)]))

    ET.fromstring(svg)
    assert "ABC" in svg
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError: ea.domain.diagram_render`)

Run: `cd backend && uv run pytest tests/unit/test_diagram_render.py -x`

- [ ] **Step 3: Implémenter `backend/src/ea/domain/diagram_render.py`**

```python
"""The one static drawing of a diagram, as an SVG string.

A report embeds it, `GET /diagrams/{id}/render.svg` serves it, and the reader
mode shows it. It is *the* static drawing: the interactive editor keeps its own
TypeScript geometry for dragging, and a parity test (`test_diagram_render.py`
against `frontend/tests/diagramParity.spec.ts`, one JSON fixture) keeps the two
at the same coordinates. Changing a number here without the editor's breaks one
of the two tests.

Pure and deterministic: same detail, same string. No clock, no randomness, no
`<style>` element (presentation attributes only, so a strict CSP is enough).
Every text from a person goes through `_esc`. See docs/adr/0043.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from ea.domain.archimate import Layer, RelationshipType
from ea.domain.diagrams import DiagramDetail
from ea.domain.model import Relationship

#: The ArchiMate layer fills. The SPA's `LAYER_COLOURS` must equal this mapping
#: (checked through the shared parity fixture) and `/metamodel` serves it.
LAYER_FILL: Final[Mapping[Layer, str]] = MappingProxyType(
    {
        Layer.MOTIVATION: "#ccccff",
        Layer.STRATEGY: "#f5deaa",
        Layer.BUSINESS: "#ffffb5",
        Layer.APPLICATION: "#b5ffff",
        Layer.TECHNOLOGY: "#c9e7b7",
        Layer.PHYSICAL: "#afdfa0",
        Layer.IMPLEMENTATION_MIGRATION: "#ffe0e0",
        Layer.OTHER: "#e8e6ec",
    }
)
#: The ink on those fills, never the page's colour.
BOX_INK: Final = "#24202b"
EDGE_INK: Final = "#4a4455"

#: Same numbers as `lib/diagramGeometry.ts` (MIN_CANVAS, CANVAS_MARGIN, CHARACTER_WIDTH).
MIN_CANVAS_WIDTH: Final = 800.0
MIN_CANVAS_HEIGHT: Final = 600.0
CANVAS_MARGIN: Final = 160.0
CHARACTER_WIDTH: Final = 7.5

#: Same words as the SPA's `features/relationships/labels.ts`: a translation
#: table, not a rule.
RELATIONSHIP_VERBS: Final[Mapping[RelationshipType, str]] = MappingProxyType(
    {
        RelationshipType.COMPOSITION: "compose",
        RelationshipType.AGGREGATION: "agrège",
        RelationshipType.ASSIGNMENT: "est affecté à",
        RelationshipType.REALIZATION: "réalise",
        RelationshipType.SERVING: "sert",
        RelationshipType.ACCESS: "accède à",
        RelationshipType.INFLUENCE: "influence",
        RelationshipType.ASSOCIATION: "est associé à",
        RelationshipType.TRIGGERING: "déclenche",
        RelationshipType.FLOW: "alimente",
        RelationshipType.SPECIALIZATION: "spécialise",
    }
)
ACCESS_VERBS: Final[Mapping[str, str]] = MappingProxyType(
    {"access": "accède", "read": "lit", "write": "écrit", "read_write": "lit et écrit"}
)

_FONT: Final = 'font-family="DejaVu Sans, sans-serif"'
_ARROW: Final = (
    '<defs><marker id="ea-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{EDGE_INK}"/></marker></defs>'
)
#: What XML 1.0 forbids: C0 controls other than tab, newline, carriage return.
_XML_ILLEGAL: Final = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

type Box = tuple[float, float, float, float]


def xml_safe(text: str) -> str:
    """The text without the characters no XML document may hold."""
    return _XML_ILLEGAL.sub("", text)


def _esc(text: str) -> str:
    return html.escape(xml_safe(text), quote=True)


def _n(value: float) -> str:
    """A coordinate in its shortest exact form: `40`, `61.5`, never `40.0` or `-0`."""
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def shorten(name: str, width: float) -> str:
    """As much of `name` as a box `width` wide holds (the editor's `shorten`)."""
    limit = max(4, math.floor(width / CHARACTER_WIDTH))
    return f"{name[: limit - 1]}…" if len(name) > limit else name


def verb_of(relationship: Relationship) -> str:
    if relationship.relationship_type is RelationshipType.ACCESS and relationship.access_type:
        return ACCESS_VERBS[relationship.access_type.value]
    return RELATIONSHIP_VERBS[relationship.relationship_type]


def _centre(box: Box) -> tuple[float, float]:
    x, y, width, height = box
    return x + width / 2, y + height / 2


def _clip(origin: tuple[float, float], box: Box) -> tuple[float, float]:
    """Where the segment from `origin` to the centre of `box` crosses its border."""
    cx, cy = _centre(box)
    dx, dy = origin[0] - cx, origin[1] - cy
    scale = min(
        math.inf if dx == 0 else box[2] / 2 / abs(dx),
        math.inf if dy == 0 else box[3] / 2 / abs(dy),
    )
    if math.isinf(scale):
        return cx, cy
    return cx + dx * scale, cy + dy * scale


def _segment(source: Box, target: Box) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The stroke between two boxes, or `None` when they overlap (the editor's rule)."""
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    if sx < tx + tw and tx < sx + sw and sy < ty + th and ty < sy + sh:
        return None
    return _clip(_centre(target), source), _clip(_centre(source), target)


def render_svg(detail: DiagramDetail) -> str:
    """The diagram as a standalone SVG document (no XML declaration, UTF-8)."""
    elements = {element.id: element for element in detail.graph.elements}
    drawn = [(node, elements[node.element_id]) for node in detail.nodes if node.element_id in elements]
    boxes: dict[object, Box] = {
        node.element_id: (node.x, node.y, node.width, node.height) for node, _ in drawn
    }
    width = max([MIN_CANVAS_WIDTH, *(x + w + CANVAS_MARGIN for x, _, w, _ in boxes.values())])
    height = max([MIN_CANVAS_HEIGHT, *(y + h + CANVAS_MARGIN for _, y, _, h in boxes.values())])

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(width)} {_n(height)}" '
        f'width="{_n(width)}" height="{_n(height)}" role="img" '
        f'aria-label="{_esc(detail.diagram.name)}">',
        f"<title>{_esc(detail.diagram.name)}</title>",
        _ARROW,
    ]
    for link in detail.graph.relationships:
        source, target = boxes.get(link.source_id), boxes.get(link.target_id)
        segment = _segment(source, target) if source and target else None
        if segment is None:
            continue
        (x1, y1), (x2, y2) = segment
        lines += [
            f'<g class="edge" data-relationship-id="{link.id}">',
            f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{EDGE_INK}" stroke-width="1.5" marker-end="url(#ea-diagram-arrow)"/>',
            f'<text x="{_n((x1 + x2) / 2)}" y="{_n((y1 + y2) / 2 - 4)}" text-anchor="middle" '
            f'{_FONT} font-size="11" fill="{EDGE_INK}">{_esc(verb_of(link))}</text>',
            "</g>",
        ]
    for node, element in drawn:
        kind = element.element_type
        cx = node.x + node.width / 2
        cy = node.y + node.height / 2
        lines += [
            f'<g class="node" data-element-id="{element.id}">',
            f"<title>{_esc(element.name)} — {_esc(kind.label)}</title>",
            f'<rect x="{_n(node.x)}" y="{_n(node.y)}" width="{_n(node.width)}" '
            f'height="{_n(node.height)}" rx="6" fill="{LAYER_FILL[kind.layer]}" '
            f'stroke="{BOX_INK}" stroke-opacity="0.35"/>',
            f'<text class="name" x="{_n(cx)}" y="{_n(cy - 2)}" text-anchor="middle" {_FONT} '
            f'font-size="13" fill="{BOX_INK}">{_esc(shorten(element.name, node.width))}</text>',
            f'<text class="type" x="{_n(cx)}" y="{_n(cy + 13)}" text-anchor="middle" {_FONT} '
            f'font-size="10" fill="{BOX_INK}">{_esc(shorten(kind.label, node.width))}</text>',
            "</g>",
        ]
    lines.append("</svg>")
    return "\n".join(lines)
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_diagram_render.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

Si `Box = dict[object, ...]` gêne mypy, remplacer `dict[object, Box]` par `dict[UUID, Box]` avec `from uuid import UUID`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/diagram_render.py backend/tests/unit/test_diagram_render.py
git commit -m "feat(reports): single static SVG drawing of a diagram

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Test de parité entre `diagram_render.py` et la géométrie de l'éditeur

Une fixture JSON unique; le test backend vérifie que le SVG serveur du jeu est exactement celui de la fixture (instantané); le test Vitest vérifie que les coordonnées de ce SVG sont celles que `diagramGeometry.ts` calcule pour les mêmes boîtes. Modifier un côté sans l'autre fait échouer l'un des deux.

**Files:**
- Create: `frontend/tests/fixtures/diagram-parity.json`
- Create: `frontend/tests/diagramParity.spec.ts`
- Modify: `backend/tests/unit/test_diagram_render.py` (ajout en fin de fichier)
- Modify: `frontend/src/lib/diagramGeometry.ts` (ajout après `DEFAULT_BOX`/`MIN_BOX`, l.~27-33)
- Modify: `frontend/src/features/diagrams/DiagramCanvas.vue` (import l.13-22; constantes l.46-51; `extent` l.62; `shorten` l.82-86)

**Interfaces:**
- Consumes: `render_svg`, `LAYER_FILL`, `BOX_INK` (Task 2); `canvasExtent`, `edgeSegment` (existants); `LAYER_COLOURS`, `BOX_TEXT` (`features/metamodel/useMetamodel`).
- Produces: `CANVAS_MARGIN`, `CHARACTER_WIDTH`, `shorten(name, width)` exportés de `lib/diagramGeometry.ts` (le canvas les importe, plus de copie locale).

- [ ] **Step 1: Écrire la fixture** (`frontend/tests/fixtures/diagram-parity.json`; la clé `svg` est vide, générée au Step 3)

```json
{
  "name": "Parité",
  "layer_colours": {
    "motivation": "#ccccff",
    "strategy": "#f5deaa",
    "business": "#ffffb5",
    "application": "#b5ffff",
    "technology": "#c9e7b7",
    "physical": "#afdfa0",
    "implementation_migration": "#ffe0e0",
    "other": "#e8e6ec"
  },
  "box_ink": "#24202b",
  "nodes": [
    { "element_id": "10000000-0000-4000-8000-000000000001", "x": 40, "y": 40, "width": 132, "height": 46 },
    { "element_id": "10000000-0000-4000-8000-000000000002", "x": 400, "y": 60, "width": 220, "height": 80 },
    { "element_id": "10000000-0000-4000-8000-000000000003", "x": 60, "y": 300, "width": 132, "height": 46 },
    { "element_id": "10000000-0000-4000-8000-000000000004", "x": 80, "y": 60, "width": 132, "height": 46 }
  ],
  "elements": [
    { "id": "10000000-0000-4000-8000-000000000001", "element_type": "application_component", "layer": "application", "name": "Facturation" },
    { "id": "10000000-0000-4000-8000-000000000002", "element_type": "business_process", "layer": "business", "name": "Order to cash : traitement complet des commandes" },
    { "id": "10000000-0000-4000-8000-000000000003", "element_type": "node", "layer": "technology", "name": "Serveur A" },
    { "id": "10000000-0000-4000-8000-000000000004", "element_type": "application_component", "layer": "application", "name": "Catalogue" }
  ],
  "relationships": [
    { "id": "20000000-0000-4000-8000-000000000001", "relationship_type": "association", "source_id": "10000000-0000-4000-8000-000000000001", "target_id": "10000000-0000-4000-8000-000000000002", "access_type": null },
    { "id": "20000000-0000-4000-8000-000000000002", "relationship_type": "association", "source_id": "10000000-0000-4000-8000-000000000003", "target_id": "10000000-0000-4000-8000-000000000001", "access_type": null },
    { "id": "20000000-0000-4000-8000-000000000003", "relationship_type": "association", "source_id": "10000000-0000-4000-8000-000000000001", "target_id": "10000000-0000-4000-8000-000000000004", "access_type": null }
  ],
  "svg": ""
}
```

Le jeu couvre: un nom plus long que la boîte (raccourci), un lien normal, un lien vertical, un chevauchement (3e lien: aucun trait). Les caractères sont tous du BMP (le `length` JS compte des unités UTF-16, Python des points de code: un emoji dans la fixture ferait diverger les deux règles de `shorten`, limite écrite dans l'ADR du dessin).

- [ ] **Step 2: Écrire le test backend d'instantané (échoue: `svg` vide)**

Ajouter en fin de `backend/tests/unit/test_diagram_render.py` (imports en tête: `import json`, `import os`, `from pathlib import Path`, `from uuid import UUID`, `from ea.domain.diagram_render import BOX_INK`):

```python
FIXTURE = Path(__file__).parents[3] / "frontend" / "tests" / "fixtures" / "diagram-parity.json"


def detail_from_fixture(data: dict) -> DiagramDetail:  # type: ignore[type-arg]
    elements = {
        e["id"]: Element.create(
            element_type=ElementType(e["element_type"]),
            name=e["name"],
            now=NOW,
            element_id=UUID(e["id"]),
        )
        for e in data["elements"]
    }
    links = tuple(
        Relationship.between(
            RelationshipType(r["relationship_type"]),
            elements[r["source_id"]],
            elements[r["target_id"]],
            now=NOW,
            relationship_id=UUID(r["id"]),
        )
        for r in data["relationships"]
    )
    return DiagramDetail(
        diagram=Diagram.create(name=data["name"], now=NOW),
        nodes=tuple(
            DiagramNode(UUID(n["element_id"]), n["x"], n["y"], n["width"], n["height"])
            for n in data["nodes"]
        ),
        graph=GraphView(elements=tuple(elements.values()), relationships=links),
    )


class TestTheSharedParityFixture:
    """The editor's geometry (Vitest) and this drawing read the same fixture."""

    def test_the_server_drawing_is_the_one_the_editor_is_compared_with(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        svg = render_svg(detail_from_fixture(data))
        if os.environ.get("UPDATE_PARITY_FIXTURE") == "1":
            data["svg"] = svg
            FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        assert svg == data["svg"]

    def test_the_palette_is_the_one_the_editor_paints_with(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))

        assert {layer.value: fill for layer, fill in LAYER_FILL.items()} == data["layer_colours"]
        assert BOX_INK == data["box_ink"]
```

`Element.create(element_id=...)` et `Relationship.between(relationship_id=...)` existent (lus dans `domain/model.py`).

- [ ] **Step 3: Lancer, échec attendu, puis générer l'instantané**

Run: `cd backend && uv run pytest tests/unit/test_diagram_render.py -x -k Shared` → échec (`svg` vide).
Run: `cd backend && UPDATE_PARITY_FIXTURE=1 uv run pytest tests/unit/test_diagram_render.py -x -k Shared` → PASS, fixture réécrite.
Run: `cd backend && uv run pytest tests/unit/test_diagram_render.py -x` → PASS sans la variable. Relire le `svg` de la fixture: 4 `class="node"`, 2 `class="edge"` (le lien chevauché n'a pas de trait).

- [ ] **Step 4: Exporter la géométrie du canvas** (`frontend/src/lib/diagramGeometry.ts`, après `export const MIN_BOX ...`)

```ts
/** Room kept past the furthest box, so there is always somewhere to drop. */
export const CANVAS_MARGIN = 160

/** Roughly how wide one character of a box's name is drawn. */
export const CHARACTER_WIDTH = 7.5

/** As much of `name` as a box `width` wide holds: a wider box shows more of it. */
export function shorten(name: string, width: number): string {
  const limit = Math.max(4, Math.floor(width / CHARACTER_WIDTH))
  return name.length > limit ? `${name.slice(0, limit - 1)}…` : name
}
```

Dans `DiagramCanvas.vue`:
1. Import: remplacer `  canvasExtent,\n  centreOf,` par `  CANVAS_MARGIN,\n  canvasExtent,\n  centreOf,` et `  resizedTo,\n  toCanvasPoint,` par `  resizedTo,\n  shorten,\n  toCanvasPoint,`.
2. Supprimer les lignes `/** Roughly how wide one character of a box's name is drawn. */` + `const CHARACTER_WIDTH = 7.5` (l.46-47) et `/** Room kept past the furthest box, so there is always somewhere to drop. */` + `const MARGIN = 160` (l.50-51).
3. `canvasExtent(props.boxes, MARGIN)` devient `canvasExtent(props.boxes, CANVAS_MARGIN)`.
4. Supprimer la fonction locale `shorten` et son commentaire (l.82-86).

- [ ] **Step 5: Écrire le test Vitest de parité**

```ts
// The static drawing (server) and the editor (this SPA) must put the same
// things at the same coordinates: one fixture, read by both sides.
import { describe, expect, it } from 'vitest'

import { BOX_TEXT, LAYER_COLOURS } from '../src/features/metamodel/useMetamodel'
import { CANVAS_MARGIN, canvasExtent, edgeSegment, shorten } from '../src/lib/diagramGeometry'
import fixture from './fixtures/diagram-parity.json'

const SVG = new DOMParser().parseFromString(fixture.svg, 'image/svg+xml')
const rects = new Map(
  fixture.nodes.map((node) => [
    node.element_id,
    { x: node.x, y: node.y, width: node.width, height: node.height },
  ]),
)

const near = (value: string | null | undefined, expected: number) =>
  expect(Number(value)).toBeCloseTo(expected, 2)

describe('the server drawing and the editor agree', () => {
  it('has a server drawing to compare with', () => {
    expect(SVG.querySelector('parsererror')).toBeNull()
    expect(fixture.svg.length).toBeGreaterThan(100)
  })

  it('draws every box where the editor places it, in the colour the editor paints', () => {
    for (const node of fixture.nodes) {
      const rect = SVG.querySelector(`g[data-element-id="${node.element_id}"] rect`)
      near(rect?.getAttribute('x'), node.x)
      near(rect?.getAttribute('y'), node.y)
      near(rect?.getAttribute('width'), node.width)
      near(rect?.getAttribute('height'), node.height)
      const element = fixture.elements.find((e) => e.id === node.element_id)
      const layer = element?.layer as keyof typeof LAYER_COLOURS
      expect(rect?.getAttribute('fill')).toBe(LAYER_COLOURS[layer])
    }
  })

  it('sizes the canvas as the editor does', () => {
    const extent = canvasExtent([...rects.values()], CANVAS_MARGIN)

    expect(SVG.documentElement.getAttribute('viewBox')).toBe(
      `0 0 ${extent.width} ${extent.height}`,
    )
  })

  it('trims every link as the editor does, and draws none where boxes overlap', () => {
    for (const link of fixture.relationships) {
      const source = rects.get(link.source_id)
      const target = rects.get(link.target_id)
      const segment = source && target ? edgeSegment(source, target) : null
      const line = SVG.querySelector(`g[data-relationship-id="${link.id}"] line`)
      if (segment === null) {
        expect(line).toBeNull()
        continue
      }
      near(line?.getAttribute('x1'), segment.from.x)
      near(line?.getAttribute('y1'), segment.from.y)
      near(line?.getAttribute('x2'), segment.to.x)
      near(line?.getAttribute('y2'), segment.to.y)
    }
  })

  it('shortens names by the editor rule', () => {
    for (const element of fixture.elements) {
      const width = rects.get(element.id)?.width ?? 0
      const text = SVG.querySelector(`g[data-element-id="${element.id}"] text.name`)?.textContent
      expect(text).toBe(shorten(element.name, width))
    }
  })

  it('uses the editor palette and ink', () => {
    expect(fixture.layer_colours).toEqual(LAYER_COLOURS)
    expect(fixture.box_ink).toBe(BOX_TEXT)
  })
})
```

- [ ] **Step 6: Lancer le test Vitest, puis la suite du canvas**

Run: `cd frontend && npm test -- --run tests/diagramParity.spec.ts tests/diagramGeometry.spec.ts tests/DiagramCanvas.spec.ts`
Attendu: PASS (avant le Step 4, le test échoue sur l'import `CANVAS_MARGIN`: c'est l'échec attendu si on inverse l'ordre des étapes).

Run: `cd frontend && npm run lint && npm run typecheck`

- [ ] **Step 7: Commit**

```bash
git add frontend/tests/fixtures/diagram-parity.json frontend/tests/diagramParity.spec.ts frontend/src/lib/diagramGeometry.ts frontend/src/features/diagrams/DiagramCanvas.vue backend/tests/unit/test_diagram_render.py
git commit -m "test(reports): parity between the server SVG and the editor geometry

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Rendu markdown sûr (`domain/markdown_html.py`) et dépendance

**Files:**
- Modify: `backend/pyproject.toml` (liste `dependencies`, ordre alphabétique, entre `httpx` et `mcp`)
- Modify: `backend/uv.lock` (régénéré)
- Create: `backend/src/ea/domain/markdown_html.py`
- Create: `backend/tests/unit/test_markdown_html.py`

**Interfaces:**
- Produces: `render_markdown(source: str) -> str`, un fragment HTML sûr.

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""Markdown becomes HTML a browser or a PDF engine can be handed, and nothing more."""

from __future__ import annotations

from ea.domain.markdown_html import render_markdown


def test_ordinary_markdown_is_rendered() -> None:
    html = render_markdown("# Titre\n\nUn *mot* et `du code`.\n\n- a\n- b\n")

    assert "<h1>Titre</h1>" in html
    assert "<em>mot</em>" in html
    assert "<code>du code</code>" in html
    assert "<li>a</li>" in html


def test_raw_html_is_text_not_markup() -> None:
    html = render_markdown("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>")

    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_dangerous_link_schemes_are_not_links() -> None:
    html = render_markdown(
        "[a](javascript:alert(1)) [b](JavaScript:alert(1)) [c](data:text/html,x) "
        "[d](vbscript:x) <javascript:alert(1)>"
    )

    assert 'href="javascript' not in html.lower()
    assert 'href="data' not in html.lower()
    assert 'href="vbscript' not in html.lower()


def test_a_normal_link_survives() -> None:
    assert 'href="https://example.org/x"' in render_markdown("[x](https://example.org/x)")
    assert 'href="mailto:a@b.example"' in render_markdown("[m](mailto:a@b.example)")
    assert 'href="#section"' in render_markdown("[s](#section)")


def test_only_web_and_mail_links_are_links() -> None:
    html = render_markdown("[i](data:image/png;base64,AAAA) [f](ftp://h/x) [t](tel:112)")

    assert "href=" not in html


def test_images_are_not_loaded() -> None:
    html = render_markdown("![logo](https://evil.example/pixel.png) ![d](data:image/png;base64,AAAA)")

    assert "<img" not in html


def test_a_fenced_block_shows_its_markup_as_text() -> None:
    html = render_markdown("```html\n<script>x</script>\n```")

    assert "<script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_the_empty_document_is_the_empty_string() -> None:
    assert render_markdown("") == ""
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError`)

Run: `cd backend && uv run pytest tests/unit/test_markdown_html.py -x`

- [ ] **Step 3: Déclarer la dépendance et implémenter**

Run: `cd backend && uv add "markdown-it-py>=3.0"` (met à jour `pyproject.toml` et `uv.lock`; la bibliothèque est déjà verrouillée via `rich`, elle devient directe). Ajouter dans `pyproject.toml` au-dessus de la ligne, le commentaire: `# Safe markdown to HTML for reports and the reader mode — see docs/adr/0042.`

Créer `backend/src/ea/domain/markdown_html.py`:

```python
"""Markdown to a safe HTML fragment — one renderer for reports and the reader mode.

Pure: no I/O, deterministic. The three settings that make it safe are the whole
point: raw HTML is disabled (it is shown as text), the `image` rule is disabled
(a report must not fetch anything, and the PDF engine refuses every URL anyway),
and `validateLink` is replaced by an allow-list: a link is relative, `http`,
`https` or `mailto`, and nothing else (the library's own default would let
`data:image/...` through). The SPA may therefore insert the result with `v-html`.
"""

from __future__ import annotations

import re
from typing import Final

from markdown_it import MarkdownIt

_SCHEME: Final = re.compile(r"^\s*([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SAFE_SCHEMES: Final = frozenset({"http", "https", "mailto"})


def _only_safe_links(url: str) -> bool:
    """A relative link, or one that goes to the web or to a mailbox. Nothing else.

    markdown-it's own default lets `data:image/png;...` through as a link target;
    a report has no use for any scheme beyond these three.
    """
    scheme = _SCHEME.match(url)
    return scheme is None or scheme.group(1).lower() in _SAFE_SCHEMES


_RENDERER: Final = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")
_RENDERER.validateLink = _only_safe_links


def render_markdown(source: str) -> str:
    """The HTML fragment for `source`; text from the author never becomes markup."""
    return _RENDERER.render(source)
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_markdown_html.py -x && uv run mypy src && uv run ruff check src tests`

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/ea/domain/markdown_html.py backend/tests/unit/test_markdown_html.py
git commit -m "feat(reports): safe markdown renderer (raw HTML and images off)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Modèle de rapport, gabarits intégrés et constructeur pur (`domain/reports.py`)

**Files:**
- Create: `backend/src/ea/domain/reports.py`
- Create: `backend/tests/unit/test_reports_domain.py`

**Interfaces:**
- Consumes: `render_svg`, `verb_of`, `RELATIONSHIP_VERBS` (Task 2); `DiagramDetail`; `Element`, `Relationship`; `UnknownReportTemplateError`.
- Produces (tous dans `ea.domain.reports`):
  - constantes `MAX_REPORT_ELEMENTS`, `MAX_REPORT_DIAGRAMS`, `MAX_REPORT_DOCUMENT_BYTES`, `REPORT_TIMEOUT_SECONDS`
  - `ReportFormat = Literal["html","pdf","docx"]`, `REPORT_FORMATS`, `SubjectKind = Literal["package","diagram","query"]`, `CONTENT_TYPES: Mapping[ReportFormat,str]`
  - `ReportSubject(kind, id: UUID)`, `ReportRequest(template_id: str, subject: ReportSubject, format: ReportFormat)`
  - sections `TextSection(markdown)`, `ElementTableSection(columns, title="Éléments")`, `DiagramSection()`, `RelationsSection(title="Relations")`, `DocumentsSection(title="Documents")`; `ReportTemplate(id, version, title, subjects, sections)`; `BUILT_IN_TEMPLATES`, `template_by_id(id)`
  - `ScopedDocument(element_name, filename, markdown)`, `ReportScope(kind, name, description, diagrams, elements, relationships)`
  - blocs `Heading(level, text)`, `Paragraph(text)`, `MarkdownBlock(source)`, `Table(headers, rows)`, `Figure(svg, caption)`; `ReportModel(title, subtitle, blocks)`
  - `build_model(template, scope, documents, *, now) -> ReportModel`
  - `RenderedReport(content, content_type, filename, duration_ms, subject_name)`, `Renderer = Callable[[ReportModel], bytes]`
  - `slugify`, `report_filename(*, now, subject_name, template_id, fmt, unique="")`, `report_key(filename, now)`

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""Templates, the closed substitution, and the model built from a scope."""

from __future__ import annotations

from datetime import UTC, datetime
import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode
from ea.domain.errors import UnknownReportTemplateError
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView
from ea.domain.reports import (
    BUILT_IN_TEMPLATES,
    DiagramSection,
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportScope,
    ReportTemplate,
    ScopedDocument,
    Table,
    TextSection,
    build_model,
    report_filename,
    report_key,
    slugify,
    substitute,
    template_by_id,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(name: str = "Facturation", description: str = "") -> Element:
    return Element.create(
        element_type=ElementType.APPLICATION_COMPONENT, name=name, now=NOW, description=description
    )


def a_scope(
    elements: tuple[Element, ...] = (),
    links: tuple[Relationship, ...] = (),
    name: str = "Vente",
    description: str = "",
) -> ReportScope:
    diagram = Diagram.create(name=name, description=description, now=NOW)
    nodes = tuple(DiagramNode(e.id, 10 * i, 10) for i, e in enumerate(elements))
    detail = DiagramDetail(diagram, nodes, GraphView(elements, links))
    return ReportScope(
        kind="diagram",
        name=name,
        description=description,
        diagrams=(detail,),
        elements=elements,
        relationships=links,
    )


SHEET = template_by_id("fiche-diagramme")


class TestTheTemplates:
    def test_the_diagram_sheet_is_built_in_and_takes_a_diagram(self) -> None:
        assert SHEET.subjects == frozenset({"diagram"})
        assert SHEET.version == 1
        assert "fiche-diagramme" in BUILT_IN_TEMPLATES

    def test_an_unknown_template_is_a_domain_refusal(self) -> None:
        with pytest.raises(UnknownReportTemplateError, match="nope"):
            template_by_id("nope")

    def test_a_template_with_an_unknown_field_cannot_exist(self) -> None:
        with pytest.raises(ValueError, match="subject.secret"):
            ReportTemplate(
                id="x",
                version=1,
                title="X",
                subjects=frozenset({"diagram"}),
                sections=(TextSection("{{subject.secret}}"),),
            )


class TestTheClosedSubstitution:
    def test_known_fields_are_replaced(self) -> None:
        fields = {"name": "Vente", "description": "d", "kind": "diagram"}

        assert substitute("A {{subject.name}} / {{ subject.kind }}", fields) == "A Vente / diagram"

    def test_markdown_syntax_in_a_value_stays_literal(self) -> None:
        fields = {"name": "*x* [a](http://e) <b>", "description": "", "kind": "diagram"}

        out = substitute("{{subject.name}}", fields)

        assert out == r"\*x\* \[a\]\(http://e\) \<b\>"


class TestBuildingTheModel:
    def test_the_title_names_the_template_and_the_subject(self) -> None:
        model = build_model(SHEET, a_scope(), (), now=NOW)

        assert model.title == "Fiche de diagramme — Vente"
        assert "2026-10-10 12:00 UTC" in model.subtitle
        assert "fiche-diagramme v1" in model.subtitle

    def test_the_introduction_substitutes_the_subject_as_literal_text(self) -> None:
        model = build_model(SHEET, a_scope(name="**gras**"), (), now=NOW)

        intro = next(b for b in model.blocks if isinstance(b, MarkdownBlock))
        assert r"\*\*gras\*\*" in intro.source

    def test_an_empty_diagram_gives_honest_empty_sections(self) -> None:
        model = build_model(SHEET, a_scope(), (), now=NOW)

        texts = [b.text for b in model.blocks if isinstance(b, Paragraph)]
        assert texts == ["Aucun élément.", "Aucune relation.", "Aucun document."]
        assert any(isinstance(b, Figure) for b in model.blocks)

    def test_elements_are_tabulated_in_name_order(self) -> None:
        zebra, alpha = an_element("Zèbre"), an_element("Alpha", "premier")

        model = build_model(SHEET, a_scope((zebra, alpha)), (), now=NOW)

        table = next(b for b in model.blocks if isinstance(b, Table))
        assert table.headers == ("Nom", "Type", "Couche", "Description")
        assert [row[0] for row in table.rows] == ["Alpha", "Zèbre"]
        assert table.rows[0] == ("Alpha", "Application Component", "Application", "premier")

    def test_relations_are_grouped_by_type_with_both_ends_named(self) -> None:
        left, right = an_element("A"), an_element("B")
        link = Relationship.between(RelationshipType.ASSOCIATION, left, right, now=NOW)

        model = build_model(SHEET, a_scope((left, right), (link,)), (), now=NOW)

        assert Heading(3, "Association") in model.blocks
        tables = [b for b in model.blocks if isinstance(b, Table)]
        assert tables[-1].rows == (("A", "est associé à", "B"),)

    def test_documents_are_listed_under_their_element(self) -> None:
        docs = (ScopedDocument("Facturation", "runbook.md", "# Run\n"),)

        model = build_model(SHEET, a_scope((an_element(),)), docs, now=NOW)

        assert Heading(3, "Facturation — runbook.md") in model.blocks
        assert MarkdownBlock("# Run\n") in model.blocks

    def test_every_diagram_section_draws_each_diagram_of_the_scope(self) -> None:
        template = ReportTemplate(
            id="t", version=1, title="T", subjects=frozenset({"diagram"}), sections=(DiagramSection(),)
        )

        model = build_model(template, a_scope(), (), now=NOW)

        figures = [b for b in model.blocks if isinstance(b, Figure)]
        assert [f.caption for f in figures] == ["Vente"]
        assert figures[0].svg.startswith("<svg")


class TestNames:
    def test_a_subject_becomes_an_ascii_slug(self) -> None:
        assert slugify("Paysage d'été — SI/Finance") == "paysage-d-ete-si-finance"
        assert slugify("日本語") == "sujet"
        assert len(slugify("x" * 100)) == 40

    def test_the_key_is_under_reports_and_the_year(self) -> None:
        name = report_filename(
            now=NOW, subject_name="Vente", template_id="fiche-diagramme", fmt="pdf", unique="ab12cd34"
        )

        assert name == "2026-10-10-vente-fiche-diagramme-ab12cd34.pdf"
        assert report_key(name, NOW) == "reports/2026/2026-10-10-vente-fiche-diagramme-ab12cd34.pdf"

    def test_a_download_name_has_no_unique_part(self) -> None:
        name = report_filename(now=NOW, subject_name="Vente", template_id="fiche-diagramme", fmt="html")

        assert name == "2026-10-10-vente-fiche-diagramme.html"
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError: ea.domain.reports`)

Run: `cd backend && uv run pytest tests/unit/test_reports_domain.py -x`

- [ ] **Step 3: Implémenter `backend/src/ea/domain/reports.py`**

```python
"""What a report is: a template, a scope read from the catalogue, and the neutral
document built from the two.

Pure (docs/adr/0042). Three serialisers turn the one
`ReportModel` into HTML, PDF or DOCX, so the content logic exists once. Templates
are code, not evaluated text: the only substitution is `{{subject.<field>}}`
from a closed table, and the value is escaped as markdown.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Literal
from uuid import UUID

from ea.domain.archimate import RelationshipType
from ea.domain.diagram_render import render_svg, verb_of
from ea.domain.diagrams import DiagramDetail
from ea.domain.errors import UnknownReportTemplateError
from ea.domain.model import Element, Relationship

MAX_REPORT_ELEMENTS: Final = 500
MAX_REPORT_DIAGRAMS: Final = 20
MAX_REPORT_DOCUMENT_BYTES: Final = 2 * 1024 * 1024
REPORT_TIMEOUT_SECONDS: Final = 30.0

ReportFormat = Literal["html", "pdf", "docx"]
SubjectKind = Literal["package", "diagram", "query"]
REPORT_FORMATS: Final[tuple[ReportFormat, ...]] = ("html", "pdf", "docx")
CONTENT_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "html": "text/html; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
SUBJECT_FIELDS: Final = ("name", "description", "kind")


@dataclass(frozen=True, slots=True)
class ReportSubject:
    kind: SubjectKind
    id: UUID


@dataclass(frozen=True, slots=True)
class ReportRequest:
    template_id: str
    subject: ReportSubject
    format: ReportFormat


# --- Sections ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextSection:
    markdown: str


@dataclass(frozen=True, slots=True)
class ElementTableSection:
    columns: tuple[Literal["name", "type", "layer", "description"], ...]
    title: str = "Éléments"


@dataclass(frozen=True, slots=True)
class DiagramSection:
    """The diagram of the subject, or every diagram of a package."""


@dataclass(frozen=True, slots=True)
class RelationsSection:
    title: str = "Relations"


@dataclass(frozen=True, slots=True)
class DocumentsSection:
    title: str = "Documents"


type Section = (
    TextSection | ElementTableSection | DiagramSection | RelationsSection | DocumentsSection
)

_PLACEHOLDER: Final = re.compile(r"\{\{\s*subject\.(\w+)\s*\}\}")
_MARKDOWN_SPECIALS: Final = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~&])")


def substitute(text: str, fields: Mapping[str, str]) -> str:
    """Replace `{{subject.<field>}}` from a closed table; the value stays literal text."""

    def one(match: re.Match[str]) -> str:
        field = match.group(1)
        if field not in SUBJECT_FIELDS:
            msg = f"unknown template field subject.{field}"
            raise ValueError(msg)
        return _MARKDOWN_SPECIALS.sub(r"\\\1", fields[field])

    return _PLACEHOLDER.sub(one, text)


@dataclass(frozen=True, slots=True)
class ReportTemplate:
    """Shaped as a stored row would be (id, version, definition), so editable
    templates later add a table without changing this type."""

    id: str
    version: int
    title: str
    subjects: frozenset[SubjectKind]
    sections: tuple[Section, ...]

    def __post_init__(self) -> None:
        blank = dict.fromkeys(SUBJECT_FIELDS, "")
        for section in self.sections:
            if isinstance(section, TextSection):
                substitute(section.markdown, blank)  # raises on an unknown field

    @property
    def needs_documents(self) -> bool:
        return any(isinstance(section, DocumentsSection) for section in self.sections)


DIAGRAM_SHEET: Final = ReportTemplate(
    id="fiche-diagramme",
    version=1,
    title="Fiche de diagramme",
    subjects=frozenset({"diagram"}),
    sections=(
        TextSection("Ce rapport décrit le diagramme **{{subject.name}}**.\n\n{{subject.description}}"),
        DiagramSection(),
        ElementTableSection(columns=("name", "type", "layer", "description")),
        RelationsSection(),
        DocumentsSection(),
    ),
)

BUILT_IN_TEMPLATES: Final[Mapping[str, ReportTemplate]] = MappingProxyType(
    {DIAGRAM_SHEET.id: DIAGRAM_SHEET}
)


def template_by_id(template_id: str) -> ReportTemplate:
    try:
        return BUILT_IN_TEMPLATES[template_id]
    except KeyError:
        msg = f"no report template {template_id!r}; GET /report-templates lists them"
        raise UnknownReportTemplateError(msg) from None


# --- Scope read by the service ---------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopedDocument:
    element_name: str
    filename: str
    markdown: str


@dataclass(frozen=True, slots=True)
class ReportScope:
    kind: SubjectKind
    name: str
    description: str
    diagrams: tuple[DiagramDetail, ...]
    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]


# --- The neutral document ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    source: str


@dataclass(frozen=True, slots=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Figure:
    """An SVG drawing already escaped by `render_svg`, and its caption."""

    svg: str
    caption: str


type Block = Heading | Paragraph | MarkdownBlock | Table | Figure


@dataclass(frozen=True, slots=True)
class ReportModel:
    title: str
    subtitle: str
    blocks: tuple[Block, ...]


type Renderer = Callable[[ReportModel], bytes]


@dataclass(frozen=True, slots=True)
class RenderedReport:
    content: bytes
    content_type: str
    filename: str
    duration_ms: int
    subject_name: str


_COLUMN_HEADERS: Final = {
    "name": "Nom",
    "type": "Type",
    "layer": "Couche",
    "description": "Description",
}


def _layer_label(element: Element) -> str:
    return element.element_type.layer.value.replace("_", " ").capitalize()


def _cell(element: Element, column: str) -> str:
    match column:
        case "name":
            return element.name
        case "type":
            return element.element_type.label
        case "layer":
            return _layer_label(element)
        case _:
            return element.description


def build_model(
    template: ReportTemplate,
    scope: ReportScope,
    documents: Sequence[ScopedDocument],
    *,
    now: datetime,
) -> ReportModel:
    """The neutral document for `template` over `scope`: pure, ordered, deterministic."""
    fields = {"name": scope.name, "description": scope.description, "kind": scope.kind}
    names = {element.id: element.name for element in scope.elements}
    ordered = sorted(scope.elements, key=lambda e: (e.name.casefold(), str(e.id)))
    blocks: list[Block] = []
    for section in template.sections:
        match section:
            case TextSection():
                blocks.append(MarkdownBlock(substitute(section.markdown, fields)))
            case DiagramSection():
                for detail in scope.diagrams:
                    blocks += [
                        Heading(2, detail.diagram.name),
                        Figure(render_svg(detail), detail.diagram.name),
                    ]
            case ElementTableSection():
                blocks.append(Heading(2, section.title))
                if not ordered:
                    blocks.append(Paragraph("Aucun élément."))
                else:
                    blocks.append(
                        Table(
                            headers=tuple(_COLUMN_HEADERS[c] for c in section.columns),
                            rows=tuple(
                                tuple(_cell(e, c) for c in section.columns) for e in ordered
                            ),
                        )
                    )
            case RelationsSection():
                blocks.append(Heading(2, section.title))
                if not scope.relationships:
                    blocks.append(Paragraph("Aucune relation."))
                order = list(RelationshipType)
                for kind in sorted({r.relationship_type for r in scope.relationships}, key=order.index):
                    rows = sorted(
                        (
                            (
                                names.get(r.source_id, "(hors périmètre)"),
                                verb_of(r),
                                names.get(r.target_id, "(hors périmètre)"),
                            )
                            for r in scope.relationships
                            if r.relationship_type is kind
                        )
                    )
                    blocks += [
                        Heading(3, kind.value.replace("_", " ").capitalize()),
                        Table(("Source", "Relation", "Cible"), tuple(rows)),
                    ]
            case DocumentsSection():
                blocks.append(Heading(2, section.title))
                if not documents:
                    blocks.append(Paragraph("Aucun document."))
                for document in documents:
                    blocks += [
                        Heading(3, f"{document.element_name} — {document.filename}"),
                        MarkdownBlock(document.markdown),
                    ]
    return ReportModel(
        title=f"{template.title} — {scope.name}",
        subtitle=(
            f"Généré le {now:%Y-%m-%d %H:%M} UTC — modèle {template.id} v{template.version}"
        ),
        blocks=tuple(blocks),
    )


# --- File names -------------------------------------------------------------


def slugify(text: str, *, limit: int = 40) -> str:
    """An ASCII slug: accents folded, anything else collapsed to `-`; `sujet` if nothing is left."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:limit].strip("-")
    return slug or "sujet"


def report_filename(
    *, now: datetime, subject_name: str, template_id: str, fmt: str, unique: str = ""
) -> str:
    parts = [f"{now:%Y-%m-%d}", slugify(subject_name), template_id]
    if unique:
        parts.append(unique)
    return f"{'-'.join(parts)}.{fmt}"


def report_key(filename: str, now: datetime) -> str:
    return f"reports/{now:%Y}/{filename}"
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_reports_domain.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/reports.py backend/tests/unit/test_reports_domain.py
git commit -m "feat(reports): report model, built-in diagram sheet, pure model builder

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Sérialiseur HTML autonome (`domain/report_html.py`)

**Files:**
- Create: `backend/src/ea/domain/report_html.py`
- Create: `backend/tests/unit/test_report_html.py`

**Interfaces:**
- Consumes: `ReportModel` et blocs (Task 5), `render_markdown` (Task 4).
- Produces: `to_html(model: ReportModel) -> str`, un document complet autonome (CSS en ligne, SVG en ligne, CSP en `<meta>`).

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""The self-contained HTML report: escaped text, inline SVG, no external reference."""

from __future__ import annotations

from ea.domain.report_html import to_html
from ea.domain.reports import (
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)

NASTY = '<script>alert(1)</script> & "q" \'s\''


def a_model(*blocks: object, title: str = "Rapport") -> ReportModel:
    return ReportModel(title=title, subtitle="Généré", blocks=tuple(blocks))  # type: ignore[arg-type]


def test_a_document_is_complete_and_declares_its_encoding_and_policy() -> None:
    html = to_html(a_model())

    assert html.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert "<h1>Rapport</h1>" in html


def test_no_external_reference_is_ever_emitted() -> None:
    html = to_html(a_model(Heading(2, "A"), Paragraph("b"), Table(("h",), (("c",),))))

    for forbidden in ("http://", "https://", "<link", "src=", "@import"):
        assert forbidden not in html


def test_every_text_from_a_person_is_escaped() -> None:
    model = a_model(
        Heading(2, NASTY),
        Paragraph(NASTY),
        Table((NASTY,), ((NASTY,),)),
        Figure("<svg></svg>", NASTY),
        title=NASTY,
    )

    html = to_html(model)

    assert "<script>" not in html
    assert html.count("&lt;script&gt;") >= 5


def test_a_figure_inlines_the_svg_untouched_with_an_escaped_caption() -> None:
    html = to_html(a_model(Figure('<svg id="d"></svg>', "Légende <b>")))

    assert '<figure><svg id="d"></svg>' in html
    assert "<figcaption>Légende &lt;b&gt;</figcaption>" in html


def test_markdown_goes_through_the_safe_renderer() -> None:
    html = to_html(a_model(MarkdownBlock("# T\n\n<script>x</script>")))

    assert "<h1>T</h1>" in html
    assert "<script>" not in html


def test_a_table_has_header_cells_and_body_rows() -> None:
    html = to_html(a_model(Table(("Nom", "Type"), (("A", "x"), ("B", "y")))))

    assert "<th>Nom</th><th>Type</th>" in html
    assert "<tr><td>A</td><td>x</td></tr>" in html


def test_heading_levels_stay_within_html() -> None:
    html = to_html(a_model(Heading(0, "a"), Heading(9, "b")))

    assert "<h1>a</h1>" in html
    assert "<h6>b</h6>" in html
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError`)

Run: `cd backend && uv run pytest tests/unit/test_report_html.py -x`

- [ ] **Step 3: Implémenter**

```python
"""The self-contained HTML report: the one serialiser PDF reuses as its input.

Everything is inline (style, SVG) and a `<meta>` policy forbids any fetch, so a
report opened from a download or printed to PDF reaches nothing. Every text from
a person is escaped here; markdown goes through `render_markdown`; the SVG of a
figure is trusted because `render_svg` escaped it.
"""

from __future__ import annotations

import html
from typing import Final

from ea.domain.markdown_html import render_markdown
from ea.domain.reports import (
    Block,
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)

_STYLE: Final = """
@page { size: A4; margin: 18mm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10pt; color: #24202b; line-height: 1.45; }
h1 { font-size: 20pt; margin-bottom: 0; }
h2 { font-size: 14pt; border-bottom: 1px solid #c9c4d0; padding-bottom: 2pt; margin-top: 22pt; }
h3 { font-size: 11pt; margin-top: 14pt; }
.meta { color: #6a6475; margin-top: 2pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #c9c4d0; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #f0eef4; }
figure { margin: 10pt 0; }
figure svg { max-width: 100%; height: auto; }
figcaption { color: #6a6475; font-size: 9pt; }
pre, code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; }
pre { background: #f6f5f8; padding: 6pt; white-space: pre-wrap; }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _block(block: Block) -> str:
    match block:
        case Heading():
            level = min(max(block.level, 1), 6)
            return f"<h{level}>{_e(block.text)}</h{level}>"
        case Paragraph():
            return f"<p>{_e(block.text)}</p>"
        case MarkdownBlock():
            return render_markdown(block.source)
        case Table():
            head = "".join(f"<th>{_e(h)}</th>" for h in block.headers)
            body = "".join(
                "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in block.rows
            )
            return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        case Figure():
            return f"<figure>{block.svg}<figcaption>{_e(block.caption)}</figcaption></figure>"


def to_html(model: ReportModel) -> str:
    body = "\n".join(_block(block) for block in model.blocks)
    return (
        "<!doctype html>\n"
        '<html lang="fr"><head>\n'
        '<meta charset="utf-8">\n'
        "<meta http-equiv=\"Content-Security-Policy\" "
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        f"<title>{_e(model.title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head><body>\n"
        f"<h1>{_e(model.title)}</h1>\n"
        f'<p class="meta">{_e(model.subtitle)}</p>\n'
        f"{body}\n"
        "</body></html>\n"
    )
```

Note: le test « aucune référence externe » interdit `http://`: le `xmlns="http://www.w3.org/2000/svg"` d'un `Figure` réel en contient un; ce test n'utilise pas de `Figure`, c'est voulu (le `xmlns` n'est pas une requête).

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_html.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/report_html.py backend/tests/unit/test_report_html.py
git commit -m "feat(reports): self-contained HTML serialiser of the report model

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `ReportService.build`: lecture, plafonds, horloge, exclusivité

**Files:**
- Create: `backend/src/ea/services/reports.py`
- Create: `backend/tests/unit/test_report_service.py`

**Interfaces:**
- Consumes: `DiagramService.open(diagram_id) -> DiagramDetail` (`services/diagrams.py`), `DocumentService.list_for_element(element_id) -> tuple[DocumentSummary, ...]` et `.get(document_id) -> Document` (`services/documents.py`), `FileService` (Task 8), `require_caller` (`services/caller.py`), le domaine des Tasks 5-6.
- Produces:
  - `ReportService(diagrams: DiagramService, documents: DocumentService | None, files: FileService, *, extra_renderers: Mapping[ReportFormat, Renderer] | None = None, clock=_utc_now, monotonic=time.monotonic, new_id=_new_id, timeout_seconds=REPORT_TIMEOUT_SECONDS, app_version="0.1.0")`
  - `async list_templates() -> tuple[ReportTemplate, ...]`
  - `async build(request: ReportRequest) -> RenderedReport` (lecture, ne persiste rien)
  - propriété `formats -> tuple[ReportFormat, ...]`

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""The report use cases against in-memory stores, a frozen clock and a fake monotonic clock."""

from __future__ import annotations

import asyncio
import threading
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    DiagramNotFoundError,
    DocumentNotFoundError,
    NotAuthenticatedError,
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnknownReportTemplateError,
    UnsupportedReportSubjectError,
)
from ea.domain.reports import (
    MAX_REPORT_ELEMENTS,
    ReportModel,
    ReportRequest,
    ReportSubject,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.files import FileService
from ea.services.reports import ReportService
from tests.conftest import FIXED_NOW, a_reader

pytestmark = pytest.mark.asyncio


class Ticking:
    """A monotonic clock that moves `step` seconds every time it is read."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


@pytest.fixture
def reports(
    diagram_service: DiagramService,
    document_service_without_an_index: DocumentService,
    file_service: FileService,
) -> ReportService:
    return ReportService(
        diagram_service,
        document_service_without_an_index,
        file_service,
        clock=lambda: FIXED_NOW,
        new_id=lambda: "ab12cd34",
    )


async def a_diagram(
    service: ArchitectureService, diagrams: DiagramService, *names: str, title: str = "Vente"
) -> tuple[object, list]:  # type: ignore[type-arg]
    elements = [
        await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)
        for name in names
    ]
    diagram = await diagrams.create(name=title)
    await diagrams.replace_layout(
        diagram.id, [DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)]
    )
    return diagram, elements


def request_for(diagram: object, fmt: str = "html", template: str = "fiche-diagramme") -> ReportRequest:
    return ReportRequest(template, ReportSubject("diagram", diagram.id), fmt)  # type: ignore[attr-defined,arg-type]


class TestListingTemplates:
    async def test_a_caller_sees_the_built_in_templates_and_the_formats_served(
        self, reports: ReportService
    ) -> None:
        assert [t.id for t in await reports.list_templates()] == ["fiche-diagramme"]
        assert reports.formats == ("html",)

    async def test_nobody_calling_is_refused(
        self, reports: ReportService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await reports.list_templates()


class TestBuilding:
    async def test_a_reader_gets_the_html_of_a_diagram_and_nothing_is_stored(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: object,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "Facturation")

        with acting_as(a_reader()):
            rendered = await reports.build(request_for(diagram))

        assert rendered.content_type == "text/html; charset=utf-8"
        assert rendered.filename == "2026-09-07-vente-fiche-diagramme.html"
        assert b"Facturation" in rendered.content
        assert b"<svg" in rendered.content
        assert files_store.objects == {}  # type: ignore[attr-defined]

    async def test_markup_in_a_name_is_text_in_the_whole_report(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, '<script>alert(1)</script> & "x"', title="<b>T</b>"
        )

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "<script>" not in html
        assert "<b>T</b>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    async def test_a_diagram_without_elements_still_gives_a_report(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="Vide")

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "Aucun élément." in html
        assert "Aucune relation." in html

    async def test_an_unknown_diagram_is_not_found(self, reports: ReportService) -> None:
        ghost = ReportRequest("fiche-diagramme", ReportSubject("diagram", uuid4()), "html")

        with pytest.raises(DiagramNotFoundError):
            await reports.build(ghost)

    async def test_an_unknown_template_is_refused(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")

        with pytest.raises(UnknownReportTemplateError):
            await reports.build(request_for(diagram, template="nope"))

    async def test_a_subject_the_template_does_not_take_is_refused(
        self, reports: ReportService
    ) -> None:
        request = ReportRequest("fiche-diagramme", ReportSubject("package", uuid4()), "html")

        with pytest.raises(UnsupportedReportSubjectError):
            await reports.build(request)

    async def test_a_format_the_deployment_cannot_produce_is_a_typed_refusal(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")

        with pytest.raises(ReportFormatUnavailableError, match="pdf"):
            await reports.build(request_for(diagram, "pdf"))

    async def test_nobody_calling_is_refused(
        self, reports: ReportService, nobody_calling: None
    ) -> None:
        ghost = ReportRequest("fiche-diagramme", ReportSubject("diagram", uuid4()), "html")

        with pytest.raises(NotAuthenticatedError):
            await reports.build(ghost)


class TestTheCeilings:
    async def test_past_500_elements_it_is_refused_not_truncated(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )

        with pytest.raises(ReportTooLargeError, match="500") as caught:
            await reports.build(request_for(diagram))

        assert "narrow" in str(caught.value) or "split" in str(caught.value)

    async def test_exactly_500_elements_is_allowed(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS)]
        )

        assert (await reports.build(request_for(diagram))).content

    async def test_documents_past_2_mib_in_total_are_refused(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        document_service_without_an_index: DocumentService,
        reports: ReportService,
    ) -> None:
        diagram, (element,) = await a_diagram(service, diagram_service, "Facturation")
        for number in range(3):  # 3 x 900 000 bytes > 2 MiB
            await document_service_without_an_index.attach_text(
                element.id, filename=f"d{number}.md", content="mot " * 225_000
            )

        with pytest.raises(ReportTooLargeError, match="2 MiB"):
            await reports.build(request_for(diagram))

    async def test_a_document_deleted_while_rendering_is_left_out_not_fatal(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        document_service_without_an_index: DocumentService,
        file_service: FileService,
    ) -> None:
        diagram, (element,) = await a_diagram(service, diagram_service, "Facturation")
        await document_service_without_an_index.attach_text(
            element.id, filename="r.md", content="# Run\n"
        )

        class Vanishing:
            async def list_for_element(self, element_id):  # type: ignore[no-untyped-def]
                return await document_service_without_an_index.list_for_element(element_id)

            async def get(self, document_id):  # type: ignore[no-untyped-def]
                raise DocumentNotFoundError("gone")

        reports = ReportService(diagram_service, Vanishing(), file_service)  # type: ignore[arg-type]

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "Aucun document." in html
        assert "Facturation" in html

    async def test_a_slow_report_is_stopped_by_the_clock_not_by_sleeping(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        slow = ReportService(diagram_service, None, file_service, monotonic=Ticking(31.0))

        with pytest.raises(ReportTooLargeError, match="30 seconds"):
            await slow.build(request_for(diagram))

    async def test_a_fast_clock_lets_it_through(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        quick = ReportService(diagram_service, None, file_service, monotonic=Ticking(0.0))

        assert (await quick.build(request_for(diagram))).content


class TestOneAtATime:
    async def test_a_second_report_of_the_same_caller_waits_its_turn(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        entered, release = threading.Event(), threading.Event()

        def blocking(model: ReportModel) -> bytes:
            entered.set()
            release.wait(5)
            return b"%PDF-fake"

        reports = ReportService(
            diagram_service, None, file_service, extra_renderers={"pdf": blocking}
        )
        first = asyncio.create_task(reports.build(request_for(diagram, "pdf")))
        await asyncio.to_thread(entered.wait, 5)

        with pytest.raises(ReportBusyError):
            await reports.build(request_for(diagram))
        with acting_as(a_reader()):  # another caller is not held up
            assert (await reports.build(request_for(diagram))).content
        release.set()
        assert (await first).content == b"%PDF-fake"
        assert (await reports.build(request_for(diagram))).content  # the slot is free again

    async def test_a_refused_report_frees_the_slot(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")
        with pytest.raises(UnknownReportTemplateError):
            await reports.build(request_for(diagram, template="nope"))

        assert (await reports.build(request_for(diagram))).content
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError: ea.services.reports`)

Run: `cd backend && uv run pytest tests/unit/test_report_service.py -x`

- [ ] **Step 3: Implémenter `backend/src/ea/services/reports.py`**

```python
"""Use cases over reports: read them (any caller) or deposit them (an editor).

A report is a photograph: `build` reads the catalogue once, builds the neutral
`ReportModel` with a pure function, and serialises it. Nothing is cached, so
nothing can leak between two callers' rights. See docs/adr/0042.

Ceilings are the domain's; this layer enforces them *before* the expensive part
and by a monotonic clock injected like every clock here. A thread already
rendering cannot be killed (`ponytail:` below), which is why the elapsed time is
also checked between stages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from ea.domain.errors import (
    DocumentNotFoundError,
    ElementNotFoundError,
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnsupportedReportSubjectError,
)
from ea.domain.report_html import to_html
from ea.domain.reports import (
    BUILT_IN_TEMPLATES,
    CONTENT_TYPES,
    MAX_REPORT_DOCUMENT_BYTES,
    MAX_REPORT_ELEMENTS,
    REPORT_FORMATS,
    REPORT_TIMEOUT_SECONDS,
    RenderedReport,
    Renderer,
    ReportFormat,
    ReportModel,
    ReportRequest,
    ReportScope,
    ReportSubject,
    ReportTemplate,
    ScopedDocument,
    build_model,
    report_filename,
    template_by_id,
)
from ea.services.caller import require_caller

if TYPE_CHECKING:
    from ea.domain.auth import Caller
    from ea.services.diagrams import DiagramService
    from ea.services.documents import DocumentService
    from ea.services.files import FileService

Clock = Callable[[], datetime]
Monotonic = Callable[[], float]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex[:8]


def _html_bytes(model: ReportModel) -> bytes:
    return to_html(model).encode()


class ReportService:
    """The single entry point `api/` and `mcp/` use to produce reports.

    `documents` is `None` only on the test seam without a relational store; the
    documents section then reports none. `extra_renderers` holds the formats
    that need native libraries (PDF, DOCX): a format absent from it is a typed
    501, never a crash.
    """

    def __init__(
        self,
        diagrams: DiagramService,
        documents: DocumentService | None,
        files: FileService,
        *,
        extra_renderers: Mapping[ReportFormat, Renderer] | None = None,
        clock: Clock = _utc_now,
        monotonic: Monotonic = time.monotonic,
        new_id: Callable[[], str] = _new_id,
        timeout_seconds: float = REPORT_TIMEOUT_SECONDS,
        app_version: str = "0.1.0",
    ) -> None:
        self._diagrams = diagrams
        self._documents = documents
        self._files = files
        self._renderers: dict[str, Renderer] = {"html": _html_bytes, **(extra_renderers or {})}
        self._now = clock
        self._monotonic = monotonic
        self._new_id = new_id
        self._timeout = timeout_seconds
        self._version = app_version
        self._busy: set[str] = set()

    @property
    def formats(self) -> tuple[ReportFormat, ...]:
        return tuple(fmt for fmt in REPORT_FORMATS if fmt in self._renderers)

    async def list_templates(self) -> tuple[ReportTemplate, ...]:
        require_caller()
        return tuple(BUILT_IN_TEMPLATES.values())

    async def build(self, request: ReportRequest) -> RenderedReport:
        """The report, as bytes. Reads only: nothing is written to the bucket."""
        caller = require_caller()
        rendered = await self._produce(request, caller)
        self._audit(request, rendered, deposited=False)
        return rendered

    # --- internals ----------------------------------------------------------

    async def _produce(self, request: ReportRequest, caller: Caller) -> RenderedReport:
        template = template_by_id(request.template_id)
        if request.subject.kind not in template.subjects:
            msg = f"the template {template.id!r} takes {sorted(template.subjects)}, not {request.subject.kind!r}"
            raise UnsupportedReportSubjectError(msg)
        renderer = self._renderers.get(request.format)
        if renderer is None:
            msg = f"the {request.format} format is not available on this deployment; html is"
            raise ReportFormatUnavailableError(msg)
        started = self._monotonic()
        with self._exclusive(caller):
            scope = await self._scope(request.subject)
            self._within_time(started)
            documents = await self._documents_of(scope) if template.needs_documents else ()
            self._within_time(started)
            model = build_model(template, scope, documents, now=self._now())
            content = await self._render(renderer, model, started)
        return RenderedReport(
            content=content,
            content_type=CONTENT_TYPES[request.format],
            filename=report_filename(
                now=self._now(),
                subject_name=scope.name,
                template_id=template.id,
                fmt=request.format,
            ),
            duration_ms=int((self._monotonic() - started) * 1000),
            subject_name=scope.name,
        )

    @contextmanager
    def _exclusive(self, caller: Caller) -> Iterator[None]:
        """One report at a time per caller. No `await` between the check and the add."""
        if caller.subject in self._busy:
            msg = "you already have a report being built; wait for it to finish"
            raise ReportBusyError(msg)
        self._busy.add(caller.subject)
        try:
            yield
        finally:
            self._busy.discard(caller.subject)

    async def _scope(self, subject: ReportSubject) -> ReportScope:
        if subject.kind != "diagram":
            msg = f"reports on a {subject.kind!r} are not available on this deployment"
            raise UnsupportedReportSubjectError(msg)
        # One read of the diagram and its graph: the report cannot mix two states.
        detail = await self._diagrams.open(subject.id)
        count = len(detail.graph.elements)
        if count > MAX_REPORT_ELEMENTS:
            msg = (
                f"a report covers at most {MAX_REPORT_ELEMENTS} elements and this diagram "
                f"has {count}; split the diagram or report on a smaller one"
            )
            raise ReportTooLargeError(msg)
        return ReportScope(
            kind="diagram",
            name=detail.diagram.name,
            description=detail.diagram.description,
            diagrams=(detail,),
            elements=detail.graph.elements,
            relationships=detail.graph.relationships,
        )

    async def _documents_of(self, scope: ReportScope) -> tuple[ScopedDocument, ...]:
        if self._documents is None:
            return ()
        # ponytail: one listing per element (at most MAX_REPORT_ELEMENTS); a
        # batched query is the upgrade if a measured report ever needs it.
        found = []
        total = 0
        for element in scope.elements:
            try:
                summaries = await self._documents.list_for_element(element.id)
            except ElementNotFoundError:
                continue  # deleted while the report was being built
            for summary in summaries:
                total += summary.byte_size
                if total > MAX_REPORT_DOCUMENT_BYTES:
                    msg = (
                        "the attached documents total more than 2 MiB, the most a report "
                        "carries; report on fewer elements"
                    )
                    raise ReportTooLargeError(msg)
                found.append((element.name, summary.id))
        documents = []
        for element_name, document_id in found:
            try:
                document = await self._documents.get(document_id)
            except DocumentNotFoundError:
                continue  # deleted while the report was being built
            documents.append(ScopedDocument(element_name, document.filename, document.content))
        return tuple(documents)

    def _within_time(self, started: float) -> None:
        if self._monotonic() - started > self._timeout:
            raise self._too_slow()

    def _too_slow(self) -> ReportTooLargeError:
        msg = (
            f"a report is built in at most {self._timeout:g} seconds; narrow the scope "
            "or ask for the html format"
        )
        return ReportTooLargeError(msg)

    async def _render(self, renderer: Renderer, model: ReportModel, started: float) -> bytes:
        remaining = self._timeout - (self._monotonic() - started)
        if remaining <= 0:
            raise self._too_slow()
        # ponytail: wait_for stops *waiting*; a thread already inside WeasyPrint
        # runs on until it returns. The ceilings above bound its work; a worker
        # process would be the upgrade if that ever stops being enough.
        try:
            return await asyncio.wait_for(asyncio.to_thread(renderer, model), remaining)
        except TimeoutError:
            raise self._too_slow() from None

    @staticmethod
    def _audit(request: ReportRequest, rendered: RenderedReport, *, deposited: bool) -> None:
        logger.info(
            "report generated: %s on %s %s as %s",
            request.template_id,
            request.subject.kind,
            request.subject.id,
            request.format,
            extra={
                "action": "report_generated",
                "template": request.template_id,
                "subject": f"{request.subject.kind}:{request.subject.id}",
                "report_format": request.format,
                "bytes": len(rendered.content),
                "duration_ms": rendered.duration_ms,
                "deposited": deposited,
            },
        )
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_service.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/reports.py backend/tests/unit/test_report_service.py
git commit -m "feat(reports): ReportService.build with ceilings, clock, and one-at-a-time

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `ReportService.deposit`: dépôt sous `reports/`, fiche, garde AST, journal

**Files:**
- Modify: `backend/src/ea/services/reports.py` (imports; nouvelle méthode après `build`)
- Modify: `backend/tests/unit/test_report_service.py` (ajout en fin)
- Modify: `backend/tests/unit/test_service_guards.py` (imports l.20-30; dict `WRITES` l.32-45)

**Interfaces:**
- Consumes: `FileService.upload(key, raw, *, content_type, overwrite=False, details=FileDetails|None) -> CatalogedFile` (lu dans `services/files.py`), `FileDetails`, `MAX_FILE_TITLE_LENGTH` (`domain/files.py`), `report_key`, `report_filename`.
- Produces: `async ReportService.deposit(request: ReportRequest) -> CatalogedFile`.

- [ ] **Step 1: Écrire les tests qui échouent** (ajout dans `test_report_service.py`; imports en tête: `import logging`, `from ea.domain.errors import NotAuthorisedError`, `from tests.conftest import InMemoryObjectStore`)

```python
class TestDepositing:
    async def test_an_editor_deposits_under_reports_with_a_filled_record(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "Facturation")

        stored = await reports.deposit(request_for(diagram))

        key = "reports/2026/2026-09-07-vente-fiche-diagramme-ab12cd34.html"
        assert stored.key == key
        assert key in files_store.objects
        assert files_store.objects[key][1] == "text/html; charset=utf-8"
        record = stored.metadata
        assert record is not None
        assert record.uploaded_by == "editor"
        assert record.details.title == "Rapport Fiche de diagramme — Vente"
        assert record.details.tags == ("rapport", "fiche-diagramme")
        assert f"diagram:{diagram.id}" in record.details.description  # type: ignore[attr-defined]
        assert "v1" in record.details.description

    async def test_two_deposits_are_two_files(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        ids = iter(["aaaaaaaa", "bbbbbbbb"])
        reports = ReportService(
            diagram_service, None, file_service, clock=lambda: FIXED_NOW, new_id=lambda: next(ids)
        )

        first = await reports.deposit(request_for(diagram))
        second = await reports.deposit(request_for(diagram))

        assert first.key != second.key
        assert len(files_store.objects) == 2

    async def test_a_reader_cannot_deposit_and_nothing_is_written(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await reports.deposit(request_for(diagram))

        assert files_store.objects == {}

    async def test_a_report_past_the_ceiling_writes_no_object(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )

        with pytest.raises(ReportTooLargeError):
            await reports.deposit(request_for(diagram))

        assert files_store.objects == {}

    async def test_a_very_long_subject_still_fits_the_200_character_title(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A", title="D" * 200)

        stored = await reports.deposit(request_for(diagram))

        assert stored.metadata is not None
        assert len(stored.metadata.details.title) <= 200


class TestTheAuditLine:
    async def test_one_line_per_report_says_whether_it_was_deposited(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await reports.build(request_for(diagram))
            await reports.deposit(request_for(diagram))

        lines = [r for r in caplog.records if getattr(r, "action", "") == "report_generated"]
        assert [line.deposited for line in lines] == [False, True]  # type: ignore[attr-defined]
        assert lines[0].template == "fiche-diagramme"  # type: ignore[attr-defined]
        assert lines[0].report_format == "html"  # type: ignore[attr-defined]
        assert lines[0].bytes > 0  # type: ignore[attr-defined]
```

Dans `backend/tests/unit/test_service_guards.py`: ajouter `from ea.services.reports import ReportService` (ordre alphabétique des imports `ea.services.*`) et, dans `WRITES`, l'entrée `ReportService: {"deposit"},` (avant `IpamService` en ordre libre; le dict n'est pas trié).

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_report_service.py tests/unit/test_service_guards.py -x`
Attendu: `AttributeError: 'ReportService' object has no attribute 'deposit'` (et, pour les gardes, `ReportService.deposit` attendu en `require_caller` tant que la méthode n'existe pas ou n'est pas en `WRITES`).

- [ ] **Step 3: Implémenter** (dans `services/reports.py`)

Imports à ajouter: `from ea.domain.files import MAX_FILE_TITLE_LENGTH, FileDetails`; dans le bloc `ea.domain.reports`: `report_key`; `from ea.services.caller import require_caller, require_editor`; sous `TYPE_CHECKING`: `from ea.domain.files import CatalogedFile`.

Méthode à insérer après `build`:

```python
    async def deposit(self, request: ReportRequest) -> CatalogedFile:
        """Build the report and store it in the bucket under `reports/`, with its record.

        The only door is `FileService.upload` (docs/adr/0036): authorisation, the
        50 MB ceiling and the object-then-row order (docs/adr/0039) are its. The
        key holds the date and a random suffix, so a regeneration is a new file
        and the record keeps its true generation date.
        """
        caller = require_editor()
        rendered = await self._produce(request, caller)
        template = template_by_id(request.template_id)
        now = self._now()
        name = report_filename(
            now=now,
            subject_name=rendered.subject_name,
            template_id=template.id,
            fmt=request.format,
            unique=self._new_id(),
        )
        title = f"Rapport {template.title} — {rendered.subject_name}"[:MAX_FILE_TITLE_LENGTH]
        stored = await self._files.upload(
            report_key(name, now),
            rendered.content,
            content_type=rendered.content_type,
            details=FileDetails(
                title=title,
                description=(
                    f"{request.subject.kind}:{request.subject.id} | "
                    f"modèle {template.id} v{template.version} | EA {self._version}"
                ),
                tags=("rapport", template.id),
            ),
        )
        self._audit(request, rendered, deposited=True)
        return stored
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_service.py tests/unit/test_service_guards.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/reports.py backend/tests/unit/test_report_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(reports): deposit a report in the bucket through FileService

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: API: schémas, routeur des rapports, `render.svg`, câblage, e2e

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (imports en tête; ajout en fin de fichier)
- Modify: `backend/src/ea/api/dependencies.py` (import l.17-22; ajout après `Files`, ~l.138)
- Create: `backend/src/ea/api/reports.py`
- Modify: `backend/src/ea/api/diagrams.py` (imports l.10-21; ajout en fin de fichier)
- Modify: `backend/src/ea/main.py` (imports l.20 et l.58-62; `_lifespan` après la création de `FileService` ~l.290; `create_app` signature l.437-448, corps ~l.507-509 et inclusion des routeurs l.530-537)
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)
- Create: `backend/tests/e2e/test_report_api.py`

**Interfaces:**
- Consumes: `ReportService` (Tasks 7-8), `FileRead.of`, `attachment` (`api/files.py`), `render_svg`, `ErrorResponse`.
- Produces:
  - `ReportTemplateId` (borne), `ReportSubjectBody`, `ReportCreate(template, subject, format)` avec `.request() -> ReportRequest`, `ReportTemplateRead(id, version, title, subjects, formats)` avec `.of(template, formats)`
  - `report_service_of(app)`, `get_report_service`, `Reports`
  - routes `GET /report-templates`, `GET /reports/render`, `POST /reports`, `GET /diagrams/{diagram_id}/render.svg`
  - `create_app(..., report_renderers: Mapping[ReportFormat, Renderer] | None = None)`

- [ ] **Step 1: Écrire les tests e2e qui échouent**

```python
"""The report endpoints end to end, over in-memory stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.archimate import ElementType
from ea.domain.diagrams import DiagramNode
from ea.domain.reports import MAX_REPORT_ELEMENTS
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryDocuments,
    InMemoryFileMetadata,
    InMemoryObjectStore,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


def an_app(service, diagrams, documents, files_store, files_metadata, **extra: Any):  # type: ignore[no-untyped-def]
    return create_app(
        Settings(debug=True, **extra.pop("settings", {"auth_enabled": False})),
        architecture_service=service,
        diagrams=diagrams,
        documents=documents,
        files=files_store,
        file_metadata=files_metadata,
        report_renderers={},
        **extra,
    )


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
    documents: InMemoryDocuments,
    files_store: InMemoryObjectStore,
    files_metadata: InMemoryFileMetadata,
) -> AsyncIterator[httpx.AsyncClient]:
    app = an_app(service, diagrams, documents, files_store, files_metadata)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def secured(
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
    documents: InMemoryDocuments,
    files_store: InMemoryObjectStore,
    files_metadata: InMemoryFileMetadata,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    app = an_app(
        service,
        diagrams,
        documents,
        files_store,
        files_metadata,
        settings={"auth_enabled": True},
        verifier=verifier,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def a_diagram(
    client: httpx.AsyncClient, *names: str, title: str = "Vente", headers: dict[str, str] | None = None
) -> str:
    headers = headers or {}
    ids = []
    for name in names:
        element = await client.post(
            "/elements",
            json={"element_type": "application_component", "name": name},
            headers=headers,
        )
        assert element.status_code == 201, element.text
        ids.append(element.json()["id"])
    diagram = await client.post("/diagrams", json={"name": title}, headers=headers)
    assert diagram.status_code == 201, diagram.text
    layout = {
        "nodes": [
            {"element_id": i, "x": 10.0 * n, "y": 20.0, "width": 200.0, "height": 80.0}
            for n, i in enumerate(ids)
        ]
    }
    placed = await client.put(f"/diagrams/{diagram.json()['id']}/layout", json=layout, headers=headers)
    assert placed.status_code == 204, placed.text
    return str(diagram.json()["id"])


def render_params(diagram_id: str, fmt: str = "html", template: str = "fiche-diagramme") -> dict[str, str]:
    return {"template": template, "subject_kind": "diagram", "subject_id": diagram_id, "format": fmt}


class TestTemplates:
    async def test_the_templates_name_their_subjects_and_the_formats_served(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/report-templates")

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": "fiche-diagramme",
                "version": 1,
                "title": "Fiche de diagramme",
                "subjects": ["diagram"],
                "formats": ["html"],
            }
        ]


class TestRendering:
    async def test_the_html_is_a_download_with_a_closed_policy(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram_id = await a_diagram(client, "Facturation")

        response = await client.get("/reports/render", params=render_params(diagram_id))

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment; filename*=UTF-8''")
        assert disposition.endswith("-vente-fiche-diagramme.html")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "Facturation" in response.text

    async def test_a_hostile_diagram_name_cannot_inject_a_header_or_markup(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram_id = await a_diagram(
            client, "<script>x</script>", title='V"; filename="evil.exe\r\nX-Evil: 1'
        )

        response = await client.get("/reports/render", params=render_params(diagram_id))

        assert response.status_code == 200
        assert "x-evil" not in response.headers
        assert "\r" not in response.headers["content-disposition"]
        assert "<script>" not in response.text

    async def test_an_empty_diagram_is_a_report_not_an_error(
        self, client: httpx.AsyncClient
    ) -> None:
        created = await client.post("/diagrams", json={"name": "Vide"})

        response = await client.get("/reports/render", params=render_params(created.json()["id"]))

        assert response.status_code == 200
        assert "Aucun élément." in response.text

    async def test_the_refusals_are_typed(self, client: httpx.AsyncClient) -> None:
        diagram_id = await a_diagram(client, "A")

        missing = await client.get("/reports/render", params=render_params(str(uuid4())))
        template = await client.get("/reports/render", params=render_params(diagram_id, template="nope"))
        pdf = await client.get("/reports/render", params=render_params(diagram_id, "pdf"))
        bad_kind = await client.get(
            "/reports/render", params={**render_params(diagram_id), "subject_kind": "package"}
        )
        no_params = await client.get("/reports/render")

        assert (missing.status_code, missing.json()["error"]) == (404, "not_found")
        assert (template.status_code, template.json()["error"]) == (404, "not_found")
        assert (pdf.status_code, pdf.json()["error"]) == (501, "format_unavailable")
        assert (bad_kind.status_code, bad_kind.json()["error"]) == (422, "unsupported_subject")
        assert no_params.status_code == 422

    async def test_the_ceiling_is_a_422_and_writes_nothing(
        self,
        client: httpx.AsyncClient,
        service: ArchitectureService,
        diagrams: InMemoryDiagrams,
        files_store: InMemoryObjectStore,
    ) -> None:
        # The layout endpoint itself refuses past 500 boxes (MAX_DIAGRAM_NODES), so a
        # 501-element diagram can only come from the service: seed it there.
        use_cases = DiagramService(diagrams, service)  # type: ignore[arg-type]
        diagram = await use_cases.create(name="Gros")
        elements = [
            await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=f"E{i}")
            for i in range(MAX_REPORT_ELEMENTS + 1)
        ]
        await use_cases.replace_layout(
            diagram.id, [DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)]
        )

        response = await client.post(
            "/reports",
            json={
                "template": "fiche-diagramme",
                "subject": {"kind": "diagram", "id": str(diagram.id)},
                "format": "html",
            },
        )

        assert (response.status_code, response.json()["error"]) == (422, "report_too_large")
        assert files_store.objects == {}


class TestDepositing:
    async def test_an_editor_deposits_and_the_record_is_filled(
        self, client: httpx.AsyncClient, files_store: InMemoryObjectStore
    ) -> None:
        diagram_id = await a_diagram(client, "Facturation")
        body = {
            "template": "fiche-diagramme",
            "subject": {"kind": "diagram", "id": diagram_id},
            "format": "html",
        }

        first = await client.post("/reports", json=body)
        second = await client.post("/reports", json=body)

        assert first.status_code == 201, first.text
        key = first.json()["key"]
        assert key.startswith("reports/") and key.endswith(".html")
        assert first.json()["metadata"]["tags"] == ["rapport", "fiche-diagramme"]
        assert first.json()["metadata"]["title"] == "Rapport Fiche de diagramme — Vente"
        assert second.json()["key"] != key
        assert len(files_store.objects) == 2

    async def test_extra_fields_in_the_body_are_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/reports",
            json={
                "template": "fiche-diagramme",
                "subject": {"kind": "diagram", "id": str(uuid4())},
                "format": "html",
                "key": "inbox/x.html",
            },
        )

        assert response.status_code == 422


class TestWhoMay:
    async def test_no_token_is_a_401_everywhere(self, secured: httpx.AsyncClient) -> None:
        assert (await secured.get("/report-templates")).status_code == 401
        assert (await secured.get("/reports/render")).status_code == 401
        assert (await secured.post("/reports", json={})).status_code == 401
        assert (await secured.get(f"/diagrams/{uuid4()}/render.svg")).status_code == 401

    async def test_a_reader_downloads_but_cannot_deposit(
        self, secured: httpx.AsyncClient, files_store: InMemoryObjectStore
    ) -> None:
        diagram_id = await a_diagram(secured, "A", headers=bearer(EDITOR))

        render = await secured.get(
            "/reports/render", params=render_params(diagram_id), headers=bearer(READER)
        )
        deposit = await secured.post(
            "/reports",
            json={
                "template": "fiche-diagramme",
                "subject": {"kind": "diagram", "id": diagram_id},
                "format": "html",
            },
            headers=bearer(READER),
        )

        assert render.status_code == 200
        assert (deposit.status_code, deposit.json()["error"]) == (403, "forbidden")
        assert files_store.objects == {}


class TestTheSvgRoute:
    async def test_the_drawing_is_served_closed_and_cacheable_by_content(
        self, client: httpx.AsyncClient
    ) -> None:
        diagram_id = await a_diagram(client, "Facturation")

        response = await client.get(f"/diagrams/{diagram_id}/render.svg")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.headers["content-security-policy"] == (
            "default-src 'none'; style-src 'unsafe-inline'"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.text.startswith("<svg")
        again = await client.get(
            f"/diagrams/{diagram_id}/render.svg",
            headers={"If-None-Match": response.headers["etag"]},
        )
        assert again.status_code == 304

    async def test_a_hostile_name_is_text_in_the_drawing(self, client: httpx.AsyncClient) -> None:
        diagram_id = await a_diagram(client, "<script>alert(1)</script>")

        response = await client.get(f"/diagrams/{diagram_id}/render.svg")

        assert "<script>" not in response.text
        assert "&lt;script&gt;" in response.text

    async def test_an_unknown_diagram_is_a_404(self, client: httpx.AsyncClient) -> None:
        assert (await client.get(f"/diagrams/{uuid4()}/render.svg")).status_code == 404
```

- [ ] **Step 1: Lancer, échec attendu** (`TypeError: create_app() got an unexpected keyword argument 'report_renderers'`)

Run: `cd backend && uv run pytest tests/e2e/test_report_api.py -x`

- [ ] **Step 1: Implémenter**

`backend/src/ea/api/schemas.py`: dans les imports du domaine (haut du fichier, à côté des imports `ea.domain.*`), ajouter

```python
from ea.domain.reports import (
    ReportFormat,
    ReportRequest,
    ReportSubject,
    ReportTemplate,
    SubjectKind,
)
```

et en fin de fichier:

```python
# --- Reports (docs/adr/0042) -------------------------------------------------

#: A built-in template's id: a slug. Declared once; the MCP tools import it.
ReportTemplateId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description="A template id from GET /report-templates.",
    ),
]


class ReportSubjectBody(_Input):
    """What the report is about: a kind and the id of one object of that kind."""

    kind: SubjectKind
    id: UUID


class ReportCreate(_Input):
    template: ReportTemplateId
    subject: ReportSubjectBody
    format: ReportFormat

    def request(self) -> ReportRequest:
        return ReportRequest(
            template_id=self.template,
            subject=ReportSubject(kind=self.subject.kind, id=self.subject.id),
            format=self.format,
        )


class ReportTemplateRead(BaseModel):
    id: str
    version: int
    title: str
    subjects: list[SubjectKind] = Field(description="The kinds of subject the template takes.")
    formats: list[ReportFormat] = Field(description="The formats this deployment can produce.")

    @classmethod
    def of(cls, template: ReportTemplate, formats: Sequence[ReportFormat]) -> ReportTemplateRead:
        return cls(
            id=template.id,
            version=template.version,
            title=template.title,
            subjects=sorted(template.subjects),
            formats=list(formats),
        )
```

Vérifier que `Sequence` est importé (`from collections.abc import Sequence`) en tête de `schemas.py`; sinon l'ajouter.

`backend/src/ea/api/dependencies.py`: ajouter `from ea.services.reports import ReportService` (ordre alphabétique) et, après `Files = ...`:

```python
def report_service_of(app: FastAPI) -> ReportService:
    """The report use cases the lifespan attached, or a clear failure.

    Absent means the app was assembled without diagrams or without a file
    service: a wiring fault and a 500, never an empty template list.
    """
    service: ReportService | None = getattr(app.state, "report_service", None)
    if service is None:
        msg = "no report service on the application — are diagrams and files attached?"
        raise RuntimeError(msg)
    return service


def get_report_service(request: Request) -> ReportService:
    return report_service_of(request.app)


Reports = Annotated[ReportService, Depends(get_report_service)]
```

`backend/src/ea/api/reports.py`:

```python
"""The report endpoints (docs/adr/0042).

`GET /reports/render` writes nothing, so any caller can have a PDF without
touching the bucket; `POST /reports` deposits one under `reports/` and is for
editors, a rule the service enforces and the router never restates. Downloads
are always attachments with a sanitised name and a closed policy.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Reports
from ea.api.files import attachment
from ea.api.schemas import (
    ErrorResponse,
    FileRead,
    ReportCreate,
    ReportTemplateId,
    ReportTemplateRead,
)
from ea.domain.reports import CONTENT_TYPES, ReportFormat, ReportRequest, ReportSubject, SubjectKind

router = APIRouter(tags=["reports"])

_ERROR = {"model": ErrorResponse}
REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: _ERROR,
    status.HTTP_501_NOT_IMPLEMENTED: _ERROR,
}
_DOWNLOAD: dict[int | str, dict[str, Any]] = {
    status.HTTP_200_OK: {
        "description": "The report, as an attachment.",
        "content": {
            content_type: {"schema": {"type": "string", "format": "binary"}}
            for content_type in CONTENT_TYPES.values()
        },
    },
    **REFUSED,
}


@router.get("/report-templates", response_model=list[ReportTemplateRead])
async def list_report_templates(reports: Reports) -> list[ReportTemplateRead]:
    """The built-in templates, with the subjects they take and the formats served."""
    return [
        ReportTemplateRead.of(template, reports.formats)
        for template in await reports.list_templates()
    ]


@router.get("/reports/render", response_class=Response, responses=_DOWNLOAD)
async def render_report(
    reports: Reports,
    template: Annotated[ReportTemplateId, Query()],
    subject_kind: Annotated[SubjectKind, Query()],
    subject_id: Annotated[UUID, Query()],
    report_format: Annotated[ReportFormat, Query(alias="format")],
) -> Response:
    """Build a report and return it. Nothing is stored."""
    rendered = await reports.build(
        ReportRequest(template, ReportSubject(subject_kind, subject_id), report_format)
    )
    return Response(
        content=rendered.content,
        media_type=rendered.content_type,
        headers={
            "Content-Disposition": attachment(rendered.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        },
    )


@router.post(
    "/reports", response_model=FileRead, status_code=status.HTTP_201_CREATED, responses=REFUSED
)
async def create_report(body: ReportCreate, reports: Reports) -> FileRead:
    """Build a report and deposit it in the bucket under `reports/`. Editors only."""
    return FileRead.of(await reports.deposit(body.request()))
```

`backend/src/ea/api/diagrams.py`: imports `from hashlib import sha256`, `from fastapi import APIRouter, Request, Response, status`, `from ea.domain.diagram_render import render_svg`; ajouter en fin:

```python
_SVG_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-cache",
}


@router.get(
    "/diagrams/{diagram_id}/render.svg",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "The diagram as a static SVG (docs/adr/0043).",
            "content": {"image/svg+xml": {"schema": {"type": "string"}}},
        },
        **NOT_FOUND,
    },
)
async def render_diagram(diagram_id: UUID, diagrams: Diagrams, request: Request) -> Response:
    """The one static drawing of a diagram, cacheable by content (`ETag`)."""
    svg = render_svg(await diagrams.open(diagram_id))
    etag = f'"{sha256(svg.encode()).hexdigest()[:32]}"'
    headers = {**_SVG_HEADERS, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(svg, media_type="image/svg+xml", headers=headers)
```

`backend/src/ea/main.py`:
- Import `from ea.api.reports import router as reports_router` (à côté des autres routeurs), `from ea.services.reports import ReportService`, `from ea.domain.reports import ReportFormat, Renderer`, `from collections.abc import Mapping` si absent.
- Nouvelle fonction (avant `_mount_mcp`):

```python
def _attach_reports(app: FastAPI) -> None:
    """The report use cases, once the services they read and write through exist.

    Called wherever one of those can appear last: `create_app` for the injected
    test seams, the lifespan for the real stores. Idempotent.
    """
    diagrams = getattr(app.state, "diagram_service", None)
    files = getattr(app.state, "file_service", None)
    if diagrams is None or files is None or getattr(app.state, "report_service", None):
        return
    app.state.report_service = ReportService(
        diagrams,
        getattr(app.state, "document_service", None),
        files,
        extra_renderers=getattr(app.state, "report_renderers", None),
        app_version=app.version,
    )
```

- Dans `_lifespan`, juste après `app.state.file_service = FileService(store, metadata=metadata_store)` (dans le `if getattr(app.state, "file_service", None) is None:`), ajouter au niveau de ce bloc (hors du `if`, pour couvrir aussi un `file_service` injecté) la ligne `_attach_reports(app)`, placée avant `app.state.degraded = tuple(degraded)`.
- `create_app`: nouveau paramètre `report_renderers: Mapping[ReportFormat, Renderer] | None = None,` (après `verifier`), documenté dans la docstring (« `report_renderers` does the same for the PDF/DOCX engines: given a mapping — `{}` in most tests — only those formats are offered »). Après `app.state.settings = settings`: `app.state.report_renderers = report_renderers`. Après le bloc `if files is not None: app.state.file_service = FileService(...)`: `_attach_reports(app)`. Inclusion: `app.include_router(reports_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)` après `files_router`.

Note: `report_renderers=None` signifie « rien » au lot 1 (HTML seul); le lot 2 (Task 18) remplace ce `None` par la détection des moteurs installés.

- [ ] **Step 1: Relancer, PASS; régénérer le contrat**

Run: `cd backend && uv run pytest tests/e2e/test_report_api.py tests/unit -x -q` (la suite unitaire entière: `test_openapi.py` et `test_auth_api.py` couvrent « toute route sauf `/health` veut un jeton »).
Run: `make openapi` puis `make openapi-check`
Run: `cd frontend && npm run typecheck`

- [ ] **Step 1: Commit**

```bash
git add backend/src/ea/api/schemas.py backend/src/ea/api/dependencies.py backend/src/ea/api/reports.py backend/src/ea/api/diagrams.py backend/src/ea/main.py backend/tests/e2e/test_report_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(reports): report endpoints and the static diagram SVG route

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `/metamodel` expose la palette du dessin

**Files:**
- Modify: `backend/src/ea/api/schemas.py` (classe `MetamodelRead`, l.~284-305; nouvelle classe avant elle)
- Create: `backend/tests/unit/test_metamodel_palette.py`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)
- Modify (si `vue-tsc` l'exige): `frontend/tests/support/api.ts` (constante `METAMODEL`)

**Interfaces:**
- Consumes: `LAYER_FILL`, `BOX_INK` (Task 2).
- Produces: `LayerStyleRead(layer, fill)`; `MetamodelRead.layer_styles: list[LayerStyleRead]`, `MetamodelRead.box_ink: str`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
"""The palette the static drawing paints with is served by /metamodel."""

from __future__ import annotations

from ea.api.schemas import MetamodelRead
from ea.domain.archimate import Layer
from ea.domain.diagram_render import BOX_INK, LAYER_FILL


def test_the_metamodel_serves_the_fills_the_svg_uses() -> None:
    snapshot = MetamodelRead.snapshot()

    assert {style.layer: style.fill for style in snapshot.layer_styles} == dict(LAYER_FILL)
    assert [style.layer for style in snapshot.layer_styles] == list(Layer)
    assert snapshot.box_ink == BOX_INK
```

- [ ] **Step 2: Lancer, échec attendu** (`AttributeError: ... 'layer_styles'`)

Run: `cd backend && uv run pytest tests/unit/test_metamodel_palette.py -x`

- [ ] **Step 3: Implémenter**

Dans `schemas.py`, importer `from ea.domain.diagram_render import BOX_INK, LAYER_FILL`, ajouter avant `MetamodelRead`:

```python
class LayerStyleRead(BaseModel):
    """The fill an ArchiMate layer is drawn with: the static drawing's palette."""

    layer: Layer
    fill: str = Field(description="A CSS hex colour, light enough for dark ink.")
```

et dans `MetamodelRead` les champs `layer_styles: list[LayerStyleRead]` et `box_ink: str = Field(description="The ink to draw text on those fills with.")`; dans `snapshot()`, ajouter à l'appel `cls(...)`:

```python
            layer_styles=[LayerStyleRead(layer=layer, fill=LAYER_FILL[layer]) for layer in Layer],
            box_ink=BOX_INK,
```

- [ ] **Step 4: Relancer, PASS; régénérer; typecheck**

Run: `cd backend && uv run pytest tests/unit/test_metamodel_palette.py tests/unit/test_mcp_server.py -x` puis `make openapi` et `cd frontend && npm run typecheck && npm test -- --run`.
Si `vue-tsc` signale `METAMODEL` dans `frontend/tests/support/api.ts` (champs requis manquants), ajouter à la constante `layer_styles: [] as { layer: string; fill: string }[]` et `box_ink: '#24202b'`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/api/schemas.py backend/tests/unit/test_metamodel_palette.py backend/openapi.json frontend/src/api/schema.d.ts frontend/tests/support/api.ts
git commit -m "feat(metamodel): serve the layer fills the static drawing paints with

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Sujet « paquet » et modèle « Inventaire d'un paquet » (gardé: sous-projet 1)

**Files:**
- Modify: `backend/src/ea/domain/reports.py` (nouveau `PackageContents`, gabarit `PACKAGE_INVENTORY`, `BUILT_IN_TEMPLATES`)
- Modify: `backend/src/ea/services/reports.py` (paramètre `packages`, branche `package` de `_scope`)
- Modify: `backend/src/ea/main.py` (`_attach_reports`: adaptateur du sous-projet 1)
- Modify: `backend/tests/unit/test_reports_domain.py`, `backend/tests/unit/test_report_service.py`

**Interfaces:**
- Consumes: le service de lecture de paquets du sous-projet 1 (nom et méthodes à relever à l'étape 1).
- Produces:
  - `PackageContents(name, description, elements: tuple[Element, ...], relationships: tuple[Relationship, ...], diagram_ids: tuple[UUID, ...])` dans `domain/reports.py`
  - `PackageReader` (Protocol: `async def contents(self, package_id: UUID) -> PackageContents`, lève `ElementNotFoundError`-équivalent 404 du sous-projet 1 pour un paquet inconnu)
  - gabarit `inventaire-paquet` (sujets `{"package"}`)

- [ ] **Step 1: Vérifier que le sous-projet 1 est livré (porte d'entrée)**

Run: `grep -rln "class Package\|packages" backend/src/ea/domain backend/src/ea/services | head` et `ls backend/migrations/versions | tail -5`.
Si rien n'est trouvé: **s'arrêter ici**, laisser cette tâche non faite, la consigner dans l'ADR 0042 (« sujet paquet: à livrer avec le sous-projet 1 ») et passer à la Task 12. Si le sous-projet est livré: relever le service de lecture d'un paquet et ses méthodes (contenu direct et sous-paquets, diagrammes rattachés) pour l'adaptateur de l'étape 6.

- [ ] **Step 2: Écrire les tests qui échouent**

Dans `test_reports_domain.py`:

```python
class TestThePackageTemplate:
    def test_the_inventory_takes_a_package_and_lists_elements_relations_and_diagrams(self) -> None:
        inventory = template_by_id("inventaire-paquet")

        assert inventory.subjects == frozenset({"package"})
        assert inventory.needs_documents is False

    def test_a_package_with_two_diagrams_draws_both(self) -> None:
        inventory = template_by_id("inventaire-paquet")
        scope = a_scope()
        two = ReportScope("package", "Finance", "", scope.diagrams * 2, (), ())

        model = build_model(inventory, two, (), now=NOW)

        assert len([b for b in model.blocks if isinstance(b, Figure)]) == 2
```

Dans `test_report_service.py`:

```python
class FakePackages:
    def __init__(self, contents: PackageContents | None) -> None:
        self.contents_of = contents

    async def contents(self, package_id: UUID) -> PackageContents:
        if self.contents_of is None:
            raise ElementNotFoundError("no package")
        return self.contents_of


class TestPackages:
    async def test_a_package_report_covers_its_elements_and_diagrams(
        self, service, diagram_service, file_service
    ) -> None:
        diagram, elements = await a_diagram(service, diagram_service, "Facturation")
        packages = FakePackages(
            PackageContents("Finance", "Le SI finance", tuple(elements), (), (diagram.id,))
        )
        reports = ReportService(diagram_service, None, file_service, packages=packages)
        request = ReportRequest("inventaire-paquet", ReportSubject("package", uuid4()), "html")

        html = (await reports.build(request)).content.decode()

        assert "Finance" in html and "Facturation" in html and "<svg" in html

    async def test_more_than_20_diagrams_is_refused(self, diagram_service, file_service) -> None:
        contents = PackageContents("Gros", "", (), (), tuple(uuid4() for _ in range(21)))
        reports = ReportService(
            diagram_service, None, file_service, packages=FakePackages(contents)
        )
        request = ReportRequest("inventaire-paquet", ReportSubject("package", uuid4()), "html")

        with pytest.raises(ReportTooLargeError, match="20"):
            await reports.build(request)

    async def test_more_than_500_elements_in_a_package_is_refused(
        self, service, diagram_service, file_service
    ) -> None:
        _, elements = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )
        contents = PackageContents("Gros", "", tuple(elements), (), ())
        reports = ReportService(
            diagram_service, None, file_service, packages=FakePackages(contents)
        )
        request = ReportRequest("inventaire-paquet", ReportSubject("package", uuid4()), "html")

        with pytest.raises(ReportTooLargeError, match="500"):
            await reports.build(request)

    async def test_without_the_packages_feature_the_kind_is_unsupported(
        self, diagram_service, file_service
    ) -> None:
        reports = ReportService(diagram_service, None, file_service)
        request = ReportRequest("inventaire-paquet", ReportSubject("package", uuid4()), "html")

        with pytest.raises(UnsupportedReportSubjectError):
            await reports.build(request)
```

(imports à ajouter: `from uuid import UUID`, `ElementNotFoundError`, `PackageContents`, `MAX_REPORT_ELEMENTS` déjà là.)

- [ ] **Step 3: Lancer, échec attendu** (`ImportError: PackageContents`)

Run: `cd backend && uv run pytest tests/unit/test_reports_domain.py tests/unit/test_report_service.py -x`

- [ ] **Step 4: Implémenter le domaine** (`domain/reports.py`)

Après `ReportScope`:

```python
@dataclass(frozen=True, slots=True)
class PackageContents:
    """What reading a package yields: its elements, the links among them, its diagrams.

    Produced by the package feature (sub-project 1) through the `PackageReader`
    port; this module only consumes it, so the report cannot redefine what a
    package contains.
    """

    name: str
    description: str
    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]
    diagram_ids: tuple[UUID, ...]
```

Après `DIAGRAM_SHEET`:

```python
PACKAGE_INVENTORY: Final = ReportTemplate(
    id="inventaire-paquet",
    version=1,
    title="Inventaire d'un paquet",
    subjects=frozenset({"package"}),
    sections=(
        TextSection("Inventaire du paquet **{{subject.name}}**.\n\n{{subject.description}}"),
        ElementTableSection(columns=("name", "type", "layer", "description")),
        RelationsSection(),
        DiagramSection(),
    ),
)
```

et `BUILT_IN_TEMPLATES = MappingProxyType({DIAGRAM_SHEET.id: DIAGRAM_SHEET, PACKAGE_INVENTORY.id: PACKAGE_INVENTORY})`.

- [ ] **Step 5: Implémenter le service** (`services/reports.py`)

Imports: `from typing import Protocol`, `from uuid import UUID`, `MAX_REPORT_DIAGRAMS`, `PackageContents` (domain). Port, avant la classe:

```python
class PackageReader(Protocol):
    """Reads one package. The package feature implements it (sub-project 1)."""

    async def contents(self, package_id: UUID) -> PackageContents: ...
```

Constructeur: paramètre keyword `packages: PackageReader | None = None` stocké dans `self._packages`. `_scope` devient:

```python
    async def _scope(self, subject: ReportSubject) -> ReportScope:
        if subject.kind == "package":
            return await self._package_scope(subject.id)
        if subject.kind != "diagram":
            msg = f"reports on a {subject.kind!r} are not available on this deployment"
            raise UnsupportedReportSubjectError(msg)
        ...  # corps diagramme inchangé
```

```python
    async def _package_scope(self, package_id: UUID) -> ReportScope:
        if self._packages is None:
            msg = "reports on a package are not available on this deployment"
            raise UnsupportedReportSubjectError(msg)
        contents = await self._packages.contents(package_id)
        if len(contents.elements) > MAX_REPORT_ELEMENTS:
            msg = (
                f"a report covers at most {MAX_REPORT_ELEMENTS} elements and this package "
                f"has {len(contents.elements)}; report on a sub-package"
            )
            raise ReportTooLargeError(msg)
        if len(contents.diagram_ids) > MAX_REPORT_DIAGRAMS:
            msg = (
                f"a report draws at most {MAX_REPORT_DIAGRAMS} diagrams and this package "
                f"has {len(contents.diagram_ids)}; report on a sub-package"
            )
            raise ReportTooLargeError(msg)
        diagrams = tuple([await self._diagrams.open(i) for i in contents.diagram_ids])
        return ReportScope(
            kind="package",
            name=contents.name,
            description=contents.description,
            diagrams=diagrams,
            elements=contents.elements,
            relationships=contents.relationships,
        )
```

- [ ] **Step 6: Câbler l'adaptateur du sous-projet 1** (`main._attach_reports`)

Ajouter `packages=getattr(app.state, "package_reader", None)` à l'appel `ReportService(...)`. Le sous-projet 1 pose `app.state.package_reader` (un objet à une méthode `async contents(package_id) -> PackageContents`) au moment où il câble son service; si au moment de l'exécution il expose son service autrement, l'adaptateur (≈ 10 lignes: lire le paquet, lister ses éléments et ses liens internes, ses diagrammes rattachés) est écrit là, avec son test, contre les noms réellement livrés relevés à l'étape 1.

- [ ] **Step 7: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_reports_domain.py tests/unit/test_report_service.py tests/e2e/test_report_api.py -x && uv run mypy src && uv run ruff check src tests`
Mettre à jour `test_the_templates_name_their_subjects...` de `test_report_api.py` si le gabarit `inventaire-paquet` apparaît dans `/report-templates` (la liste contient alors les deux gabarits: ajuster l'attendu).

- [ ] **Step 8: Commit**

```bash
git add backend/src/ea/domain/reports.py backend/src/ea/services/reports.py backend/src/ea/main.py backend/tests/unit/test_reports_domain.py backend/tests/unit/test_report_service.py backend/tests/e2e/test_report_api.py
git commit -m "feat(reports): package subject and the package inventory template

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Intégration: dépôt dans un vrai MinIO, plafond sur un vrai PostgreSQL

**Files:**
- Create: `backend/tests/integration/test_reports_deposit.py`

**Interfaces:**
- Consumes: fixtures `graph_service` (PostgreSQL au schéma `head`) et `minio_store` de `tests/integration/conftest.py`; `InMemoryDiagrams`, `InMemoryFileMetadata`.

- [ ] **Step 1: Écrire le test**

```python
"""A report deposited in a real bucket; a too-large one leaves the bucket untouched."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import ReportTooLargeError
from ea.domain.reports import MAX_REPORT_ELEMENTS, ReportRequest, ReportSubject
from ea.repositories.object_store import MinioObjectStore
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from ea.services.files import FileService
from ea.services.reports import ReportService
from tests.conftest import InMemoryDiagrams, InMemoryFileMetadata

pytestmark = pytest.mark.asyncio


async def a_report_service(
    graph_service: ArchitectureService, minio_store: MinioObjectStore
) -> tuple[ReportService, DiagramService]:
    diagrams = DiagramService(InMemoryDiagrams(), graph_service)  # type: ignore[arg-type]
    files = FileService(minio_store, metadata=InMemoryFileMetadata())
    return ReportService(diagrams, None, files), diagrams


async def a_diagram(
    graph_service: ArchitectureService, diagrams: DiagramService, count: int
) -> ReportRequest:
    elements = [
        await graph_service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name=f"E{i}"
        )
        for i in range(count)
    ]
    diagram = await diagrams.create(name="Vente")
    await diagrams.replace_layout(
        diagram.id, [DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)]
    )
    return ReportRequest("fiche-diagramme", ReportSubject("diagram", diagram.id), "html")


async def test_a_deposited_report_is_a_real_object_under_reports(
    graph_service: ArchitectureService, minio_store: MinioObjectStore
) -> None:
    reports, diagrams = await a_report_service(graph_service, minio_store)
    request = await a_diagram(graph_service, diagrams, 3)

    stored = await reports.deposit(request)

    found = await minio_store.stat(stored.key)
    assert found is not None
    assert stored.key.startswith("reports/")
    assert found.content_type == "text/html; charset=utf-8"
    _, chunks = await minio_store.open(stored.key)
    assert b"E0" in b"".join([chunk async for chunk in chunks])


async def test_a_report_past_the_ceiling_leaves_the_bucket_empty(
    graph_service: ArchitectureService, minio_store: MinioObjectStore
) -> None:
    reports, diagrams = await a_report_service(graph_service, minio_store)
    request = await a_diagram(graph_service, diagrams, MAX_REPORT_ELEMENTS + 1)

    with pytest.raises(ReportTooLargeError):
        await reports.deposit(request)

    listing = await minio_store.list_folder("reports/", limit=10)
    assert listing.files == () and listing.folders == ()
```

- [ ] **Step 2: Lancer contre les conteneurs jetables**

Run: `make test-integration` (démarre PostgreSQL 5433 et MinIO 9100 jetables; jamais les stocks partagés). Attendu: PASS. Si Docker est absent de la machine d'exécution, le test se saute et la tâche reste ouverte jusqu'à ce que l'utilisateur lance la commande.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_reports_deposit.py
git commit -m "test(reports): deposit into a real bucket and ceiling on real PostgreSQL

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: ADR du lot 1 (en entier)

**Files:**
- Create: `docs/adr/0042-generation-de-documents.md`
- Create: `docs/adr/0043-un-seul-dessin-de-diagramme.md`

Avant d'écrire: `ls docs/adr | tail -3`. Les numéros 0042, 0043 (et 0044 au lot 3) sont ceux réservés par ce plan; s'ils sont pris, décaler les trois, puis `grep -rn "docs/adr/0042\|docs/adr/0043\|docs/adr/0044" backend frontend docs/adr` et corriger les références (commentaires de code) dans le même commit.

- [ ] **Step 1: Écrire `docs/adr/0042-generation-de-documents.md`**

```markdown
---
titre: Génération de documents (rapports HTML, PDF, DOCX)
date: 2026-10-10
statut: Proposition
affects: domain/reports.py, domain/report_html.py, domain/markdown_html.py, services/reports.py, api/reports.py, repositories/report_renderers.py, backend/Dockerfile, pyproject.toml
---

# Génération de documents (rapports HTML, PDF, DOCX)

## Contexte

Le catalogue sait se lire à l'écran; il ne sait rien produire qu'on puisse envoyer, imprimer ou archiver. Les outils de modélisation du marché (Sparx EA et son Document Generator) génèrent des rapports datés d'un paquet ou d'un diagramme. Trois nouvelles dépendances sont en jeu (rendu markdown, PDF, DOCX) et l'image `backend/Dockerfile` doit changer (ADR 0027): la décision exige donc un ADR.

## Décision

1. **Un seul document intermédiaire.** `ReportModel` (titres, paragraphes, tables, figures SVG, blocs markdown) est construit par une fonction pure du domaine (`build_model`) à partir d'un gabarit et d'un périmètre lu par `ReportService`. HTML, PDF et DOCX sont trois sérialiseurs de cet objet: la logique de contenu existe une fois.
2. **Gabarits intégrés, déclaratifs, sans évaluation.** Deux gabarits en constantes du domaine (« Fiche de diagramme », « Inventaire d'un paquet » dès que le sous-projet des paquets existe). Seule substitution: `{{subject.name|description|kind}}` par table fermée, valeur échappée en markdown. Pas de Jinja. Aucune table, aucune migration; la forme (`id`, `version`, `sections` discriminées) est celle qu'une table stockerait si l'édition de gabarits devient un besoin.
3. **Dépendances.** `markdown-it-py` (rendu markdown, HTML brut et images désactivés, schémas dangereux refusés; déjà verrouillé via `rich`); `weasyprint` (PDF à partir du HTML autonome); `python-docx` (DOCX). Le HTML autonome est toujours livrable: un déploiement sans les bibliothèques natives répond 501 `format_unavailable` pour `pdf`/`docx`, et `GET /report-templates` liste les formats réellement servis.
4. **Image Docker.** L'étage final ajoute `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0` et `fonts-dejavu-core` (environ 80 Mo). Les deux `FROM` partagent la même version de Python (rappel: le venv copié pointe `/usr/local/bin/python3.12`). La police est embarquée (DejaVu), jamais les polices du système.
5. **Génération synchrone et plafonnée.** 500 éléments, 20 diagrammes, 2 MiB de documents cumulés, 30 s (horloge monotone injectée, vérifiée entre les étapes, plus `asyncio.wait_for` autour du thread de rendu), un seul rapport simultané par `sub` (429). Un dépassement est une erreur typée 422 `report_too_large`, jamais une troncature.
6. **Dépôt.** `POST /reports` dépose par `FileService.upload` sous `reports/<AAAA>/<date>-<sujet>-<modèle>-<id8>.<ext>` (ADR 0036, 0039), avec une fiche remplie (appelant, date, tags `rapport` et le modèle). Jamais d'URL présignée. `GET /reports/render` ne persiste rien.
7. **Sécurité.** Tout texte saisi est échappé; WeasyPrint reçoit un récupérateur d'URL qui refuse tout; les téléchargements sont des pièces jointes avec une politique de contenu fermée; l'écriture exige `ea-editor` (ADR 0032), décidée dans `services/`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Chromium sans tête | Rendu identique au navigateur | ~400 Mo dans l'image, bac à sable, lent, non déterministe | Écarté |
| HTML imprimable seul | Aucune dépendance | La personne doit imprimer; pas de DOCX | Gardé comme repli, pas comme solution |
| Worker Prefect | Pas de limite de durée | `pipelines/` non déployé (ADR 0028 Proposition); jeton de service, pas l'appelant | Écarté tant que les plafonds bornent la durée |
| Table de gabarits dès maintenant | Gabarits éditables | Aucune demande; une migration pour rien | Reporté (lot 5 éventuel) |
| Jinja | Gabarits souples | Évaluation d'expressions libres: injection | Écarté |

## Conséquences

- L'image API grossit d'environ 80 Mo et son build dépend de paquets Debian: la vérification du build se fait sur la machine de l'utilisateur (`docker build`), jamais déduite des tests. Les tests PDF se sautent si WeasyPrint ne se charge pas (Mac sans `brew install pango`); la CI installe les paquets.
- Le CJK s'affiche en carrés vides dans le PDF (DejaVu couvre latin, grec, cyrillique). Ajouter une police CJK pèse plusieurs dizaines de Mo: décision à prendre si le besoin apparaît.
- Un thread de rendu bloqué ne peut pas être tué; les plafonds bornent son travail. Si un plafond doit monter, c'est le signal de passer en asynchrone, pas avant.
- `proxy_read_timeout` de `location /api/` passe à 60 s dans le dépôt Infra (hors de ce dépôt): à faire avant de compter sur des rapports proches de 30 s.
- Le DOCX montre le markdown brut des documents et remplace un diagramme par une note renvoyant au HTML/PDF (voir le lot 4).
- Des rapports s'accumulent dans `reports/`: aucune purge automatique; l'éditeur supprime par le navigateur de fichiers.

## Références

- ADR liés: [0027](0027-deploiement-via-portainer-et-nginx-de-l-infra.md), [0032](0032-authentification-keycloak.md), [0036](0036-fichiers-dans-minio.md), [0039](0039-metadonnees-des-fichiers-dans-postgresql.md), [0043](0043-un-seul-dessin-de-diagramme.md)
- Code concerné: `backend/src/ea/domain/reports.py`, `backend/src/ea/services/reports.py`, `backend/src/ea/api/reports.py`, `backend/Dockerfile`
```

Avant de sauver: remplacer les noms de fichiers des liens vers 0027 et 0032 par ceux qui existent réellement (`ls docs/adr | grep -E "^00(27|32)"`).

- [ ] **Step 2: Écrire `docs/adr/0043-un-seul-dessin-de-diagramme.md`**

```markdown
---
titre: Un seul dessin statique de diagramme
date: 2026-10-10
statut: Proposition
affects: domain/diagram_render.py, api/diagrams.py, api/schemas.py (MetamodelRead), frontend/src/lib/diagramGeometry.ts, frontend/tests/diagramParity.spec.ts
---

# Un seul dessin statique de diagramme

## Contexte

Un diagramme a deux lecteurs qui ne passent pas par l'éditeur: le rapport (HTML, PDF) et le mode lecteur du SPA. S'ils dessinaient chacun, il y aurait trois dessins à tenir à l'identique, dont deux jamais regardés par la personne qui modifie l'éditeur. L'éditeur interactif (`DiagramCanvas.vue`) garde de toute façon sa géométrie TypeScript pour la manipulation (glisser, redimensionner, relier).

## Décision

1. **`domain/diagram_render.py` est l'unique dessin statique.** `render_svg(detail: DiagramDetail) -> str`: pur, déterministe (aucune horloge, aucun aléa), échappement XML strict, sans élément `<style>` (attributs de présentation seulement, pour qu'une CSP fermée suffise). Le rapport l'incorpore, `GET /diagrams/{id}/render.svg` le sert (`ETag` = hachage du contenu), le mode lecteur l'affiche.
2. **La palette y vit** (`LAYER_FILL`, `BOX_INK`) et `/metamodel` l'expose (`layer_styles`, `box_ink`). La constante `LAYER_COLOURS` du SPA reste synchrone (cinq composants la lisent au rendu); une divergence est interdite par un test, pas par une lecture à l'exécution.
3. **Un test de parité sur une fixture JSON partagée** (`frontend/tests/fixtures/diagram-parity.json`): le test backend vérifie que le SVG du serveur est exactement celui de la fixture et que la palette est celle de la fixture; le test Vitest recalcule avec `diagramGeometry.ts` les boîtes, les segments, la taille du canevas, les noms raccourcis et la palette, et les compare au SVG de la fixture. Modifier l'un des deux côtés sans l'autre casse un test.
4. Le raccourcissement des noms, la marge (160) et le canevas minimal (800 x 600) sont les constantes de l'éditeur, exportées de `diagramGeometry.ts` pour que le canvas et le test les partagent.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Navigateur sans tête | Dessin identique au SPA | Chromium dans l'image, lent, non déterministe | Écarté |
| Rendu du SPA côté serveur | Un seul code de dessin | Exige Node dans l'image API | Écarté |
| Dessin serveur seul, éditeur qui l'affiche | Un seul code | L'éditeur doit rester interactif: il garde sa géométrie | Écarté |
| Palette lue par le SPA via `/metamodel` à l'exécution | Source unique stricte | Refonte asynchrone de cinq composants pour un gain nul | Écarté au profit du test |

## Conséquences

- Le raccourcissement compte en points de code côté serveur et en unités UTF-16 côté SPA: un caractère hors BMP dans un nom peut raccourcir différemment. La fixture n'en contient pas; si le cas devient réel, aligner les deux règles par un test.
- Les sous-projets 6 (formes, connecteurs, notes) étendent `DiagramDetail` et ce module; la fixture de parité s'étend avec eux.
- Une personne qui modifie l'éditeur voit le test de parité échouer: c'est voulu, c'est ce qui garde « un seul dessin ».

## Références

- ADR liés: [0031](0031-diagrammes-enregistres.md), [0035](0035-taille-des-boites-de-diagramme.md), [0042](0042-generation-de-documents.md)
- Code concerné: `backend/src/ea/domain/diagram_render.py`, `frontend/src/lib/diagramGeometry.ts`
```

(Vérifier le nom réel du fichier de l'ADR 0031 avec `ls docs/adr | grep 0031` avant de sauver.)

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0042-generation-de-documents.md docs/adr/0043-un-seul-dessin-de-diagramme.md
git commit -m "docs(adr): document generation and the single static diagram drawing

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Vérification du lot 1

- [ ] **Step 1:** `make openapi` puis `make openapi-check` (aucune différence attendue après la Task 10).
- [ ] **Step 2:** `make check` (lint, types, contrat, tests avec plancher de couverture 90 %, tests SPA, pipelines).
- [ ] **Step 3:** `make test-integration` si Docker est présent.
- [ ] **Step 4:** `git status` ne doit montrer aucun fichier généré hors `openapi.json`/`schema.d.ts` déjà commités; `git log --oneline` du lot lisible (Conventional Commits).
- [ ] **Step 5:** Ouvrir la PR du lot 1 (fusionnable seule: HTML + `render.svg`, aucune dépendance lourde).

---

# LOT 2: PDF et outils MCP

### Task 15: Porte d'entrée, dépendance WeasyPrint, Dockerfile, CI

**Files:**
- Modify: `backend/pyproject.toml` (dépendances l.5-30; surcharge mypy après `[tool.mypy]`, l.74-78)
- Modify: `backend/uv.lock` (régénéré)
- Modify: `backend/Dockerfile` (étage final, l.24-33)
- Modify: `.github/workflows/ci.yml` (job `backend`, avant `- run: make install`, l.47)

**Interfaces:**
- Produces: `import weasyprint` disponible en dev et dans l'image; paquets Debian `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`, `fonts-dejavu-core` dans l'image et sur le runner de CI.

- [ ] **Step 1: Porte d'entrée: le lot 1 est livré**

Run: `cd backend && uv run pytest tests/e2e/test_report_api.py tests/unit/test_report_service.py -q` et `grep -n "def render_report" src/ea/api/reports.py`. Si l'un échoue ou manque: **s'arrêter**, le lot 1 n'est pas livré.

- [ ] **Step 2: Déclarer la dépendance**

Run: `cd backend && uv add "weasyprint>=66.0"`. Dans `pyproject.toml`, juste au-dessus de la ligne ajoutée, le commentaire: `# PDF from the self-contained HTML report — needs Pango at run time; see docs/adr/0042.`

Surcharge mypy (WeasyPrint et python-docx ne publient pas tous leurs types): dans `pyproject.toml`, remplacer

```toml
mypy_path = "src"
packages = ["ea"]
```

par

```toml
mypy_path = "src"
packages = ["ea"]

[[tool.mypy.overrides]]
module = ["weasyprint", "weasyprint.*", "docx", "docx.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Dockerfile** (`backend/Dockerfile`, étage final)

Remplacer

```dockerfile
RUN useradd --system --no-create-home ea
```

par

```dockerfile
# Les rapports PDF (docs/adr/0042) : WeasyPrint charge Pango à l'exécution. La
# police est embarquée (DejaVu) pour que le rendu ne dépende d'aucune machine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home ea
```

et la ligne `ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1` par

```dockerfile
# XDG_CACHE_HOME : l'utilisateur `ea` n'a pas de HOME ; fontconfig y écrit son cache.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 XDG_CACHE_HOME=/tmp
```

Les deux `FROM` restent sur la même version de Python (3.12): ne pas y toucher.

- [ ] **Step 4: CI** (`.github/workflows/ci.yml`, job `backend`)

Remplacer

```yaml
      - run: make install
      - run: make lint-check
```

par

```yaml
      # Les tests PDF chargent WeasyPrint, donc Pango (docs/adr/0042).
      - name: Bibliothèques de WeasyPrint
        run: >
          sudo apt-get update && sudo apt-get install -y --no-install-recommends
          libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core
      - run: make install
      - run: make lint-check
```

- [ ] **Step 5: Vérifier l'environnement local**

Run: `cd backend && uv sync --all-extras && uv run python -c "import weasyprint; print(weasyprint.__version__)"`.
Sur un Mac sans Pango: `OSError: cannot load library 'libgobject-2.0-0'`; installer avec `brew install pango` puis relancer. Les tests PDF de la Task 17 se sautent tant que l'import échoue.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/Dockerfile .github/workflows/ci.yml
git commit -m "build(reports): WeasyPrint and the Pango packages in the API image

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Vérification du build de l'image (sur la machine de l'utilisateur)

L'agent qui exécute ce plan ne construit ni ne lance d'image: Docker et Pango ne sont pas garantis dans son environnement, et un build vérifié « par lecture » ne vaut rien. Cette tâche est donc **pour l'utilisateur**; le plan reste ouvert sur elle jusqu'à son retour.

- [ ] **Step 1: Construire l'image** (sur le Mac de l'utilisateur)

```bash
cd backend && docker build -t ea-api:report-check .
```

Attendu: build réussi, `uv sync --locked` accepte le `uv.lock` régénéré à la Task 15.

- [ ] **Step 2: Produire un PDF dans l'image, avec des caractères non latins**

```bash
docker run --rm --entrypoint python ea-api:report-check -c "from weasyprint import HTML; pdf = HTML(string='<p>Facturation é Ελληνικά Кириллица</p>').write_pdf(); print(pdf[:5], len(pdf))"
```

Attendu: `b'%PDF-' <taille supérieure à 1000>`. Une `OSError: cannot load library` signifie un paquet Pango manquant dans l'étage final.

- [ ] **Step 3: Mesurer le coût de l'image**

```bash
docker images ea-api:report-check --format '{{.Size}}'
```

Comparer à l'image précédente (`git stash` interdit: construire d'abord `main` sous un autre tag: `git worktree add ../ea-main main && docker build -t ea-api:before ../ea-main/backend`). Noter les deux tailles dans l'ADR 0042 (section Conséquences) en remplaçant « environ 80 Mo » par la mesure.

- [ ] **Step 4: Redéployer** (hors de ce plan): `make app-up` ne s'exécute qu'après la fusion sur `main` (Portainer clone `main`, ADR 0041).

---

### Task 17: Rendu PDF et récupérateur d'URL fermé (`repositories/report_renderers.py`)

**Files:**
- Create: `backend/src/ea/repositories/report_renderers.py`
- Create: `backend/tests/unit/test_report_renderers.py`

**Interfaces:**
- Consumes: `to_html` (Task 6), `Renderer`, `ReportFormat`, `ReportModel` (Task 5).
- Produces:
  - `refuse_every_url(url: str, *args: object, **kwargs: object) -> NoReturn`
  - `pdf_renderer() -> Renderer` (lève `ImportError`/`OSError` si WeasyPrint ou Pango manquent)
  - `available_renderers() -> dict[ReportFormat, Renderer]` (n'inclut que les moteurs qui se chargent; journalise `action=report_format_unavailable` pour les autres)

- [ ] **Step 1: Écrire les tests qui échouent**

```python
"""The report engines that need native libraries, and the policy that closes them."""

from __future__ import annotations

import logging

import pytest

import ea.repositories.report_renderers as renderers
from ea.domain.diagram_render import render_svg
from ea.domain.reports import (
    Figure,
    Heading,
    MarkdownBlock,
    Paragraph,
    ReportModel,
    Table,
)
from ea.repositories.report_renderers import available_renderers, pdf_renderer, refuse_every_url
from tests.unit.test_diagram_render import a_detail, an_element


def a_pdf_engine():  # type: ignore[no-untyped-def]
    try:
        return pdf_renderer()
    except (ImportError, OSError) as error:
        pytest.skip(f"WeasyPrint is not loadable here ({error}); see docs/adr/0042")


def model(*blocks: object) -> ReportModel:
    return ReportModel(title="Rapport", subtitle="Généré", blocks=tuple(blocks))  # type: ignore[arg-type]


class TestTheClosedFetcher:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",
            "https://example.org/pixel.png",
            "file:///etc/passwd",
            "data:text/plain;base64,AAAA",
            "ftp://host/x",
        ],
    )
    def test_nothing_is_ever_fetched(self, url: str) -> None:
        with pytest.raises(ValueError, match="refused"):
            refuse_every_url(url)


class TestThePdf:
    def test_a_report_becomes_a_pdf(self) -> None:
        engine = a_pdf_engine()
        svg = render_svg(a_detail([(an_element(), 40, 40, 132, 46)]))

        pdf = engine(
            model(
                Heading(2, "Titre"),
                Paragraph("Texte"),
                Table(("Nom", "Type"), (("A", "x"),)),
                Figure(svg, "Vente"),
                MarkdownBlock("# Titre\n\n- a\n- b\n"),
            )
        )

        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000

    def test_an_empty_report_is_still_a_pdf(self) -> None:
        assert a_pdf_engine()(model()).startswith(b"%PDF")

    def test_non_latin_names_do_not_break_the_rendering(self) -> None:
        engine = a_pdf_engine()

        pdf = engine(
            model(
                Heading(2, "Ελληνικά Кириллица 日本語"),
                Table(("Nom",), (("Σύστημα ΕΑ",), ("Система",), ("システム",))),
            )
        )

        assert pdf.startswith(b"%PDF")

    def test_markup_and_remote_references_in_the_content_fetch_nothing(self) -> None:
        engine = a_pdf_engine()

        pdf = engine(
            model(
                Paragraph('<img src="http://127.0.0.1:9/x.png"> <script>1</script> & "q"'),
                MarkdownBlock("![x](http://127.0.0.1:9/y.png)\n\n<img src=file:///etc/passwd>"),
            )
        )

        assert pdf.startswith(b"%PDF")


class TestWhatIsOffered:
    def test_a_loadable_engine_is_offered(self) -> None:
        a_pdf_engine()

        assert "pdf" in available_renderers()

    def test_an_engine_that_cannot_load_is_left_out_and_said_in_the_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken() -> None:
            raise OSError("cannot load library 'libgobject-2.0-0'")

        monkeypatch.setattr(renderers, "pdf_renderer", broken)

        with caplog.at_level(logging.WARNING):
            offered = available_renderers()

        assert "pdf" not in offered
        assert any(getattr(r, "action", "") == "report_format_unavailable" for r in caplog.records)
```

- [ ] **Step 2: Lancer, échec attendu** (`ModuleNotFoundError: ea.repositories.report_renderers`)

Run: `cd backend && uv run pytest tests/unit/test_report_renderers.py -x`

- [ ] **Step 3: Implémenter**

```python
"""The report formats that need native libraries: PDF now, DOCX in the next lot.

Each engine is imported on demand and an engine that cannot load is *left out*,
not fatal: HTML is always served, and `GET /report-templates` lists the formats
this deployment really produces (docs/adr/0042).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import NoReturn

from ea.domain.report_html import to_html
from ea.domain.reports import Renderer, ReportFormat, ReportModel

logger = logging.getLogger(__name__)


def refuse_every_url(url: str, *args: object, **kwargs: object) -> NoReturn:
    """WeasyPrint's URL fetcher, closed: a report is self-contained.

    Neither the network (SSRF toward the Infra's hosts or a metadata endpoint)
    nor the local disk (`file:///etc/passwd` through an `<img>`) is ever read,
    and `data:` is refused too: the HTML this engine receives has none.
    """
    msg = f"fetching {url!r} is refused: a report is self-contained"
    raise ValueError(msg)


def pdf_renderer() -> Renderer:
    """The PDF engine. Raises `ImportError` or `OSError` when WeasyPrint or Pango is missing."""
    from weasyprint import HTML  # noqa: PLC0415 - the import is what loads the native libraries

    def render(model: ReportModel) -> bytes:
        pdf = HTML(string=to_html(model), url_fetcher=refuse_every_url).write_pdf()
        if pdf is None:  # only when a `target` is given, never here
            msg = "WeasyPrint returned no bytes"
            raise RuntimeError(msg)
        return bytes(pdf)

    return render


def available_renderers() -> dict[ReportFormat, Renderer]:
    """Every extra format whose engine loads on this machine."""
    engines: tuple[tuple[ReportFormat, Callable[[], Renderer]], ...] = (("pdf", pdf_renderer),)
    found: dict[ReportFormat, Renderer] = {}
    for name, factory in engines:
        try:
            found[name] = factory()
        except (ImportError, OSError) as error:
            logger.warning(
                "%s reports are unavailable: %s",
                name,
                error,
                extra={"action": "report_format_unavailable", "report_format": name},
            )
    return found
```

Note: `engines` est construit dans la fonction, au moment de l'appel: c'est ce qui permet à `monkeypatch.setattr(renderers, "pdf_renderer", ...)` d'être vu.

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_renderers.py -x -rs` (les tests PDF se sautent avec la raison affichée si Pango manque; le reste passe) puis `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/report_renderers.py backend/tests/unit/test_report_renderers.py
git commit -m "feat(reports): PDF engine with a URL fetcher that refuses everything

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Détection des formats au démarrage et e2e PDF

**Files:**
- Modify: `backend/src/ea/main.py` (`_attach_reports`, créé à la Task 9)
- Modify: `backend/tests/e2e/test_report_api.py` (ajout en fin)

**Interfaces:**
- Consumes: `available_renderers` (Task 17).
- Produces: `create_app(report_renderers=None)` signifie désormais « détecter les moteurs installés »; `{}` ou une table explicite reste le moyen de les imposer en test.

- [ ] **Step 1: Écrire les tests qui échouent** (fin de `test_report_api.py`)

```python
FAKE_PDF = b"%PDF-1.7 fake"


def a_client(service, diagrams, documents, files_store, files_metadata, renderers):  # type: ignore[no-untyped-def]
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,
        documents=documents,
        files=files_store,
        file_metadata=files_metadata,
        report_renderers=renderers,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestThePdfFormat:
    async def test_an_offered_format_is_listed_and_served(
        self, service, diagrams, documents, files_store, files_metadata
    ) -> None:
        async with a_client(
            service, diagrams, documents, files_store, files_metadata, {"pdf": lambda m: FAKE_PDF}
        ) as client:
            diagram_id = await a_diagram(client, "Facturation")

            templates = await client.get("/report-templates")
            response = await client.get("/reports/render", params=render_params(diagram_id, "pdf"))
            deposit = await client.post(
                "/reports",
                json={
                    "template": "fiche-diagramme",
                    "subject": {"kind": "diagram", "id": diagram_id},
                    "format": "pdf",
                },
            )

        assert templates.json()[0]["formats"] == ["html", "pdf"]
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"].endswith(".pdf")
        assert response.content == FAKE_PDF
        assert deposit.json()["key"].endswith(".pdf")
        assert deposit.json()["content_type"] == "application/pdf"

    async def test_the_detected_engine_makes_a_real_pdf(
        self, service, diagrams, documents, files_store, files_metadata
    ) -> None:
        from ea.repositories.report_renderers import available_renderers

        detected = available_renderers()
        if "pdf" not in detected:
            pytest.skip("WeasyPrint is not loadable here; see docs/adr/0042")
        async with a_client(
            service, diagrams, documents, files_store, files_metadata, None
        ) as client:
            diagram_id = await a_diagram(client, "Facturation Ελληνικά")

            response = await client.get("/reports/render", params=render_params(diagram_id, "pdf"))

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/e2e/test_report_api.py -x -k Pdf`
Attendu: le second test échoue (formats `["html"]` seulement: `report_renderers=None` n'a rien détecté).

- [ ] **Step 3: Implémenter**

Dans `main._attach_reports`, après le test de garde et avant la création:

```python
    renderers = getattr(app.state, "report_renderers", None)
    if renderers is None:  # not imposed by a test: use the engines this machine loads
        renderers = available_renderers()
```

avec `from ea.repositories.report_renderers import available_renderers` en tête de `main.py`, et `extra_renderers=renderers` dans l'appel. Mettre à jour la docstring de `create_app` (« `None` detects the installed engines ») et supprimer la mention « HTML seul » ajoutée à la Task 9.

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/e2e tests/unit -x -q`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/main.py backend/tests/e2e/test_report_api.py
git commit -m "feat(reports): offer the formats whose engine loads on this machine

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Outils MCP `list_report_templates` et `generate_report`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports l.37-91; `INSTRUCTIONS` l.109-165; fournisseurs ~l.170; signature `build_mcp_server` l.294-303; nouveaux outils avant `# --- Metamodel ---`, ~l.917)
- Modify: `backend/src/ea/main.py` (`_mount_mcp`, appel `build_mcp_server`, ~l.402; import `report_service_of`)
- Modify: `backend/tests/conftest.py` (nouvelle fixture `report_service`, après `file_service`, ~l.845-853)
- Modify: `backend/tests/unit/test_mcp_server.py` (fixtures l.40-66; liste d'outils l.88-123; nouvelles classes en fin)

**Interfaces:**
- Consumes: `ReportService` (Tasks 7-8), `ReportTemplateRead`, `ReportTemplateId`, `FileRead` (`api/schemas.py`), `ADDS`, `READS`, `speaking_plainly`.
- Produces: `ReportProvider = Callable[[], ReportService]`; `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_reports, *, version, token_verifier, auth)`; outils `list_report_templates() -> list[ReportTemplateRead]`, `generate_report(template, subject_kind, subject_id, format="pdf") -> FileRead`.

- [ ] **Step 1: Écrire les tests qui échouent**

`backend/tests/conftest.py`: import `from ea.services.reports import ReportService` (avec les autres services) et, après la fixture `file_service`:

```python
@pytest.fixture
def report_service(
    diagram_service: DiagramService,
    document_service_without_an_index: DocumentService,
    file_service: FileService,
) -> ReportService:
    """Reports over the in-memory stores, on one frozen clock and a fixed suffix."""
    return ReportService(
        diagram_service,
        document_service_without_an_index,
        file_service,
        clock=lambda: FIXED_NOW,
        new_id=lambda: "ab12cd34",
    )
```

`test_mcp_server.py`: les deux fixtures `server` et `server_without_an_index` prennent `report_service: ReportService` et passent `lambda: report_service` en cinquième argument de `build_mcp_server`; ajouter `from ea.services.reports import ReportService` et `from ea.domain.diagrams import DiagramNode`; dans l'ensemble attendu de `test_it_exposes_the_whole_service_and_nothing_else`, ajouter après `"delete_file",`:

```python
            "list_report_templates",
            "generate_report",
```

Nouvelles classes en fin de fichier:

```python
@pytest.mark.asyncio
class TestReports:
    async def a_diagram(self, service: ArchitectureService, diagram_service: DiagramService) -> str:
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        diagram = await diagram_service.create(name="Vente")
        await diagram_service.replace_layout(diagram.id, [DiagramNode(element.id, 10.0, 10.0)])
        return str(diagram.id)

    async def test_the_templates_name_their_subjects_and_the_served_formats(
        self, server: MCPServer[Any]
    ) -> None:
        templates = (await call(server, "list_report_templates"))["result"]

        assert templates == [
            {
                "id": "fiche-diagramme",
                "version": 1,
                "title": "Fiche de diagramme",
                "subjects": ["diagram"],
                "formats": ["html"],
            }
        ]

    async def test_a_report_is_deposited_and_answered_by_its_record_never_its_bytes(
        self,
        server: MCPServer[Any],
        service: ArchitectureService,
        diagram_service: DiagramService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram_id = await self.a_diagram(service, diagram_service)

        record = await call(
            server,
            "generate_report",
            template="fiche-diagramme",
            subject_kind="diagram",
            subject_id=diagram_id,
            format="html",
        )

        assert record["key"] == "reports/2026/2026-09-07-vente-fiche-diagramme-ab12cd34.html"
        assert record["metadata"]["tags"] == ["rapport", "fiche-diagramme"]
        assert "content" not in record
        assert record["key"] in files_store.objects

    async def test_a_reader_cannot_generate_one_and_nothing_is_written(
        self,
        server: MCPServer[Any],
        service: ArchitectureService,
        diagram_service: DiagramService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram_id = await self.a_diagram(service, diagram_service)

        with acting_as(a_reader()), pytest.raises(ToolError):
            await server.call_tool(
                "generate_report",
                {
                    "template": "fiche-diagramme",
                    "subject_kind": "diagram",
                    "subject_id": diagram_id,
                    "format": "html",
                },
            )

        assert files_store.objects == {}

    async def test_an_unavailable_format_says_so_in_words_the_model_can_act_on(
        self,
        server: MCPServer[Any],
        service: ArchitectureService,
        diagram_service: DiagramService,
    ) -> None:
        diagram_id = await self.a_diagram(service, diagram_service)

        with pytest.raises(ToolError, match="not available"):
            await server.call_tool(
                "generate_report",
                {
                    "template": "fiche-diagramme",
                    "subject_kind": "diagram",
                    "subject_id": diagram_id,
                    "format": "pdf",
                },
            )

    async def test_the_template_bound_is_the_http_adapters(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await server.call_tool(
                "generate_report",
                {
                    "template": "Not A Slug",
                    "subject_kind": "diagram",
                    "subject_id": str(uuid4()),
                    "format": "html",
                },
            )

    async def test_the_write_is_flagged_as_an_addition_never_a_destruction(
        self, server: MCPServer[Any]
    ) -> None:
        tools = {tool.name: tool.annotations for tool in await server.list_tools()}

        assert tools["list_report_templates"].read_only_hint is True  # type: ignore[union-attr]
        generate = tools["generate_report"]
        assert generate is not None
        assert (generate.read_only_hint, generate.destructive_hint) == (False, False)
```

(imports de test manquants: `from uuid import uuid4`, `ToolError`, `ElementType`, `ArchitectureService`, `DiagramService` — vérifier ceux déjà présents en tête du fichier et n'ajouter que les absents.)

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Attendu: `TypeError: build_mcp_server() takes ... positional arguments` ou liste d'outils différente.

- [ ] **Step 3: Implémenter** (`mcp/server.py`)

Imports: dans le bloc `from ea.api.schemas import (...)`, ajouter `ReportTemplateId`, `ReportTemplateRead` (ordre alphabétique); `from ea.domain.reports import ReportFormat, ReportRequest, ReportSubject, SubjectKind`; `from ea.services.reports import ReportService`.

Fournisseur et arguments, après `FileProvider = ...`:

```python
ReportProvider = Callable[[], ReportService]
```

après `FileContent = ...`:

```python
ReportTemplateArgument = Annotated[
    ReportTemplateId, Field(description="A template id, as `list_report_templates` reports it.")
]
SubjectIdArgument = Annotated[
    UUID, Field(description="The id of the diagram (or package) the report is about.")
]
```

Texte à ajouter à la fin de `INSTRUCTIONS` (avant le `\` final qui ferme la chaîne), après le paragraphe sur `set_file_details`:

```
Reports are dated documents built from the catalogue. `list_report_templates`
names the templates, the kind of subject each takes and the formats this server
can produce (html is always there; pdf and docx only where the server carries
the engine). `generate_report` builds one for a diagram, stores it in the bucket
under `reports/` and answers with its path and record, never its bytes: read an
html report with `read_file`, or give the path to the person. A report is a
snapshot, refused past 500 elements, 20 diagrams, 2 MiB of attached documents
or 30 seconds — narrow the scope when it is.
```

Signature: `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_reports: ReportProvider, *, version..., ...)` et docstring (« `get_reports` is required for the same reason »).

Outils, avant `# --- Metamodel ---`:

```python
    # --- Reports (docs/adr/0042) ---------------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_report_templates() -> list[ReportTemplateRead]:
        """The report templates: id, version, title, the kinds of subject, the formats served.

        Call this before `generate_report`: a template takes only the kinds of
        subject it lists, and `formats` is what this server can really produce
        (html is always there).
        """
        reports = get_reports()
        return [
            ReportTemplateRead.of(template, reports.formats)
            for template in await reports.list_templates()
        ]

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def generate_report(
        template: ReportTemplateArgument,
        subject_kind: SubjectKind,
        subject_id: SubjectIdArgument,
        format: ReportFormat = "pdf",  # noqa: A002 - the name an agent sees
    ) -> FileRead:
        """Build a report on a diagram and store it in the bucket under `reports/`.

        Answers with the file's path (`key`) and its record, never its bytes: read
        an html report with `read_file`, or give the path to the person, who
        downloads it from the web interface. Each call stores one more file — a
        regeneration never overwrites. The report is refused, with a message
        saying which ceiling, past 500 elements, 20 diagrams, 2 MiB of attached
        documents or 30 seconds; narrow the scope and call again. A format the
        server cannot produce (`pdf`, `docx`) is refused too: use `html`.
        """
        return FileRead.of(
            await get_reports().deposit(
                ReportRequest(template, ReportSubject(subject_kind, subject_id), format)
            )
        )
```

`main._mount_mcp`: ajouter `lambda: report_service_of(app),` après `lambda: file_service_of(app),`; importer `report_service_of` depuis `ea.api.dependencies` (liste l.~20).

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/e2e/test_mcp_endpoint.py tests/unit/test_mcp_logging.py -x && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/src/ea/main.py backend/tests/conftest.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): list_report_templates and generate_report

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 20: SPA: composable `useReport`, boîte « Générer un rapport… », bouton du diagramme

**Files:**
- Create: `frontend/src/features/reports/useReport.ts`
- Create: `frontend/src/features/reports/ReportDialog.vue`
- Create: `frontend/tests/useReport.spec.ts`
- Create: `frontend/tests/ReportDialog.spec.ts`
- Modify: `frontend/src/features/diagrams/DiagramsSection.vue` (import l.10-21; en-tête de l'éditeur, balise `<h2 v-if="editor.diagram.value">`)
- Modify: `frontend/src/api/schema.d.ts` (déjà généré aux Tasks 9-10; vérifier la présence de `ReportTemplateRead`)

**Interfaces:**
- Consumes: `api`, `unwrap` (`lib/api`), `useLatestRequest` (`lib/latest`), `saveAs` (`features/files/useFiles`), `useMe` (`lib/me`), types générés `components['schemas']['ReportTemplateRead']` et `['FileRead']`.
- Produces:
  - `filenameOf(header: string | null, fallback: string): string`
  - `useReport() -> { templates, deposited, load, templatesFor(kind), download(template, kind, id, format), deposit(template, kind, id, format), status, error, catalogueError }`
  - `<ReportDialog subject-kind subject-id [download-only] />`

- [ ] **Step 1: Écrire les tests qui échouent**

`frontend/tests/useReport.spec.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { filenameOf, useReport } from '../src/features/reports/useReport'
import { stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const TEMPLATES = [
  { id: 'fiche-diagramme', version: 1, title: 'Fiche de diagramme', subjects: ['diagram'], formats: ['html', 'pdf'] },
  { id: 'inventaire-paquet', version: 1, title: "Inventaire d'un paquet", subjects: ['package'], formats: ['html'] },
]

describe('filenameOf', () => {
  it('reads the RFC 5987 name of an attachment', () => {
    expect(filenameOf("attachment; filename*=UTF-8''2026-10-10-vente-fiche.pdf", 'x')).toBe(
      '2026-10-10-vente-fiche.pdf',
    )
  })

  it('falls back when there is no header or it is malformed', () => {
    expect(filenameOf(null, 'repli.pdf')).toBe('repli.pdf')
    expect(filenameOf("attachment; filename*=UTF-8''%E0%A4%A", 'repli.pdf')).toBe('repli.pdf')
  })
})

describe('useReport', () => {
  it('keeps only the templates that take the subject', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])
    const report = useReport()

    await report.load()

    expect(report.templatesFor('diagram').map((t) => t.id)).toEqual(['fiche-diagramme'])
    expect(report.templatesFor('package').map((t) => t.id)).toEqual(['inventaire-paquet'])
  })

  it('downloads through GET /reports/render with the subject in the query', async () => {
    const calls = stubApi([{ path: '/reports/render', body: { ok: true } }])
    const createObjectURL = vi.fn(() => 'blob:report')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const report = useReport()

    await report.download('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    const [call] = calls
    expect(call?.method).toBe('GET')
    expect(call?.url.searchParams.get('template')).toBe('fiche-diagramme')
    expect(call?.url.searchParams.get('subject_kind')).toBe('diagram')
    expect(call?.url.searchParams.get('subject_id')).toBe('dddddddd-0000-4000-8000-000000000000')
    expect(call?.url.searchParams.get('format')).toBe('html')
    expect(createObjectURL).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
  })

  it('deposits through POST /reports and keeps the answer', async () => {
    const calls = stubApi([{ method: 'POST', path: '/reports', status: 201, body: { key: 'reports/2026/x.html' } }])
    const report = useReport()

    await report.deposit('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    expect(calls[0]?.body).toEqual({
      template: 'fiche-diagramme',
      subject: { kind: 'diagram', id: 'dddddddd-0000-4000-8000-000000000000' },
      format: 'html',
    })
    expect(report.deposited.value).toEqual({ key: 'reports/2026/x.html' })
  })

  it('puts a refusal in its state instead of throwing', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/reports',
        status: 422,
        body: { error: 'report_too_large', detail: 'a report covers at most 500 elements' },
      },
    ])
    const report = useReport()

    await report.deposit('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    expect(report.error.value).toContain('500 elements')
    expect(report.deposited.value).toBeNull()
  })
})
```

`frontend/tests/ReportDialog.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import ReportDialog from '../src/features/reports/ReportDialog.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const ID = 'dddddddd-0000-4000-8000-000000000000'
const TEMPLATES = [
  { id: 'fiche-diagramme', version: 1, title: 'Fiche de diagramme', subjects: ['diagram'], formats: ['html', 'pdf'] },
  { id: 'inventaire-paquet', version: 1, title: "Inventaire d'un paquet", subjects: ['package'], formats: ['html'] },
]

async function open(props: Record<string, unknown> = {}) {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/diagrammes')
  const view = render(ReportDialog, {
    props: { subjectKind: 'diagram', subjectId: ID, ...props },
    global: { plugins: [router] },
  })
  await fireEvent.click(screen.getByRole('button', { name: /Générer un rapport/ }))
  return { router, view }
}

describe('ReportDialog', () => {
  it('offers only the templates that take this kind of subject', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open()

    expect(await screen.findByRole('option', { name: 'Fiche de diagramme' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: "Inventaire d'un paquet" })).toBeNull()
  })

  it('downloads for everybody, and an editor may also deposit', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open()

    expect(await screen.findByRole('button', { name: 'Télécharger' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Déposer dans les fichiers' })).toBeInTheDocument()
  })

  it('hides the deposit from a reader and never calls POST /reports', async () => {
    const calls = stubApi([{ path: '/report-templates', body: TEMPLATES }])
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'lecteur', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    await open()

    await screen.findByRole('button', { name: 'Télécharger' })
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('hides the deposit in download-only mode, even for an editor', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open({ downloadOnly: true })

    await screen.findByRole('button', { name: 'Télécharger' })
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
  })

  it('deposits, then lands on the reports folder of the file browser', async () => {
    const calls = stubApi([
      { path: '/report-templates', body: TEMPLATES },
      { method: 'POST', path: '/reports', status: 201, body: { key: 'reports/2026/x.html' } },
    ])
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Déposer dans les fichiers' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/fichiers'))
    expect(router.currentRoute.value.query.prefix).toBe('reports/')
    expect(calls.some((call) => call.method === 'POST' && call.url.pathname === '/reports')).toBe(true)
  })

  it('says why a report was refused', async () => {
    stubApi([
      { path: '/report-templates', body: TEMPLATES },
      {
        method: 'POST',
        path: '/reports',
        status: 422,
        body: { error: 'report_too_large', detail: 'a report covers at most 500 elements' },
      },
    ])
    await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Déposer dans les fichiers' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('500 elements')
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** (module introuvable)

Run: `cd frontend && npm test -- --run tests/useReport.spec.ts tests/ReportDialog.spec.ts`

- [ ] **Step 3: Implémenter**

`frontend/src/features/reports/useReport.ts`:

```ts
// Generating a report: which templates fit a subject, the download, the deposit.
//
// A download is `GET /reports/render` (any reader, nothing stored); a deposit is
// `POST /reports` (editors, the API decides). Both go through `useLatestRequest`
// so a second click supersedes the first, and a refusal lands in `error` in
// words rather than being thrown at the dialog.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { saveAs } from '../files/useFiles'

export type ReportTemplateRead = components['schemas']['ReportTemplateRead']
export type ReportFormat = ReportTemplateRead['formats'][number]
export type SubjectKind = ReportTemplateRead['subjects'][number]
type FileRead = components['schemas']['FileRead']

/** The name an `attachment; filename*=UTF-8''…` header carries, or `fallback`. */
export function filenameOf(header: string | null, fallback: string): string {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (!encoded) {
    return fallback
  }
  try {
    return decodeURIComponent(encoded)
  } catch {
    return fallback
  }
}

export function useReport() {
  const templates = ref<ReportTemplateRead[]>([])
  const deposited = ref<FileRead | null>(null)
  const catalogue = useLatestRequest()
  const work = useLatestRequest()

  async function load(): Promise<void> {
    await catalogue.run(
      async (signal) => unwrap(await api.GET('/report-templates', { signal })),
      (answer) => {
        templates.value = answer
      },
    )
  }

  function templatesFor(kind: SubjectKind): ReportTemplateRead[] {
    return templates.value.filter((template) => template.subjects.includes(kind))
  }

  async function download(
    template: string,
    kind: SubjectKind,
    id: string,
    format: ReportFormat,
  ): Promise<void> {
    await work.run(
      async (signal) => {
        const result = await api.GET('/reports/render', {
          params: { query: { template, subject_kind: kind, subject_id: id, format } },
          parseAs: 'blob',
          signal,
        })
        const blob = unwrap(result)
        const name = filenameOf(
          result.response.headers.get('content-disposition'),
          `${template}.${format}`,
        )
        return { blob, name }
      },
      ({ blob, name }) => {
        saveAs(blob, name)
      },
    )
  }

  async function deposit(
    template: string,
    kind: SubjectKind,
    id: string,
    format: ReportFormat,
  ): Promise<void> {
    deposited.value = null
    await work.run(
      async (signal) =>
        unwrap(
          await api.POST('/reports', {
            body: { template, subject: { kind, id }, format },
            signal,
          }),
        ),
      (file) => {
        deposited.value = file
      },
    )
  }

  return {
    templates,
    deposited,
    load,
    templatesFor,
    download,
    deposit,
    status: work.status,
    error: work.error,
    catalogueError: catalogue.error,
  }
}
```

`frontend/src/features/reports/ReportDialog.vue`:

```vue
<script setup lang="ts">
// « Générer un rapport… » : choose a template and a format for one subject, then
// download it (everybody) or deposit it in the files (editors).
//
// The API refuses a reader's deposit anyway (docs/adr/0032); hiding the button
// only spares them a control that could not work. `downloadOnly` is for the
// reader mode, which never offers a write.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useMe } from '../../lib/me'
import { useReport, type ReportFormat, type SubjectKind } from './useReport'

const props = defineProps<{
  subjectKind: SubjectKind
  subjectId: string
  downloadOnly?: boolean
}>()

const router = useRouter()
const { canWrite } = useMe()
const report = useReport()

const open = ref(false)
const template = ref('')
const format = ref<ReportFormat>('html')

const options = computed(() => report.templatesFor(props.subjectKind))
const formats = computed(
  () => options.value.find((candidate) => candidate.id === template.value)?.formats ?? [],
)
const busy = computed(() => report.status.value === 'loading')
const mayDeposit = computed(() => canWrite.value && !props.downloadOnly)

watch(
  options,
  (list) => {
    if (!list.some((candidate) => candidate.id === template.value)) {
      template.value = list[0]?.id ?? ''
    }
  },
  { immediate: true },
)
watch(
  formats,
  (list) => {
    if (!list.includes(format.value)) {
      format.value = list.includes('pdf') ? 'pdf' : (list[0] ?? 'html')
    }
  },
  { immediate: true },
)

async function show(): Promise<void> {
  open.value = true
  await report.load()
}

async function download(): Promise<void> {
  await report.download(template.value, props.subjectKind, props.subjectId, format.value)
}

async function deposit(): Promise<void> {
  await report.deposit(template.value, props.subjectKind, props.subjectId, format.value)
  if (report.deposited.value) {
    await router.push({ path: '/fichiers', query: { prefix: 'reports/' } })
  }
}
</script>

<template>
  <div class="report">
    <button type="button" class="secondary" @click="show">Générer un rapport…</button>

    <form v-if="open" class="report__form" aria-label="Générer un rapport" @submit.prevent>
      <p v-if="report.catalogueError.value" class="banner banner--error" role="alert">
        {{ report.catalogueError.value }}
      </p>
      <p v-else-if="options.length === 0" class="hint">Aucun modèle de rapport pour ce sujet.</p>
      <template v-else>
        <div class="field">
          <label for="report-template">Modèle</label>
          <select id="report-template" v-model="template">
            <option v-for="candidate in options" :key="candidate.id" :value="candidate.id">
              {{ candidate.title }}
            </option>
          </select>
        </div>
        <div class="field">
          <label for="report-format">Format</label>
          <select id="report-format" v-model="format">
            <option v-for="candidate in formats" :key="candidate" :value="candidate">
              {{ candidate.toUpperCase() }}
            </option>
          </select>
        </div>
        <button type="button" :disabled="busy || !template" @click="download">Télécharger</button>
        <button v-if="mayDeposit" type="button" :disabled="busy || !template" @click="deposit">
          Déposer dans les fichiers
        </button>
      </template>
      <p v-if="report.error.value" class="banner banner--error" role="alert">
        {{ report.error.value }}
      </p>
      <button type="button" class="secondary" @click="open = false">Fermer</button>
    </form>
  </div>
</template>
```

`DiagramsSection.vue`: ajouter `import ReportDialog from '../reports/ReportDialog.vue'` (après l'import de `LinkPopover`) et, dans `<header class="editor__header">`, après la ligne `<h2 v-if="editor.diagram.value">{{ editor.diagram.value.name }}</h2>`:

```vue
      <ReportDialog v-if="editor.diagram.value" subject-kind="diagram" :subject-id="diagramId" />
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/useReport.spec.ts tests/ReportDialog.spec.ts tests/DiagramsSection.spec.ts && npm run lint && npm run typecheck`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/reports frontend/tests/useReport.spec.ts frontend/tests/ReportDialog.spec.ts frontend/src/features/diagrams/DiagramsSection.vue
git commit -m "feat(spa): generate a report from a diagram, download or deposit

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Les boutons « fiche d'un paquet » et « liste de requêtes enregistrées » de la spec s'ajoutent avec les écrans du sous-projet 1: `<ReportDialog subject-kind="package" :subject-id="…" />` suffit, le composant est écrit pour cela.

---

### Task 21: Vérification du lot 2

- [ ] **Step 1:** `make openapi` puis `make openapi-check` (aucune différence attendue).
- [ ] **Step 2:** `make check` (sur une machine avec Pango: les tests PDF s'exécutent; sinon ils se sautent et la couverture peut tomber sous 90 %: dans ce cas lancer `make check` après `brew install pango`).
- [ ] **Step 3:** Confirmer que la Task 16 (build d'image sur la machine de l'utilisateur) a été faite avant de fusionner: sans elle, `make app-up` après fusion pourrait échouer au build.
- [ ] **Step 4:** Mettre à jour la mesure de taille dans l'ADR 0042 (Task 16, step 3), commit `docs(adr): measured image size`.
- [ ] **Step 5:** PR du lot 2.

---

# LOT 3: mode lecteur (`/view/*`)

### Task 22: `GET /documents/{id}/html`

**Files:**
- Modify: `backend/src/ea/api/documents.py` (imports l.9-20; ajout après `read_document`, ~l.98)
- Create: `backend/tests/e2e/test_document_html_api.py`
- Modify: `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)

**Interfaces:**
- Consumes: `DocumentService.get(document_id) -> Document`, `render_markdown` (Task 4).
- Produces: route `GET /documents/{document_id}/html` → `text/html` (fragment sûr), `404` si inconnu.

- [ ] **Step 0: Porte d'entrée:** `grep -n "def render_report" backend/src/ea/api/reports.py && grep -n "render_markdown" backend/src/ea/domain/markdown_html.py` (lot 1 livré). Sinon s'arrêter.

- [ ] **Step 1: Écrire le test qui échoue**

```python
"""A document, rendered to safe HTML by the server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryDocuments

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, documents: InMemoryDocuments
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        documents=documents,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def attached(client: httpx.AsyncClient, markdown: bytes) -> str:
    element = await client.post(
        "/elements", json={"element_type": "application_component", "name": "Facturation"}
    )
    document = await client.post(
        f"/elements/{element.json()['id']}/documents",
        files={"file": ("runbook.md", markdown, "text/markdown")},
    )
    assert document.status_code == 201, document.text
    return str(document.json()["id"])


async def test_markdown_comes_back_as_html(client: httpx.AsyncClient) -> None:
    document_id = await attached(client, b"# Runbook\n\nRedemarrer *le service*.\n")

    response = await client.get(f"/documents/{document_id}/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Runbook</h1>" in response.text
    assert "<em>le service</em>" in response.text


async def test_nothing_in_a_document_can_run(client: httpx.AsyncClient) -> None:
    document_id = await attached(
        client,
        b"<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![p](http://evil.example/p.png)\n",
    )

    response = await client.get(f"/documents/{document_id}/html")

    assert "<script" not in response.text
    assert 'href="javascript' not in response.text.lower()
    assert "<img" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_an_unknown_document_is_a_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"/documents/{uuid4()}/html")).status_code == 404
```

- [ ] **Step 2: Lancer, échec attendu** (404 de route)

Run: `cd backend && uv run pytest tests/e2e/test_document_html_api.py -x`

- [ ] **Step 3: Implémenter**

Dans `api/documents.py`: `from fastapi.responses import HTMLResponse` et `from ea.domain.markdown_html import render_markdown`; après `read_document`:

```python
@router.get(
    "/documents/{document_id}/html", response_class=HTMLResponse, responses=NOT_FOUND
)
async def read_document_html(document_id: UUID, documents: Documents) -> HTMLResponse:
    """The document as a safe HTML fragment, for a reader that cannot render markdown.

    Raw HTML in the document is shown as text, images are not loaded and
    `javascript:` / `data:` links are not links (docs/adr/0042): the SPA inserts
    the result with `v-html` for exactly that reason.
    """
    document = await documents.get(document_id)
    return HTMLResponse(
        render_markdown(document.content),
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
```

- [ ] **Step 4: Relancer, PASS; régénérer le contrat**

Run: `cd backend && uv run pytest tests/e2e/test_document_html_api.py tests/e2e/test_auth_api.py -x && cd .. && make openapi && make openapi-check`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/api/documents.py backend/tests/e2e/test_document_html_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(api): serve a document as safe HTML

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 23: Section « Consultation », routes imbriquées, mise en page allégée

**Ordre d'exécution:** cette tâche et la Task 24 se terminent ensemble. Écrire les tests de la Task 23 (Step 1), exécuter les Steps 1 à 3 de la Task 24 (les pages), puis revenir aux Steps 3 et 4 de celle-ci: les routes paresseuses n'ont de cible qu'avec leurs pages. Un seul commit les réunit (Task 24, Step 5).

**Files:**
- Modify: `frontend/src/router/sections.ts` (type `Section` l.14-26; `GROUPS` l.29-33; fin de `SECTIONS` l.88-96)
- Modify: `frontend/src/router/index.ts` (routes l.21-28)
- Modify: `frontend/src/App.vue` (script et gabarit)
- Create: `frontend/src/features/view/ViewShell.vue`
- Modify: `frontend/tests/router.spec.ts`, `frontend/tests/App.spec.ts` (ajouts)

**Interfaces:**
- Produces: `Section.children?: RouteRecordRaw[]`, `Section.reader?: boolean`; routes `view` (`/view`), `view-home`, `view-element` (`/view/element/:id`), `view-diagram` (`/view/diagram/:id`); `route.meta.reader`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `frontend/tests/router.spec.ts` (dans le `describe` existant):

```ts
  it('routes the reader mode and its three screens under /view', async () => {
    const app = router()

    await app.push('/view')
    expect(app.currentRoute.value.name).toBe('view-home')
    await app.push('/view/element/11111111-1111-4111-8111-111111111111')
    expect(app.currentRoute.value.name).toBe('view-element')
    await app.push('/view/diagram/dddddddd-0000-4000-8000-000000000000')
    expect(app.currentRoute.value.name).toBe('view-diagram')
    expect(app.currentRoute.value.meta.reader).toBe(true)
  })

  it('does not make the reader mode the landing page', async () => {
    const app = router()

    await app.push('/')

    expect(app.currentRoute.value.path).toBe(HOME)
    expect(HOME).not.toBe('/view')
  })
```

Ajouter à `frontend/tests/AppNav.spec.ts` (il dispose de `renderNav(path, entries?)`):

```ts
it('lists the reading section under its own heading', async () => {
  await renderNav('/view')

  expect(screen.getByText('Lecture')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Consultation/ })).toHaveAttribute('href', '/view')
})
```

Ajouter à `frontend/tests/App.spec.ts`, dans le `describe` existant (son `renderApp(path)` répond `[]` à tout ce qui n'est pas `/health`, ce que `ViewHome` accepte pour `GET /diagrams`):

```ts
  it('drops the editing menu in the reader mode', async () => {
    await renderApp('/view')

    expect(await screen.findByRole('navigation', { name: 'Consultation' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: /sections/i })).toBeNull()
  })
```

- [ ] **Step 2: Lancer, échec attendu**

Run: `cd frontend && npm test -- --run tests/router.spec.ts tests/AppNav.spec.ts tests/App.spec.ts`

- [ ] **Step 3: Implémenter**

`sections.ts`: dans `Section`, ajouter

```ts
  /** Nested screens, mounted inside this section's view (which holds a `<RouterView>`). */
  children?: RouteRecordRaw[]
  /** A reading screen: the shell drops the editing menu around it (docs/adr/0044). */
  reader?: boolean
```

`GROUPS`: ajouter `{ id: 'reading', label: 'Lecture' },` en dernier. `SECTIONS`: ajouter en dernier:

```ts
  {
    path: '/view',
    name: 'view',
    label: 'Consultation',
    summary: 'Lire le modèle sans pouvoir le modifier : éléments, relations, diagrammes.',
    group: 'reading',
    reader: true,
    view: () => import('../features/view/ViewShell.vue'),
    children: [
      { path: '', name: 'view-home', component: () => import('../features/view/ViewHome.vue') },
      {
        path: 'element/:id',
        name: 'view-element',
        component: () => import('../features/view/ViewElement.vue'),
      },
      {
        path: 'diagram/:id',
        name: 'view-diagram',
        component: () => import('../features/view/ViewDiagram.vue'),
      },
    ],
  },
```

`router/index.ts`: dans le `.map`, remplacer `meta: { label: section.label },` par

```ts
    children: section.children,
    meta: { label: section.label, reader: section.reader === true },
```

`App.vue`: `import { useRoute } from 'vue-router'`, `const route = useRoute()` dans le script, et `<AppNav />` devient `<AppNav v-if="!route.meta.reader" />`.

`features/view/ViewShell.vue`:

```vue
<script setup lang="ts">
// The reader mode: the model read, never written (docs/adr/0044). Same origin,
// same login, same endpoints as the rest of the SPA, so there is no second
// surface to protect — only a lighter layout around the same reads.
import { HOME } from '../../router/sections'
</script>

<template>
  <section class="view">
    <nav class="view__bar" aria-label="Consultation">
      <RouterLink :to="{ name: 'view-home' }">Consultation</RouterLink>
      <RouterLink :to="HOME">Ouvrir l'application complète</RouterLink>
    </nav>
    <RouterView />
  </section>
</template>

<style scoped>
.view__bar {
  display: flex;
  flex-wrap: wrap;
  gap: 1.25rem;
  margin-bottom: 1.25rem;
}
</style>
```

- [ ] **Step 4: Relancer, PASS** (une fois les pages de la Task 24 écrites)

Run: `cd frontend && npm test -- --run tests/router.spec.ts tests/AppNav.spec.ts tests/App.spec.ts && npm run lint && npm run typecheck`

- [ ] **Step 5:** pas de commit ici: voir la Task 24, Step 5.

---

### Task 24: Pages de lecture: accueil, élément, diagramme; « aucune écriture »

**Files:**
- Create: `frontend/src/features/view/ViewHome.vue`
- Create: `frontend/src/features/view/ViewElement.vue`
- Create: `frontend/src/features/view/ViewDiagram.vue`
- Create: `frontend/tests/view.spec.ts`

**Interfaces:**
- Consumes: `api`, `unwrap`, `useLatestRequest`, `useDiagrams` (`features/diagrams/useDiagrams`), `useElementDetail`, `useMetamodel`, `RELATIONSHIP_LABELS`/`verbOf` (`features/relationships/labels`), `ReportDialog` (Task 20, `downloadOnly`), routes de la Task 23.
- Produces: trois écrans qui n'émettent que des `GET`.

- [ ] **Step 1: Écrire les tests qui échouent** (`frontend/tests/view.spec.ts`)

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ViewDiagram from '../src/features/view/ViewDiagram.vue'
import ViewElement from '../src/features/view/ViewElement.vue'
import ViewHome from '../src/features/view/ViewHome.vue'
import { createAppRouter } from '../src/router'
import {
  aDocumentSummary,
  aGraph,
  anElement,
  aPage,
  aRelationship,
  METAMODEL,
  stubApi,
  type Route,
} from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})
const LINK = aRelationship({ source_id: A.id, target_id: B.id, relationship_type: 'serving' })
const DIAGRAM_ID = 'dddddddd-0000-4000-8000-000000000000'
const SUMMARY = {
  id: DIAGRAM_ID,
  name: 'Vue applicative',
  description: '',
  node_count: 2,
  created_at: '2026-09-13T12:00:00Z',
  updated_at: '2026-09-13T12:00:00Z',
}

const ROUTES: Route[] = [
  { path: '/metamodel', body: METAMODEL },
  { path: '/diagrams', body: [SUMMARY] },
  { path: '/elements', body: aPage([A, B]) },
  { path: `/elements/${A.id}`, body: A },
  { path: `/elements/${A.id}/relationships`, body: aGraph([A, B], [LINK]) },
  { path: `/elements/${A.id}/documents`, body: [aDocumentSummary({ element_id: A.id })] },
  { path: '/documents/dddddddd-1111-4111-8111-111111111111/html', body: '<h1>Runbook</h1>' },
  {
    path: `/diagrams/${DIAGRAM_ID}`,
    body: {
      ...SUMMARY,
      nodes: [
        { element_id: A.id, x: 10, y: 20, width: 132, height: 46 },
        { element_id: B.id, x: 300, y: 20, width: 132, height: 46 },
      ],
      elements: [A, B],
      relationships: [LINK],
    },
  },
  { path: `/diagrams/${DIAGRAM_ID}/render.svg`, body: '<svg></svg>' },
]

async function at(path: string, component: object) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const view = render(component, { global: { plugins: [router] } })
  return { router, view }
}

const WRITE_CONTROLS = /Supprimer|Modifier|Créer|Enregistrer|Déposer|Importer|Retirer/

describe('the reader mode never writes', () => {
  it('home: lists diagrams and finds elements by name, with GET only', async () => {
    const calls = stubApi(ROUTES)

    await at('/view?q=Fact', ViewHome)

    expect(await screen.findByRole('link', { name: 'Vue applicative' })).toHaveAttribute(
      'href',
      `/view/diagram/${DIAGRAM_ID}`,
    )
    expect(await screen.findByRole('link', { name: 'Facturation' })).toHaveAttribute(
      'href',
      `/view/element/${A.id}`,
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('element: facts, clickable links both ways, documents rendered by the server', async () => {
    const calls = stubApi(ROUTES)

    await at(`/view/element/${A.id}`, ViewElement)

    expect(await screen.findByRole('heading', { name: 'Facturation' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Commandes' })).toHaveAttribute(
      'href',
      `/view/element/${B.id}`,
    )
    await fireEvent.click(await screen.findByRole('button', { name: 'runbook.md' }))
    expect(await screen.findByRole('heading', { name: 'Runbook' })).toBeInTheDocument()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('element: the relation filter is in the URL and asks the API for that type only', async () => {
    const calls = stubApi(ROUTES)
    const { router } = await at(`/view/element/${A.id}`, ViewElement)
    await screen.findByRole('link', { name: 'Commandes' })
    await screen.findByRole('option', { name: 'sert' })

    await fireEvent.update(screen.getByLabelText('Type de relation'), 'serving')

    await waitFor(() => expect(router.currentRoute.value.query.relation).toBe('serving'))
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.pathname.endsWith('/relationships') &&
            call.url.searchParams.get('relationship_type') === 'serving',
        ),
      ).toBe(true),
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('diagram: the server drawing as an image, then the elements under it, GET only', async () => {
    const createObjectURL = vi.fn(() => 'blob:drawing')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const calls = stubApi(ROUTES)

    await at(`/view/diagram/${DIAGRAM_ID}`, ViewDiagram)

    const image = await screen.findByRole('img', { name: /Vue applicative/ })
    expect(image).toHaveAttribute('src', 'blob:drawing')
    expect(await screen.findByRole('link', { name: 'Commandes' })).toHaveAttribute(
      'href',
      `/view/element/${B.id}`,
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    // Download is offered, a deposit never is, whoever is logged in.
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('diagram: an unknown diagram is said, not drawn', async () => {
    stubApi([
      {
        path: `/diagrams/${DIAGRAM_ID}`,
        status: 404,
        body: { error: 'not_found', detail: 'no diagram' },
      },
    ])

    await at(`/view/diagram/${DIAGRAM_ID}`, ViewDiagram)

    expect(await screen.findByRole('alert')).toHaveTextContent('no diagram')
    expect(screen.queryByRole('img')).toBeNull()
  })
})
```

- [ ] **Step 2: Lancer, échec attendu** (modules introuvables)

Run: `cd frontend && npm test -- --run tests/view.spec.ts`

- [ ] **Step 3: Implémenter**

`ViewHome.vue`:

```vue
<script setup lang="ts">
// The reader's entry point: the saved diagrams, and a search over element names.
// The term is in the URL (`?q=`), so a search can be linked to and walked back.
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { useDiagrams } from '../diagrams/useDiagrams'

type ElementRead = components['schemas']['ElementRead']

const route = useRoute()
const router = useRouter()
const diagrams = useDiagrams()
const search = useLatestRequest()

const term = ref(typeof route.query.q === 'string' ? route.query.q : '')
const found = ref<ElementRead[]>([])

async function run(q: string): Promise<void> {
  if (!q.trim()) {
    search.cancel()
    found.value = []
    return
  }
  await search.run(
    async (signal) =>
      unwrap(await api.GET('/elements', { params: { query: { search: q.trim(), limit: 20 } }, signal })),
    (page) => {
      found.value = page.items
    },
  )
}

function submit(): void {
  void router.replace({ query: { q: term.value.trim() || undefined } })
}

watch(
  () => route.query.q,
  (q) => {
    void run(typeof q === 'string' ? q : '')
  },
  { immediate: true },
)
onMounted(async () => {
  await diagrams.load()
})
</script>

<template>
  <div class="view-home">
    <h2>Consultation</h2>

    <form role="search" @submit.prevent="submit">
      <label for="view-search">Chercher un élément</label>
      <input id="view-search" v-model="term" type="search" maxlength="200" autocomplete="off" />
      <button type="submit">Chercher</button>
    </form>
    <p v-if="search.error.value" class="banner banner--error" role="alert">
      {{ search.error.value }}
    </p>
    <ul v-if="found.length" aria-label="Éléments trouvés">
      <li v-for="element in found" :key="element.id">
        <RouterLink :to="{ name: 'view-element', params: { id: element.id } }">{{
          element.name
        }}</RouterLink>
      </li>
    </ul>

    <h3>Diagrammes</h3>
    <p v-if="diagrams.status.value === 'error'" class="banner banner--error" role="alert">
      {{ diagrams.error.value }}
    </p>
    <p v-else-if="diagrams.status.value === 'ready' && diagrams.diagrams.value.length === 0" class="hint">
      Aucun diagramme.
    </p>
    <ul>
      <li v-for="diagram in diagrams.diagrams.value" :key="diagram.id">
        <RouterLink :to="{ name: 'view-diagram', params: { id: diagram.id } }">{{
          diagram.name
        }}</RouterLink>
        <span class="hint">{{ diagram.node_count }} élément{{ diagram.node_count > 1 ? 's' : '' }}</span>
      </li>
    </ul>
  </div>
</template>
```

`ViewElement.vue`:

```vue
<script setup lang="ts">
// One element, read: its facts, its links in both directions (each one a link),
// and its documents, rendered to safe HTML by the server (docs/adr/0042).
// The relation filter is in the URL (`?relation=`), like the neighbourhood's.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { useElementDetail } from '../elements/useElementDetail'
import { useMetamodel } from '../metamodel/useMetamodel'
import { RELATIONSHIP_LABELS, verbOf } from '../relationships/labels'

type GraphRead = components['schemas']['GraphRead']
type DocumentSummaryRead = components['schemas']['DocumentSummaryRead']
type RelationshipType = components['schemas']['RelationshipType']

const route = useRoute()
const router = useRouter()
const detail = useElementDetail()
const metamodel = useMetamodel()
const links = useLatestRequest()
const docs = useLatestRequest()
const reading = useLatestRequest()

const graph = ref<GraphRead>({ elements: [], relationships: [] })
const documents = ref<DocumentSummaryRead[]>([])
const opened = ref('')

const id = computed(() => String(route.params.id))
const relation = computed(() =>
  typeof route.query.relation === 'string' ? route.query.relation : '',
)
const names = computed(() => new Map(graph.value.elements.map((e) => [e.id, e.name])))
const rows = computed(() =>
  graph.value.relationships.map((link) => {
    const outgoing = link.source_id === id.value
    const otherId = outgoing ? link.target_id : link.source_id
    return {
      id: link.id,
      outgoing,
      verb: verbOf(link.relationship_type, link.access_type),
      otherId,
      otherName: names.value.get(otherId) ?? 'élément inconnu',
    }
  }),
)

async function load(): Promise<void> {
  opened.value = ''
  await detail.open(id.value)
  await links.run(
    async (signal) =>
      unwrap(
        await api.GET('/elements/{element_id}/relationships', {
          params: {
            path: { element_id: id.value },
            query: relation.value ? { relationship_type: [relation.value as RelationshipType] } : {},
          },
          signal,
        }),
      ),
    (answer) => {
      graph.value = answer
    },
  )
  await docs.run(
    async (signal) =>
      unwrap(
        await api.GET('/elements/{element_id}/documents', {
          params: { path: { element_id: id.value } },
          signal,
        }),
      ),
    (answer) => {
      documents.value = answer
    },
  )
}

async function read(documentId: string): Promise<void> {
  await reading.run(
    async (signal) =>
      unwrap(
        await api.GET('/documents/{document_id}/html', {
          params: { path: { document_id: documentId } },
          parseAs: 'text',
          signal,
        }),
      ),
    (html) => {
      opened.value = html
    },
  )
}

function filter(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  void router.replace({ query: { ...route.query, relation: value || undefined } })
}

onMounted(async () => {
  await metamodel.load()
})
watch(
  [id, relation],
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <article class="view-element">
    <p v-if="detail.status.value === 'error'" class="banner banner--error" role="alert">
      {{ detail.error.value }}
    </p>
    <template v-if="detail.element.value">
      <h2>{{ detail.element.value.name }}</h2>
      <p class="hint">{{ metamodel.labelOf(detail.element.value.element_type) }}</p>
      <p v-if="detail.element.value.description">{{ detail.element.value.description }}</p>
      <dl v-if="Object.keys(detail.element.value.properties).length">
        <template v-for="(value, key) in detail.element.value.properties" :key="key">
          <dt>{{ key }}</dt>
          <dd>{{ value }}</dd>
        </template>
      </dl>
    </template>

    <h3>Relations</h3>
    <label for="view-relation">Type de relation</label>
    <select id="view-relation" :value="relation" @change="filter">
      <option value="">Toutes</option>
      <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
        {{ RELATIONSHIP_LABELS[type.value] }}
      </option>
    </select>
    <p v-if="links.status.value === 'error'" class="banner banner--error" role="alert">
      {{ links.error.value }}
    </p>
    <p v-else-if="links.status.value === 'ready' && rows.length === 0" class="hint">
      Aucune relation.
    </p>
    <ul>
      <li v-for="row in rows" :key="row.id">
        <span v-if="!row.outgoing" aria-label="entrante">←</span>
        {{ row.verb }}
        <RouterLink :to="{ name: 'view-element', params: { id: row.otherId } }">{{
          row.otherName
        }}</RouterLink>
      </li>
    </ul>

    <h3>Documents</h3>
    <p v-if="docs.status.value === 'ready' && documents.length === 0" class="hint">
      Aucun document.
    </p>
    <ul>
      <li v-for="document in documents" :key="document.id">
        <button type="button" class="secondary" @click="read(document.id)">
          {{ document.filename }}
        </button>
      </li>
    </ul>
    <p v-if="reading.error.value" class="banner banner--error" role="alert">
      {{ reading.error.value }}
    </p>
    <!-- Safe: the server renders markdown with raw HTML off, images off and
         dangerous link schemes refused, and tests that (docs/adr/0042). -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-if="opened" class="markdown" v-html="opened" />
  </article>
</template>
```

`ViewDiagram.vue`:

```vue
<script setup lang="ts">
// A diagram, read: the server's own drawing (docs/adr/0043) as an image — fetched
// with the login's header and shown from a blob URL, because an `<img src>`
// cannot carry a bearer token and a token never goes into a URL — and under it
// the list of its elements, each a link, which is also the accessible reading.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import ReportDialog from '../reports/ReportDialog.vue'

type DiagramRead = components['schemas']['DiagramRead']

const route = useRoute()
const read = useLatestRequest()
const drawing = useLatestRequest()

const id = computed(() => String(route.params.id))
const diagram = ref<DiagramRead | null>(null)
const imageUrl = ref('')
const zoom = ref(1)

function release(): void {
  if (imageUrl.value) {
    URL.revokeObjectURL(imageUrl.value)
  }
  imageUrl.value = ''
}

async function load(diagramId: string): Promise<void> {
  diagram.value = null
  release()
  await read.run(
    async (signal) =>
      unwrap(
        await api.GET('/diagrams/{diagram_id}', {
          params: { path: { diagram_id: diagramId } },
          signal,
        }),
      ),
    (answer) => {
      diagram.value = answer
    },
  )
  if (!diagram.value) {
    return
  }
  await drawing.run(
    async (signal) =>
      unwrap(
        await api.GET('/diagrams/{diagram_id}/render.svg', {
          params: { path: { diagram_id: diagramId } },
          parseAs: 'blob',
          signal,
        }),
      ),
    (blob) => {
      release()
      imageUrl.value = URL.createObjectURL(blob)
    },
  )
}

function step(by: number): void {
  zoom.value = Math.min(3, Math.max(0.4, zoom.value + by))
}

onBeforeUnmount(release)
watch(
  id,
  (value) => {
    void load(value)
  },
  { immediate: true },
)
</script>

<template>
  <article class="view-diagram">
    <p v-if="read.error.value" class="banner banner--error" role="alert">{{ read.error.value }}</p>
    <p v-if="drawing.error.value" class="banner banner--error" role="alert">
      {{ drawing.error.value }}
    </p>
    <template v-if="diagram">
      <header>
        <h2>{{ diagram.name }}</h2>
        <p v-if="diagram.description" class="hint">{{ diagram.description }}</p>
        <ReportDialog subject-kind="diagram" :subject-id="diagram.id" download-only />
      </header>

      <div class="view-diagram__zoom" role="group" aria-label="Zoom">
        <button type="button" class="secondary" aria-label="Réduire" @click="step(-0.25)">−</button>
        <span>{{ Math.round(zoom * 100) }} %</span>
        <button type="button" class="secondary" aria-label="Agrandir" @click="step(0.25)">+</button>
      </div>
      <div class="view-diagram__frame">
        <img
          v-if="imageUrl"
          :src="imageUrl"
          :alt="`Diagramme ${diagram.name} : ${diagram.elements.length} éléments, ${diagram.relationships.length} relations`"
          :style="{ width: `${zoom * 100}%`, maxWidth: 'none' }"
        />
      </div>

      <h3>Éléments du diagramme</h3>
      <ul>
        <li v-for="element in diagram.elements" :key="element.id">
          <RouterLink :to="{ name: 'view-element', params: { id: element.id } }">{{
            element.name
          }}</RouterLink>
        </li>
      </ul>
    </template>
  </article>
</template>

<style scoped>
.view-diagram__frame {
  overflow: auto;
  border: 1px solid var(--border);
}
.view-diagram__zoom {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.75rem 0;
}
</style>
```

- [ ] **Step 4: Relancer, PASS**

Run: `cd frontend && npm test -- --run tests/view.spec.ts tests/router.spec.ts tests/AppNav.spec.ts tests/App.spec.ts && npm run lint && npm run typecheck`

- [ ] **Step 5: Commit** (Tasks 23 et 24 ensemble: les routes paresseuses n'ont de sens qu'avec leurs pages)

```bash
git add frontend/src/router/sections.ts frontend/src/router/index.ts frontend/src/App.vue frontend/src/features/view frontend/tests/view.spec.ts frontend/tests/router.spec.ts frontend/tests/AppNav.spec.ts frontend/tests/App.spec.ts
git commit -m "feat(spa): read-only consultation mode under /view

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 25: Pages de paquets du mode lecteur (différée: sous-projet 1)

Cette tâche **ne s'exécute pas** tant que les paquets n'existent pas. Elle ne contient volontairement aucun code: les points d'entrée de l'API des paquets sont ceux du sous-projet 1, qu'on ne peut pas écrire d'avance sans les inventer.

- [ ] **Step 1: Porte d'entrée.** Run: `grep -rln "packages" backend/src/ea/api backend/openapi.json | head`. Rien trouvé: laisser la tâche ouverte et passer à la Task 26.
- [ ] **Step 2: Si livré.** Ouvrir une session `superpowers:brainstorming` de trente minutes dont l'unique sortie est un addendum de trois lignes à la spec 07 (route `GET` de l'arbre, route `GET` du contenu d'un paquet, forme de la réponse), puis écrire un plan court pour `ViewPackage.vue` et l'arbre de `ViewHome.vue` sur le modèle exact de `ViewElement.vue` (Task 24) et de son test « GET seulement ». L'état du paquet ouvert reste dans le chemin (`/view/package/:id`), conformément à la règle du dépôt.

---

### Task 26: ADR du lot 3 (en entier)

**Files:**
- Create: `docs/adr/0044-publication-en-lecture.md`

- [ ] **Step 1: Écrire l'ADR** (vérifier que 0044 est libre, sinon décaler et corriger les références `docs/adr/0044` de `sections.ts`)

```markdown
---
titre: Publication en lecture par un mode lecteur du SPA
date: 2026-10-10
statut: Proposition
affects: frontend/src/features/view/, frontend/src/router/sections.ts, frontend/src/App.vue, api/documents.py (GET /documents/{id}/html)
---

# Publication en lecture par un mode lecteur du SPA

## Contexte

Des personnes ont besoin de lire le modèle (éléments, relations, diagrammes, documents) sans l'application complète ni le droit de la modifier: l'équivalent de WebEA pour Sparx. Le dépôt n'a aucune route anonyme (`require_caller` partout, ADR 0032) et aucune raison d'en ouvrir une pour cela.

## Décision

1. **Option A: un mode lecteur du SPA**, routes `/view/*` dans la même application, même origine, même authentification `oidc-client-ts`, mêmes endpoints d'API. Aucun jeton de plus, aucune surface réseau de plus. L'écran `/view` est une section du menu (« Consultation », groupe « Lecture ») dont la mise en page retire le menu d'édition.
2. **Aucune écriture dans `/view/*`**: pas de bouton, pas d'appel `POST`/`PUT`/`PATCH`/`DELETE`; testé écran par écran. La boîte de rapport y est en mode « téléchargement seul ». L'API reste l'autorité: un lecteur qui forgerait une écriture reçoit un 403 du service.
3. **État dans l'URL** (règle du dépôt): le sujet est dans le chemin (`/view/element/:id`, `/view/diagram/:id`), `?relation=` et `?q=` dans la requête.
4. **Le dessin d'un diagramme est celui du serveur** (ADR 0043), récupéré avec l'en-tête du login et affiché depuis une URL de blob dans une balise `<img>`: un `<img src>` ne peut pas porter de jeton et un jeton ne va jamais dans une URL; une image SVG ne peut pas exécuter de script. Sous le dessin, la liste des éléments (chacun un lien) est la lecture accessible.
5. **Les documents markdown sont rendus par le serveur** (`GET /documents/{id}/html`, ADR 0042): HTML brut désactivé, images désactivées, schémas `javascript:`/`data:` refusés, réponse avec politique de contenu fermée. Le SPA l'insère avec `v-html` seulement parce que cette sortie est neutralisée et testée.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| B: site statique exporté | Hébergeable partout, sans serveur | Copie périmée dès l'écriture suivante; droits figés à la génération; un second rendeur à tenir | Écarté |
| C: lien partageable à jeton | Lecteurs sans compte | Amende « tout appelant est authentifié » (ADR 0032); jeton, expiration, révocation, audit, route anonyme | Écarté tant qu'aucun besoin externe n'est exprimé; défaut s'il vient: partager un rapport déposé, jamais le modèle vivant |
| Redirection automatique des comptes sans `ea-editor` vers `/view` | Atterrissage adapté | Rend l'arrivée sur `/` asynchrone (un appel `/me`) et casse l'attente synchrone du routeur | Reportée; `/view` reste un choix du menu |

## Conséquences

- Les lecteurs ont un compte du realm `ea`: pas de partage externe en l'état. « Envoyer à un client » = télécharger un PDF et le transmettre soi-même.
- `/view/diagram` appelle un rendu à chaque ouverture; `ETag` (hachage du contenu) et `Cache-Control: private, no-cache` évitent de retransmettre un dessin inchangé, sans cache serveur partagé.
- Les pages de paquets (`/view/package/:id`) et l'arbre sur `/view` arrivent avec le sous-projet 1; l'écran d'accueil liste en attendant les diagrammes et une recherche par nom.
- Si l'équipe veut la redirection par rôle, c'est une décision à part (un appel `/me` avant d'atterrir).

## Références

- ADR liés: [0008](0008-sections-du-spa.md), [0027](0027-deploiement-via-portainer-et-nginx-de-l-infra.md), [0032](0032-authentification-keycloak.md), [0036](0036-fichiers-dans-minio.md), [0039](0039-metadonnees-des-fichiers-dans-postgresql.md), [0042](0042-generation-de-documents.md), [0043](0043-un-seul-dessin-de-diagramme.md)
- Code concerné: `frontend/src/features/view/`, `backend/src/ea/api/documents.py`
```

Avant de sauver, remplacer les noms de fichiers des liens (0008, 0027, 0032) par les vrais (`ls docs/adr | grep -E "^00(08|27|32)"`).

- [ ] **Step 2: Commit**

```bash
git add docs/adr/0044-publication-en-lecture.md
git commit -m "docs(adr): read-only publication through a reader mode of the SPA

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 27: Vérification du lot 3

- [ ] **Step 1:** `make openapi` puis `make openapi-check`.
- [ ] **Step 2:** `make check`.
- [ ] **Step 3:** Parcours manuel de l'utilisateur (le navigateur de l'agent ne remplace pas son œil): `make run`, ouvrir `http://localhost:5173/view`, chercher un élément, ouvrir un diagramme, zoomer, télécharger un rapport HTML. Les captures et la sensation de lecture sont à valider par l'utilisateur.
- [ ] **Step 4:** PR du lot 3.

---

# LOT 4: DOCX

### Task 28: Porte d'entrée, dépendance `python-docx`, décision sur les diagrammes

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`
- Modify: `docs/adr/0042-generation-de-documents.md` (section Conséquences)

- [ ] **Step 1: Porte d'entrée.** Run: `cd backend && uv run pytest tests/unit/test_report_renderers.py tests/e2e/test_report_api.py -q && grep -n "def available_renderers" src/ea/repositories/report_renderers.py`. Absent ou rouge: **s'arrêter**, les lots 1 et 2 ne sont pas livrés.

- [ ] **Step 2: Dépendance.** Run: `cd backend && uv add "python-docx>=1.1"`; commentaire au-dessus de la ligne dans `pyproject.toml`: `# DOCX reports; pure Python on top of lxml — see docs/adr/0042.`

- [ ] **Step 3: Décision sur les diagrammes dans le DOCX.** Défaut retenu: **repli sans `cairosvg`**. Word n'a pas de SVG fiable; rasteriser en PNG exige `cairosvg` et `libcairo2` dans l'image, que Pango n'apporte pas (WeasyPrint ne dépend plus de Cairo). Le DOCX remplace donc la figure par une note qui renvoie au HTML/PDF, et les tableaux d'éléments et de relations (déjà dans le document) restent la lecture du diagramme. Ajouter à la section « Conséquences » de l'ADR 0042: « Décision du lot 4: le DOCX n'embarque pas les diagrammes (repli documenté, aucun `cairosvg`). Si les utilisateurs l'exigent, ajouter `cairosvg` + `libcairo2` à l'image et rasteriser dans `docx_renderer`; mesurer alors le coût d'image comme à la Task 16. »

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock docs/adr/0042-generation-de-documents.md
git commit -m "build(reports): python-docx, and the decision not to rasterise diagrams

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 29: Sérialiseur DOCX

**Files:**
- Modify: `backend/src/ea/repositories/report_renderers.py` (imports; `docx_renderer`; `available_renderers`)
- Modify: `backend/tests/unit/test_report_renderers.py` (ajout)

**Interfaces:**
- Consumes: `xml_safe` (Task 2), blocs et `ReportModel` (Task 5).
- Produces: `docx_renderer() -> Renderer`; `available_renderers()` propose aussi `"docx"`.

- [ ] **Step 1: Écrire les tests qui échouent** (ajout dans `test_report_renderers.py`; imports: `import io`, `import zipfile`, `from ea.repositories.report_renderers import docx_renderer`)

```python
def document_xml(docx: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        return archive.read("word/document.xml").decode()


class TestTheDocx:
    def test_a_report_becomes_a_zip_with_a_document(self) -> None:
        docx = docx_renderer()(
            model(
                Heading(2, "Titre"),
                Paragraph("Texte"),
                Table(("Nom", "Type"), (("Facturation", "Application Component"),)),
                MarkdownBlock("# Doc\n\n- a\n"),
            )
        )

        xml = document_xml(docx)
        assert docx.startswith(b"PK")
        assert "Facturation" in xml and "Titre" in xml

    def test_markup_in_a_name_is_text_in_the_document(self) -> None:
        xml = document_xml(
            docx_renderer()(model(Paragraph("<w:p>&</w:p>"), Table(("<b>",), (("</w:t>",),))))
        )

        assert "&lt;w:p&gt;&amp;&lt;/w:p&gt;" in xml
        assert "&lt;/w:t&gt;" in xml

    def test_characters_xml_forbids_do_not_break_it(self) -> None:
        xml = document_xml(docx_renderer()(model(Paragraph("A\x00B\x0bC"), Heading(2, "D\x1fE"))))

        assert "ABC" in xml and "DE" in xml

    def test_a_diagram_is_replaced_by_a_note_not_embedded(self) -> None:
        svg = render_svg(a_detail([(an_element("Facturation"), 40, 40, 132, 46)]))

        xml = document_xml(docx_renderer()(model(Figure(svg, "Vente"))))

        assert "Vente" in xml
        assert "<svg" not in xml and "&lt;svg" not in xml

    def test_an_empty_report_is_still_a_valid_document(self) -> None:
        assert document_xml(docx_renderer()(model())).startswith("<?xml")

    def test_non_latin_text_survives(self) -> None:
        xml = document_xml(docx_renderer()(model(Paragraph("Ελληνικά Кириллица 日本語"))))

        assert "Ελληνικά Кириллица 日本語" in xml


def test_docx_is_offered_with_the_pdf_engine_when_it_loads() -> None:
    assert "docx" in available_renderers()
```

- [ ] **Step 2: Lancer, échec attendu** (`ImportError: docx_renderer`)

Run: `cd backend && uv run pytest tests/unit/test_report_renderers.py -x -k "Docx or docx"`

- [ ] **Step 3: Implémenter** (dans `report_renderers.py`)

Imports en tête: `import io`, `from ea.domain.diagram_render import xml_safe`, `from ea.domain.reports import Figure, Heading, MarkdownBlock, Paragraph, Table` (en plus de ceux existants).

```python
def docx_renderer() -> Renderer:
    """The DOCX engine (python-docx). Raises `ImportError` when it is not installed."""
    from docx import Document  # noqa: PLC0415 - optional engine, imported on demand

    def render(model: ReportModel) -> bytes:
        document = Document()
        document.core_properties.title = xml_safe(model.title)[:255]
        document.add_heading(xml_safe(model.title), level=0)
        document.add_paragraph(xml_safe(model.subtitle))
        for block in model.blocks:
            match block:
                case Heading():
                    document.add_heading(xml_safe(block.text), level=min(max(block.level, 1), 9))
                case Paragraph():
                    document.add_paragraph(xml_safe(block.text))
                case MarkdownBlock():
                    # The markdown source as plain lines: Word has no markdown. The
                    # syntax stays visible; a faithful conversion is a later lot.
                    for line in block.source.splitlines():
                        if line.strip():
                            document.add_paragraph(xml_safe(line))
                case Table():
                    table = document.add_table(rows=1, cols=max(len(block.headers), 1))
                    table.style = "Table Grid"
                    for cell, text in zip(table.rows[0].cells, block.headers, strict=False):
                        cell.text = xml_safe(text)
                    for row in block.rows:
                        for cell, text in zip(table.add_row().cells, row, strict=False):
                            cell.text = xml_safe(text)
                case Figure():
                    document.add_paragraph(
                        f"Diagramme « {xml_safe(block.caption)} » : le dessin figure dans le "
                        "rapport HTML ou PDF; ses éléments et ses relations sont listés "
                        "dans les tableaux de ce document."
                    )
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()

    return render
```

et dans `available_renderers`, le tuple devient `(("pdf", pdf_renderer), ("docx", docx_renderer))`.

- [ ] **Step 4: Relancer, PASS**

Run: `cd backend && uv run pytest tests/unit/test_report_renderers.py -x -rs && uv run ruff format src tests && uv run ruff check src tests && uv run mypy src`

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/report_renderers.py backend/tests/unit/test_report_renderers.py
git commit -m "feat(reports): DOCX serialiser, diagrams replaced by a pointer to HTML/PDF

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 30: e2e DOCX

**Files:**
- Modify: `backend/tests/e2e/test_report_api.py` (ajout en fin)

- [ ] **Step 1: Écrire le test** (le câblage est déjà fait par `available_renderers`; ce test échoue seulement si un maillon manque)

```python
class TestTheDocxFormat:
    async def test_a_docx_is_a_zip_with_a_document_and_deposits_like_the_others(
        self, service, diagrams, documents, files_store, files_metadata
    ) -> None:
        import io
        import zipfile

        from ea.repositories.report_renderers import docx_renderer

        async with a_client(
            service, diagrams, documents, files_store, files_metadata, {"docx": docx_renderer()}
        ) as client:
            diagram_id = await a_diagram(client, "Facturation")

            response = await client.get("/reports/render", params=render_params(diagram_id, "docx"))
            deposit = await client.post(
                "/reports",
                json={
                    "template": "fiche-diagramme",
                    "subject": {"kind": "diagram", "id": diagram_id},
                    "format": "docx",
                },
            )

        assert response.status_code == 200
        assert response.headers["content-type"].endswith("wordprocessingml.document")
        assert response.headers["content-disposition"].endswith(".docx")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "word/document.xml" in archive.namelist()
            assert "Facturation" in archive.read("word/document.xml").decode()
        assert deposit.status_code == 201
        assert deposit.json()["key"].endswith(".docx")
```

- [ ] **Step 2: Lancer**

Run: `cd backend && uv run pytest tests/e2e/test_report_api.py -x -k Docx`
Attendu: PASS dès la Task 29 (si `docx_renderer` n'était pas exporté, l'échec est un `ImportError`).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_report_api.py
git commit -m "test(reports): DOCX end to end through the API

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 31: Vérification du lot 4

- [ ] **Step 1:** `make openapi` puis `make openapi-check` (aucune différence: aucun schéma n'a bougé).
- [ ] **Step 2:** `make check`.
- [ ] **Step 3:** Ouvrir un DOCX produit (`GET /reports/render?...&format=docx` téléchargé depuis le SPA) dans Word ou Pages: vérifier à l'œil les tableaux et la note de diagramme. Cette lecture est à faire par l'utilisateur.
- [ ] **Step 4:** Reconstruire l'image sur la machine de l'utilisateur (commandes de la Task 16): `python-docx` est en Python pur, aucun paquet système ne s'ajoute, mais le `uv.lock` a changé.
- [ ] **Step 5:** PR du lot 4.

---

# LOT 5 (si demandé): cadrage seulement

Ce lot n'est pas planifié en détail: aucune demande ne l'exige. Le plan se limite à produire la décision.

### Task 32: Cadrage des suites éventuelles

**Files:**
- Create: `docs/superpowers/specs/<date>-07b-publication-suites-design.md` (seulement si la demande existe)

- [ ] **Step 1: Poser la question à l'utilisateur** (une seule): « Faut-il (a) des modèles de rapport éditables ou (b) un partage externe à lien à jeton ? Rien n'est demandé tant que tu ne l'as pas dit. » Sans réponse positive, la tâche s'arrête: aucun code, aucun ADR.

- [ ] **Step 2: Si (a)**, ouvrir `superpowers:brainstorming` avec ce point de départ de la spec: table append-only `report_templates(id uuid pk, slug, version int, title, definition jsonb, created_by, created_at, unique(slug, version))`, migration Alembic (jamais avant), CRUD réservé à `ea-editor`, validation Pydantic `extra="forbid"` de la définition (les sections de `domain/reports.py` en sont déjà la forme), éditeur SPA, ADR dédié.

- [ ] **Step 3: Si (b)**, ouvrir `superpowers:brainstorming` avec ce point de départ: partager un rapport **déposé**, jamais le modèle vivant; jeton aléatoire de 256 bits stocké haché, expiration de 30 jours au plus, révocable, événement d'audit à la création, à l'ouverture et à la révocation, route anonyme limitée à cet objet, ADR qui amende « tout appelant est authentifié » (ADR 0032). C'est la seule suite qui touche à la sécurité: elle demande une revue dédiée.

---

### Task 33: Clôture, `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (paragraphe « Project status »)

- [ ] **Step 1:** Dans `CLAUDE.md`, remplacer `as thirty-four tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing and the files of the bucket —` par `as thirty-six tools — the element CRUD, the links, the two traversals, the metamodel, the markdown attached to an element, the IP addressing, the files of the bucket and the report generation —` (vérifier le texte exact par `grep -n "thirty-four" CLAUDE.md` avant d'éditer).

- [ ] **Step 2:** Dans le même paragraphe, juste avant la phrase `This file records the *decisions already made*`, insérer la phrase unique:

```
Reports (HTML, PDF, DOCX) of a diagram are built by `ReportService` from one neutral model, downloaded from `GET /reports/render` or deposited by editors under `reports/` of the bucket by `POST /reports` (and the `generate_report` tool), drawn with the single static SVG of `domain/diagram_render.py` that the SPA's read-only `/view` mode also shows (see `docs/adr/0042`, `0043` and `0044`).
```

- [ ] **Step 3:** `make check` (le test `test_deploy_stack` et les autres lisent des fichiers, pas `CLAUDE.md`; vérifier malgré tout) puis commit:

```bash
git add CLAUDE.md
git commit -m "docs: record reports and the reader mode in CLAUDE.md

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revue

**Couverture de la spec**
- §3 domaine: modèle (Task 5), plafonds (Tasks 5, 7, 11), `ReportModel` (Task 5), dessin SVG unique (Tasks 2-3, ADR 0043).
- §4 données: aucune migration; clé et fiche (Task 8); `reports/` sans liste de chemins à modifier (écart 3).
- §5 API: quatre routes (Task 9) plus `/documents/{id}/html` (Task 22); `Content-Disposition` assaini, CSP du SVG, 429 par `sub`, délai 30 s par horloge injectée (Task 7). Réserve consignée: `proxy_read_timeout` à 60 s dans le dépôt Infra (ADR 0042).
- §6 MCP: Task 19 (34 → 36, `generate_report` ne renvoie jamais d'octets, fournisseur obligatoire).
- §7 SPA: boîte de rapport (Task 20), mode lecteur (Tasks 23-24), documents rendus côté serveur (Task 22). Bouton « fiche d'un paquet » et « requêtes enregistrées »: avec le sous-projet 1 (Tasks 11, 25).
- §8 sécurité: échappement (Tasks 2, 5, 6, 29), récupérateur fermé (Task 17), plafonds et exclusivité (Task 7), journal (Task 8), lecteur refusé (Tasks 8, 9, 19, 20).
- §9 tests: unité, intégration (Task 12), API, parité (Task 3), SPA « aucune écriture » (Task 24), garde `socket` conservée. E2E Playwright: non disponible (CLAUDE.md), consigné.
- §10 ADR: 0042, 0043 (lot 1), 0044 (lot 3).
- §11/§12: HTML autonome toujours livrable et 501 typé (Tasks 7, 18); DOCX repli (Task 28); police embarquée (Task 15); lots 1 à 5 respectés, le lot 5 en cadrage seulement (Task 32).

**Marqueurs d'attente:** aucun. Deux tâches contiennent volontairement des étapes sans code et le disent: Task 25 (pages de paquets, gardée par le sous-projet 1) et Task 32 (cadrage). Tous les autres pas d'écriture de code ont un bloc de code.

**Cohérence des noms:** `ReportService.build/deposit/list_templates/formats`; `ReportRequest(template_id, subject, format)` côté domaine et `ReportCreate(template, subject, format)` côté API; `report_renderers` (paramètre de `create_app`) / `app.state.report_renderers` / `extra_renderers` (paramètre du service); `available_renderers`, `pdf_renderer`, `docx_renderer`, `refuse_every_url`; `render_svg`, `LAYER_FILL`, `BOX_INK`, `xml_safe`, `shorten` (Python) / `shorten`, `CANVAS_MARGIN` (TypeScript); routes `view-home`, `view-element`, `view-diagram`; clé de journal `report_format` (jamais `format`, qui n'est pas un champ de `LogRecord` mais prête à confusion).
