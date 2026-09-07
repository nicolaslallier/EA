"""The document endpoints, end to end against doubled stores.

`multipart/form-data` is the one thing this API takes that is not JSON, so the
uploads here go through `httpx`'s own `files=` rather than a hand-built body:
what is under test is the request a browser actually sends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.documents import MAX_DOCUMENT_BYTES
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryDocuments

RUNBOOK = b"# Runbook\n\nRedemarrer le service.\n"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, documents: InMemoryDocuments
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with both stores doubled, driven without ever starting it."""
    app = create_app(
        Settings(debug=True),
        architecture_service=service,
        documents=documents,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def an_element(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/elements", json={"element_type": "application_component", "name": "Facturation"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def markdown(name: str = "runbook.md", raw: bytes = RUNBOOK) -> dict[str, Any]:
    """The multipart part a browser sends for a picked file."""
    return {"file": (name, raw, "text/markdown")}


async def attach(client: httpx.AsyncClient, element_id: str, **kwargs: Any) -> dict[str, Any]:
    response = await client.post(f"/elements/{element_id}/documents", files=markdown(**kwargs))
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestAttaching:
    async def test_an_uploaded_file_comes_back_with_its_text_and_its_size(
        self, client: httpx.AsyncClient
    ) -> None:
        element_id = await an_element(client)

        body = await attach(client, element_id)

        assert body["filename"] == "runbook.md"
        assert body["content"] == RUNBOOK.decode()
        assert body["byte_size"] == len(RUNBOOK)
        assert body["element_id"] == element_id

    async def test_attaching_to_an_unknown_element_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(f"/elements/{uuid4()}/documents", files=markdown())

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    async def test_the_same_file_name_twice_is_a_409(self, client: httpx.AsyncClient) -> None:
        element_id = await an_element(client)
        await attach(client, element_id)

        response = await client.post(f"/elements/{element_id}/documents", files=markdown())

        assert response.status_code == 409
        assert response.json()["error"] == "duplicate"

    async def test_a_file_that_is_not_markdown_is_a_422(self, client: httpx.AsyncClient) -> None:
        element_id = await an_element(client)

        response = await client.post(
            f"/elements/{element_id}/documents", files=markdown(name="notes.txt")
        )

        assert response.status_code == 422
        assert "markdown" in response.json()["detail"]

    async def test_a_file_that_is_not_text_is_a_422_and_not_a_500(
        self, client: httpx.AsyncClient
    ) -> None:
        """The store holds `text`; bytes that are not text are refused, not cast."""
        element_id = await an_element(client)

        response = await client.post(
            f"/elements/{element_id}/documents",
            files=markdown(raw=b"\xff\xfe\x00\x01binaire"),
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_input"

    async def test_a_file_past_the_size_limit_is_refused(self, client: httpx.AsyncClient) -> None:
        element_id = await an_element(client)

        response = await client.post(
            f"/elements/{element_id}/documents",
            files=markdown(raw=b"#" * (MAX_DOCUMENT_BYTES + 10)),
        )

        assert response.status_code == 422
        assert "larger than" in response.json()["detail"]

    async def test_a_request_without_a_file_is_rejected_by_the_schema(
        self, client: httpx.AsyncClient
    ) -> None:
        element_id = await an_element(client)

        response = await client.post(f"/elements/{element_id}/documents")

        assert response.status_code == 422


@pytest.mark.asyncio
class TestReading:
    async def test_a_listing_names_the_files_and_never_carries_their_text(
        self, client: httpx.AsyncClient
    ) -> None:
        """The whole reason `DocumentSummaryRead` exists as its own model."""
        element_id = await an_element(client)
        await attach(client, element_id)

        response = await client.get(f"/elements/{element_id}/documents")

        assert response.status_code == 200
        [listed] = response.json()
        assert listed["filename"] == "runbook.md"
        assert "content" not in listed

    async def test_an_element_with_nothing_attached_lists_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        element_id = await an_element(client)

        response = await client.get(f"/elements/{element_id}/documents")

        assert (response.status_code, response.json()) == (200, [])

    async def test_listing_the_documents_of_an_unknown_element_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/elements/{uuid4()}/documents")

        assert response.status_code == 404

    async def test_one_document_is_read_with_its_markdown(self, client: httpx.AsyncClient) -> None:
        element_id = await an_element(client)
        attached = await attach(client, element_id)

        response = await client.get(f"/documents/{attached['id']}")

        assert response.status_code == 200
        assert response.json()["content"] == RUNBOOK.decode()

    async def test_reading_a_document_that_is_gone_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/documents/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"


@pytest.mark.asyncio
class TestRevisingAndRemoving:
    async def test_a_new_version_of_the_same_file_replaces_the_content(
        self, client: httpx.AsyncClient
    ) -> None:
        element_id = await an_element(client)
        attached = await attach(client, element_id)

        response = await client.put(
            f"/documents/{attached['id']}",
            files=markdown(raw=b"# Runbook v2\n"),
        )

        assert response.status_code == 200
        assert response.json()["id"] == attached["id"]
        assert response.json()["content"] == "# Runbook v2\n"

    async def test_uploading_a_differently_named_file_over_one_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        element_id = await an_element(client)
        attached = await attach(client, element_id)

        response = await client.put(f"/documents/{attached['id']}", files=markdown(name="autre.md"))

        assert response.status_code == 422
        assert "autre.md" in response.json()["detail"]

    async def test_a_document_is_removed_on_its_own(self, client: httpx.AsyncClient) -> None:
        element_id = await an_element(client)
        attached = await attach(client, element_id)

        removed = await client.delete(f"/documents/{attached['id']}")
        listed = await client.get(f"/elements/{element_id}/documents")

        assert removed.status_code == 204
        assert listed.json() == []

    async def test_removing_a_document_that_is_gone_is_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await client.delete(f"/documents/{uuid4()}")).status_code == 404

    async def test_deleting_the_element_takes_its_documents_with_it(
        self, client: httpx.AsyncClient
    ) -> None:
        """No foreign key can do this — the element is a node in the graph."""
        element_id = await an_element(client)
        attached = await attach(client, element_id)

        await client.delete(f"/elements/{element_id}")
        orphan = await client.get(f"/documents/{attached['id']}")

        assert orphan.status_code == 404


@pytest.mark.asyncio
async def test_the_document_routes_say_so_when_the_relational_store_is_shut(
    service: ArchitectureService,
) -> None:
    """`EA_POSTGRES_ENABLED` off is a wiring fault, not "this element has none".

    A 500 rather than an empty list on purpose: an operator who turned the
    store off must not be told, quietly, that every element lost its files.
    """
    app = create_app(Settings(debug=True), architecture_service=service)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/elements/{uuid4()}/documents")

    assert response.status_code == 500
