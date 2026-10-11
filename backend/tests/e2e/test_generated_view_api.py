"""Generated views end to end: live by default, frozen on request, never silently edited."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.generated_views import QuerySource
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryViewSources,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams, view_sources: InMemoryViewSources
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        view_sources=view_sources,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def secured(
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
    view_sources: InMemoryViewSources,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        view_sources=view_sources,  # type: ignore[arg-type]
        verifier=StaticVerifier({EDITOR: an_editor(), READER: a_reader()}),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def an_element(client: httpx.AsyncClient, name: str) -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": name}
    )
    return str(response.json()["id"])


async def revision_of(client: httpx.AsyncClient, diagram: str) -> int:
    return int((await client.get(f"/diagrams/{diagram}")).json()["revision"])


def by_query(query_id: UUID) -> dict[str, Any]:
    return {"query_id": str(query_id), "package_id": None, "recursive": False}


def box(element: str, x: float = 10.0) -> dict[str, Any]:
    return {
        "element_id": element,
        "x": x,
        "y": 20.0,
        "width": 132.0,
        "height": 46.0,
        "parent_object_id": None,
        "z": 0,
    }


class TestGenerating:
    async def test_a_generated_view_is_live_and_shows_its_source(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        a = await an_element(client, "A")
        query = uuid4()
        view_sources.answers[QuerySource(query)] = [UUID(a)]

        response = await client.post(
            "/diagrams/generate", json={"name": "Périmètre", "source": by_query(query)}
        )

        assert response.status_code == 201
        body = response.json()
        assert (body["generation"], body["nodes"]) == ("live", [])
        assert [e["id"] for e in body["elements"]] == [a]
        assert body["source"]["query_id"] == str(query)

    async def test_a_source_that_does_not_fit_is_a_422_with_the_count(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        query = uuid4()
        view_sources.answers[QuerySource(query)] = [uuid4() for _ in range(501)]

        response = await client.post(
            "/diagrams/generate", json={"name": "Trop", "source": by_query(query)}
        )

        assert (response.status_code, response.json()["error"]) == (422, "generated_view_too_large")
        assert "501" in response.json()["detail"]

    async def test_a_body_naming_both_ids_or_neither_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        both = {"query_id": str(uuid4()), "package_id": str(uuid4()), "recursive": False}
        neither = {"query_id": None, "package_id": None, "recursive": False}

        for source in (both, neither):
            response = await client.post("/diagrams/generate", json={"name": "X", "source": source})
            assert response.status_code == 422

    async def test_a_missing_source_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/diagrams/generate", json={"name": "X", "source": by_query(uuid4())}
        )

        assert response.status_code == 404

    async def test_a_live_view_refuses_a_layout(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        query = uuid4()
        view_sources.answers[QuerySource(query)] = []
        view = (
            await client.post("/diagrams/generate", json={"name": "V", "source": by_query(query)})
        ).json()

        response = await client.put(
            f"/diagrams/{view['id']}/layout",
            json={"revision": 0, "nodes": [], "objects": [], "edges": []},
        )

        assert (response.status_code, response.json()["error"]) == (409, "diagram_is_live")


class TestFreezingAndRefreshing:
    async def _view(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> tuple[dict[str, Any], str, UUID]:
        a = await an_element(client, "A")
        query = uuid4()
        view_sources.answers[QuerySource(query)] = [UUID(a)]
        view = (
            await client.post("/diagrams/generate", json={"name": "V", "source": by_query(query)})
        ).json()
        return view, a, query

    async def test_freezing_stores_the_layout_and_the_view_becomes_editable(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        view, a, _ = await self._view(client, view_sources)

        frozen = await client.post(
            f"/diagrams/{view['id']}/freeze", json={"revision": 0, "nodes": [box(a)]}
        )

        assert frozen.status_code == 200
        assert (frozen.json()["generation"], [n["element_id"] for n in frozen.json()["nodes"]]) == (
            "frozen",
            [a],
        )
        twice = await client.post(
            f"/diagrams/{view['id']}/freeze", json={"revision": 0, "nodes": [box(a)]}
        )
        assert (twice.status_code, twice.json()["error"]) == (409, "diagram_not_live")

    async def test_the_source_diff_shows_the_gap_without_writing_it(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        view, a, query = await self._view(client, view_sources)
        await client.post(f"/diagrams/{view['id']}/freeze", json={"revision": 0, "nodes": [box(a)]})
        b = await an_element(client, "B")
        view_sources.answers[QuerySource(query)] = [UUID(b)]

        diff = await client.get(f"/diagrams/{view['id']}/source-diff")

        assert diff.status_code == 200
        assert [e["id"] for e in diff.json()["added"]] == [b]
        assert diff.json()["removed"] == [a]
        assert [
            n["element_id"] for n in (await client.get(f"/diagrams/{view['id']}")).json()["nodes"]
        ] == [a]

    async def test_thawing_refuses_to_lose_a_note_unless_discard_is_explicit(
        self, client: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        view, a, _ = await self._view(client, view_sources)
        await client.post(f"/diagrams/{view['id']}/freeze", json={"revision": 0, "nodes": [box(a)]})
        note = {
            "id": str(uuid4()),
            "kind": "note",
            "x": 0.0,
            "y": 0.0,
            "width": 100.0,
            "height": 50.0,
            "text": "à voir",
            "colour": None,
            "z": 0,
            "parent_id": None,
            "anchor_element_id": None,
        }
        current = (await client.get(f"/diagrams/{view['id']}")).json()["revision"]
        await client.put(
            f"/diagrams/{view['id']}/layout",
            json={"revision": current, "nodes": [box(a)], "objects": [note], "edges": []},
        )

        refused = await client.post(
            f"/diagrams/{view['id']}/unfreeze",
            json={"revision": await revision_of(client, view["id"]), "discard_annotations": False},
        )
        thawed = await client.post(
            f"/diagrams/{view['id']}/unfreeze",
            json={"revision": await revision_of(client, view["id"]), "discard_annotations": True},
        )

        assert (refused.status_code, refused.json()["error"]) == (409, "has_annotations")
        assert (thawed.status_code, thawed.json()["generation"]) == (200, "live")
        assert thawed.json()["objects"] == []


class TestWhoMay:
    async def test_a_reader_opens_a_live_view_but_cannot_generate_freeze_or_thaw(
        self, secured: httpx.AsyncClient, view_sources: InMemoryViewSources
    ) -> None:
        editor = {"Authorization": f"Bearer {EDITOR}"}
        reader = {"Authorization": f"Bearer {READER}"}
        query = uuid4()
        view_sources.answers[QuerySource(query)] = []
        view = (
            await secured.post(
                "/diagrams/generate", json={"name": "V", "source": by_query(query)}, headers=editor
            )
        ).json()

        opened = await secured.get(f"/diagrams/{view['id']}", headers=reader)
        generate = await secured.post(
            "/diagrams/generate", json={"name": "W", "source": by_query(query)}, headers=reader
        )
        freeze = await secured.post(
            f"/diagrams/{view['id']}/freeze", json={"revision": 0, "nodes": []}, headers=reader
        )
        thaw = await secured.post(
            f"/diagrams/{view['id']}/unfreeze",
            json={"revision": 0, "discard_annotations": True},
            headers=reader,
        )

        assert opened.status_code == 200
        assert (generate.status_code, freeze.status_code, thaw.status_code) == (403, 403, 403)
