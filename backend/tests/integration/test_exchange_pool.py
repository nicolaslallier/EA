"""Slow downloads leave the connection pool alone (docs/adr/0048).

A pool of two connections, and three downloads whose clients never read a byte: if an export held
its snapshot while it was sent, the third would wait for a connection that never comes back, and
so would every other request of the API. It holds none: the file is ready, the snapshot is over.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.exchange import ExchangeFormat, ExportRequest
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.fixture
def small_pool(engine_at_head: AsyncEngine) -> AsyncEngine:
    return create_async_engine(
        engine_at_head.url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
    )


@pytest.mark.parametrize("fmt", [ExchangeFormat.ARCHIMATE_XML, ExchangeFormat.CSV])
async def test_three_downloads_nobody_reads_hold_no_connection(
    small_pool: AsyncEngine, fmt: ExchangeFormat
) -> None:
    sessions = create_session_factory(small_pool)
    architecture = ArchitectureService(PostgresArchitectureRepository(sessions))
    for number in range(20):
        await architecture.create_element(element_type=E.NODE, name=f"n{number}")
    exchange = ExchangeService(PostgresExchangeRepository(sessions), architecture)

    exports = [await exchange.export(ExportRequest(format=fmt)) for _ in range(3)]

    assert small_pool.pool.checkedout() == 0  # type: ignore[attr-defined]  # nothing is held
    # ... so an ordinary request is served while three clients sit on their files
    assert len(await architecture.list_elements(ElementFilter(limit=100))) == 20
    for export in exports:
        data = b"".join([chunk async for chunk in export.chunks])
        assert b"n19" in data or fmt is ExchangeFormat.CSV
    await small_pool.dispose()
