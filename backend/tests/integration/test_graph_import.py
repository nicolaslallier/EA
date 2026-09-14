"""The one-off copy, against a real PostgreSQL — docs/adr/0033."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ea import graph_import
from ea.db.models.architecture import ElementRecord
from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.graph_import import GraphImportError, copy_graph
from ea.repositories.architecture_store import PostgresArchitectureRepository, element_row

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

WHEN = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)


def a_small_graph() -> tuple[list[Element], list[Relationship]]:
    app = Element.create(element_type=E.APPLICATION_COMPONENT, name="Billing", now=WHEN)
    host = Element.create(element_type=E.NODE, name="srv-01", properties={"rack": "12"}, now=WHEN)
    return [app, host], [Relationship.between(R.ASSOCIATION, app, host, now=WHEN)]


async def test_the_copy_keeps_ids_and_passes_its_own_verification(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    elements, relationships = a_small_graph()

    await copy_graph(sessions, elements, relationships)

    repository = PostgresArchitectureRepository(sessions)
    assert await repository.get_element(elements[1].id) == elements[1]
    assert await repository.get_relationship(relationships[0].id) == relationships[0]


async def test_a_second_copy_is_refused(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    elements, relationships = a_small_graph()
    await copy_graph(sessions, elements, relationships)

    with pytest.raises(GraphImportError, match="imported before"):
        await copy_graph(sessions, elements, relationships)


async def test_a_copy_that_does_not_match_what_was_read_is_rolled_back(
    engine_at_head: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ids are checked before the commit, so a mismatch leaves nothing behind."""

    def a_row_that_loses_its_id(element: Any) -> dict[str, Any]:
        return {**element_row(element), "id": uuid4()}

    monkeypatch.setattr(graph_import, "element_row", a_row_that_loses_its_id)
    sessions = create_session_factory(engine_at_head)
    elements, _ = a_small_graph()

    with pytest.raises(GraphImportError, match="2 read but not stored"):
        await copy_graph(sessions, elements, [])

    async with sessions() as session:
        stored = (
            await session.execute(select(func.count()).select_from(ElementRecord))
        ).scalar_one()
    assert stored == 0
