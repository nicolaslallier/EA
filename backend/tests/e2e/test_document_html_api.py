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
