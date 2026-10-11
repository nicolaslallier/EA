"""POST /exchanges/imports end to end against a real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.postgres import create_session_factory
from ea.main import create_app
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_editor
from tests.exchange_files import DATA

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest_asyncio.fixture
async def client(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=graph_service,
        exchanges=PostgresExchangeRepository(create_session_factory(engine_at_head)),
        verifier=StaticVerifier({"reader": a_reader(), "editor": an_editor()}),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": "Bearer editor"}
    ) as client:
        yield client


def upload(name: str, **form: str) -> dict[str, object]:
    data = (DATA / name).read_bytes()
    return {"files": {"file": ("model.xml", data, "application/xml")}, "data": form}


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def test_a_dry_run_writes_nothing_a_real_import_commits_and_replaying_creates_nothing(
    client: httpx.AsyncClient, engine_at_head: AsyncEngine
) -> None:
    simulated = await client.post("/exchanges/imports", **upload("minimal-model.xml"))
    assert simulated.status_code == 200 and simulated.json()["applied"] is False
    assert await count(engine_at_head, "elements") == 0
    assert await count(engine_at_head, "audit_events") == 0

    real = await client.post("/exchanges/imports", **upload("minimal-model.xml", dry_run="false"))
    assert real.json()["applied"] is True
    assert real.json()["counts"] == simulated.json()["counts"]  # the simulation told the truth
    assert (
        await count(engine_at_head, "elements"),
        await count(engine_at_head, "relationships"),
    ) == (
        3,
        2,
    )

    again = await client.post("/exchanges/imports", **upload("minimal-model.xml", dry_run="false"))
    assert again.json()["counts"]["elements"]["created"] == 0
    assert again.json()["counts"]["relationships"]["created"] == 0
    assert (
        await count(engine_at_head, "elements"),
        await count(engine_at_head, "relationships"),
    ) == (
        3,
        2,
    )


async def test_a_reader_is_refused_even_for_a_simulation(
    client: httpx.AsyncClient, engine_at_head: AsyncEngine
) -> None:
    response = await client.post(
        "/exchanges/imports",
        headers={"Authorization": "Bearer reader"},
        **upload("minimal-model.xml"),
    )

    assert response.status_code == 403
    assert await count(engine_at_head, "elements") == 0


async def test_strict_with_a_refused_row_writes_nothing_and_applied_is_false(
    client: httpx.AsyncClient, engine_at_head: AsyncEngine
) -> None:
    response = await client.post(
        "/exchanges/imports", **upload("refused-relations.xml", dry_run="false", strict="true")
    )

    body = response.json()
    assert response.status_code == 200 and body["applied"] is False
    assert body["issues"][-1]["code"] == "strict_refused"
    assert await count(engine_at_head, "elements") == 0
    assert await count(engine_at_head, "audit_events") == 0


async def test_without_strict_the_refused_row_is_reported_and_the_rest_is_written(
    client: httpx.AsyncClient, engine_at_head: AsyncEngine
) -> None:
    response = await client.post(
        "/exchanges/imports", **upload("refused-relations.xml", dry_run="false")
    )

    body = response.json()
    assert body["applied"] is True and body["counts"]["relationships"]["rejected"] == 1
    assert (
        await count(engine_at_head, "elements"),
        await count(engine_at_head, "relationships"),
    ) == (
        3,
        1,
    )
