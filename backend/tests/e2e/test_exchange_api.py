"""The exchange endpoints, end to end against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.archimate import ElementType as E
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryExchange, StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        exchanges=exchange_store,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestExport:
    async def test_it_streams_the_model_as_an_xml_attachment(
        self, client: httpx.AsyncClient, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv-1")

        response = await client.get("/exchanges/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert response.headers["content-disposition"].startswith(
            "attachment; filename*=UTF-8''ea-model-"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "srv-1" in response.text

    async def test_an_unknown_scope_is_a_typed_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/exchanges/export", params={"scope": "galaxy"})

        assert response.status_code == 422
        assert response.json()["error"] == "unknown_scope"

    async def test_the_file_name_ignores_anything_the_caller_sends(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/exchanges/export", params={"filename": "../../etc/passwd", "format": "archimate-xml"}
        )

        assert "passwd" not in response.headers["content-disposition"]

    async def test_an_unknown_format_is_a_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/exchanges/export", params={"format": "xmi"})

        assert response.status_code == 422


class TestAuthentication:
    async def test_401_without_a_token_and_200_for_a_reader(
        self, service: ArchitectureService, exchange_store: InMemoryExchange
    ) -> None:
        verifier = StaticVerifier({"reader": a_reader(), "editor": an_editor()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            exchanges=exchange_store,  # type: ignore[arg-type]
            verifier=verifier,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/exchanges/export")).status_code == 401
            reader = await client.get(
                "/exchanges/export", headers={"Authorization": "Bearer reader"}
            )
            assert reader.status_code == 200
