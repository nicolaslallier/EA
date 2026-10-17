"""The report endpoints end to end, over in-memory stores."""

from __future__ import annotations

import io
import zipfile
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
from ea.repositories.report_renderers import available_renderers, docx_renderer
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryDocuments,
    InMemoryFileMetadata,
    InMemoryObjectStore,
    InMemoryPackages,
    StaticVerifier,
    a_reader,
    an_editor,
    an_empty_package_tree,
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
        **{"packages": an_empty_package_tree(), **extra},
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
    client: httpx.AsyncClient,
    *names: str,
    title: str = "Vente",
    headers: dict[str, str] | None = None,
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
        "revision": 0,
        "nodes": [
            {
                "element_id": i,
                "x": 10.0 * n,
                "y": 20.0,
                "width": 200.0,
                "height": 80.0,
                "parent_object_id": None,
                "z": 0,
            }
            for n, i in enumerate(ids)
        ],
        "objects": [],
        "edges": [],
    }
    placed = await client.put(
        f"/diagrams/{diagram.json()['id']}/layout", json=layout, headers=headers
    )
    assert placed.status_code == 204, placed.text
    return str(diagram.json()["id"])


def render_params(
    diagram_id: str, fmt: str = "html", template: str = "fiche-diagramme"
) -> dict[str, str]:
    return {
        "template": template,
        "subject_kind": "diagram",
        "subject_id": diagram_id,
        "format": fmt,
    }


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
            },
            {
                "id": "inventaire-paquet",
                "version": 1,
                "title": "Inventaire d'un paquet",
                "subjects": ["package"],
                "formats": ["html"],
            },
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
        template = await client.get(
            "/reports/render", params=render_params(diagram_id, template="nope")
        )
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
            await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT, name=f"E{i}"
            )
            for i in range(MAX_REPORT_ELEMENTS + 1)
        ]
        await use_cases.replace_layout(
            diagram.id,
            revision=0,
            nodes=[DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)],
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


class TestPackageReports:
    async def test_a_package_inventory_lists_its_elements_and_draws_its_diagrams(
        self,
        service: ArchitectureService,
        diagrams: InMemoryDiagrams,
        documents: InMemoryDocuments,
        packages: InMemoryPackages,
        files_store: InMemoryObjectStore,
        files_metadata: InMemoryFileMetadata,
    ) -> None:
        app = an_app(service, diagrams, documents, files_store, files_metadata, packages=packages)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            package = (await client.post("/packages", json={"name": "Finance"})).json()
            element = (
                await client.post(
                    "/elements",
                    json={"element_type": "application_component", "name": "Facturation"},
                )
            ).json()
            moved = await client.post(
                "/elements/move", json={"element_ids": [element["id"]], "package_id": package["id"]}
            )
            assert moved.status_code == 204, moved.text
            diagram = (
                await client.post("/diagrams", json={"name": "Vue", "package_id": package["id"]})
            ).json()
            await client.put(
                f"/diagrams/{diagram['id']}/layout",
                json={
                    "revision": 0,
                    "objects": [],
                    "edges": [],
                    "nodes": [
                        {
                            "element_id": element["id"],
                            "x": 10,
                            "y": 10,
                            "width": 132,
                            "height": 46,
                            "parent_object_id": None,
                            "z": 0,
                        }
                    ],
                },
            )

            response = await client.get(
                "/reports/render",
                params={
                    "template": "inventaire-paquet",
                    "subject_kind": "package",
                    "subject_id": package["id"],
                    "format": "html",
                },
            )

        assert response.status_code == 200, response.text
        assert "Facturation" in response.text
        assert "<svg" in response.text


FAKE_PDF = b"%PDF-1.7 fake"


def a_client(
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
    documents: InMemoryDocuments,
    files_store: InMemoryObjectStore,
    files_metadata: InMemoryFileMetadata,
    renderers: Any,
) -> httpx.AsyncClient:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,
        documents=documents,
        files=files_store,
        file_metadata=files_metadata,
        report_renderers=renderers,
        packages=an_empty_package_tree(),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestThePdfFormat:
    async def test_an_offered_format_is_listed_and_served(
        self,
        service: ArchitectureService,
        diagrams: InMemoryDiagrams,
        documents: InMemoryDocuments,
        files_store: InMemoryObjectStore,
        files_metadata: InMemoryFileMetadata,
    ) -> None:
        renderers = {"pdf": lambda model: FAKE_PDF}
        async with a_client(
            service, diagrams, documents, files_store, files_metadata, renderers
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
        self,
        service: ArchitectureService,
        diagrams: InMemoryDiagrams,
        documents: InMemoryDocuments,
        files_store: InMemoryObjectStore,
        files_metadata: InMemoryFileMetadata,
    ) -> None:
        detected = available_renderers()
        if "pdf" not in detected:
            pytest.skip("WeasyPrint is not loadable here; see docs/adr/0062")
        async with a_client(
            service, diagrams, documents, files_store, files_metadata, detected
        ) as client:
            diagram_id = await a_diagram(client, "Facturation Ελληνικά")

            response = await client.get("/reports/render", params=render_params(diagram_id, "pdf"))

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")


class TestTheDocxFormat:
    async def test_a_docx_is_a_zip_with_a_document_and_deposits_like_the_others(
        self,
        service: ArchitectureService,
        diagrams: InMemoryDiagrams,
        documents: InMemoryDocuments,
        files_store: InMemoryObjectStore,
        files_metadata: InMemoryFileMetadata,
    ) -> None:
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
