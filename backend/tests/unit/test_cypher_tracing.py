"""Every statement the graph is sent, when somebody asks to see them.

Off by default and behind `EA_LOG_CYPHER` (docs/adr/0021): this is one line per
query, and a single catalogue page issues several. It is the trace worth having
when a traversal answers something surprising, and pure noise the rest of the
time.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from ea.core.logging import CYPHER_LOGGER
from ea.repositories.archimate_graph import Neo4jArchitectureRepository


class RecordingDriver:
    """Answers every query with nothing, and remembers it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def execute_query(self, query: str, parameters: Any, **_: Any) -> Any:
        self.asked.append(query)
        return SimpleNamespace(records=[])


def traces(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == CYPHER_LOGGER]


@pytest.mark.asyncio
class TestTheCypherTrace:
    async def test_a_query_says_what_it_ran_and_how_long_it_took(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repository = Neo4jArchitectureRepository(RecordingDriver(), database="neo4j")  # type: ignore[arg-type]

        with caplog.at_level(logging.DEBUG, logger=CYPHER_LOGGER):
            await repository.get_element(uuid4())

        (line,) = traces(caplog)
        assert "MATCH" in line.cypher  # type: ignore[attr-defined]
        assert line.duration_ms >= 0  # type: ignore[attr-defined]
        assert line.records == 0  # type: ignore[attr-defined]

    async def test_the_parameters_are_traced_because_they_are_half_the_query(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        element_id = uuid4()
        repository = Neo4jArchitectureRepository(RecordingDriver(), database="neo4j")  # type: ignore[arg-type]

        with caplog.at_level(logging.DEBUG, logger=CYPHER_LOGGER):
            await repository.get_element(element_id)

        (line,) = traces(caplog)
        assert line.parameters == {"id": str(element_id)}  # type: ignore[attr-defined]

    async def test_it_says_nothing_at_all_until_it_is_switched_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repository = Neo4jArchitectureRepository(RecordingDriver(), database="neo4j")  # type: ignore[arg-type]

        with caplog.at_level(logging.INFO):
            await repository.get_element(uuid4())

        assert traces(caplog) == []
