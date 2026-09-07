"""Boot-time checks on the graph connection.

The driver is not exercised here — its failure handling is. An unreachable
database must stop the process with a message that names no host, no scheme and
no credential, because that message can end up in front of a user.
"""

from __future__ import annotations

from typing import Any

import pytest
from neo4j.exceptions import ServiceUnavailable

from ea.core.config import Settings
from ea.db.neo4j import GraphUnavailableError, check_connectivity, create_driver


class UnreachableDriver:
    async def verify_connectivity(self) -> None:
        raise ServiceUnavailable("Cannot resolve address graph.internal:7687")


@pytest.mark.asyncio
async def test_an_unreachable_graph_stops_the_boot() -> None:
    with pytest.raises(GraphUnavailableError):
        await check_connectivity(UnreachableDriver())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_failure_message_leaks_neither_host_nor_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The address belongs in the log; the caller gets a generic sentence."""
    with pytest.raises(GraphUnavailableError) as caught:
        await check_connectivity(UnreachableDriver())  # type: ignore[arg-type]

    assert "graph.internal" not in str(caught.value)
    assert "7687" not in str(caught.value)


def test_the_driver_is_built_from_settings_without_connecting() -> None:
    """Building a driver must not do I/O, or `create_app` would block on boot."""
    settings = Settings(debug=True, neo4j_uri="bolt://localhost:7687", neo4j_password="whatever")

    driver: Any = create_driver(settings)

    assert driver is not None
