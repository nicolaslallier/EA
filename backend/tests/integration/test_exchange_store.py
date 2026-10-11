"""The export snapshot against a real PostgreSQL: order, paging and isolation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.exchange import ExportScope
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


@pytest.fixture
def exchange(engine_at_head: AsyncEngine) -> PostgresExchangeRepository:
    return PostgresExchangeRepository(create_session_factory(engine_at_head))


async def test_elements_come_ordered_by_type_then_name(
    exchange: PostgresExchangeRepository, graph_service: ArchitectureService
) -> None:
    await graph_service.create_element(element_type=E.NODE, name="Zeta")
    await graph_service.create_element(element_type=E.BUSINESS_ACTOR, name="Bob")
    await graph_service.create_element(element_type=E.BUSINESS_ACTOR, name="Alice")

    async with exchange.snapshot(ExportScope()) as snapshot:
        page = await snapshot.elements_page(0, 10)

    assert [(e.element_type, e.name) for e in page] == [
        (E.BUSINESS_ACTOR, "Alice"),
        (E.BUSINESS_ACTOR, "Bob"),
        (E.NODE, "Zeta"),
    ]


async def test_pages_follow_one_another_without_overlap(
    exchange: PostgresExchangeRepository, graph_service: ArchitectureService
) -> None:
    for index in range(5):
        await graph_service.create_element(element_type=E.NODE, name=f"n{index}")

    async with exchange.snapshot(ExportScope()) as snapshot:
        first = await snapshot.elements_page(0, 3)
        second = await snapshot.elements_page(3, 3)

    assert [e.name for e in (*first, *second)] == ["n0", "n1", "n2", "n3", "n4"]


async def test_relationships_are_ordered_by_source_then_target_then_type(
    exchange: PostgresExchangeRepository, graph_service: ArchitectureService
) -> None:
    a = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="A")
    b = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="B")
    c = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="C")
    await graph_service.connect(relationship_type=R.FLOW, source_id=b.id, target_id=c.id)
    await graph_service.connect(relationship_type=R.SERVING, source_id=a.id, target_id=c.id)
    await graph_service.connect(relationship_type=R.FLOW, source_id=a.id, target_id=b.id)

    async with exchange.snapshot(ExportScope()) as snapshot:
        links = await snapshot.relationships_page(0, 10)

    assert [(link.source_id, link.target_id) for link in links] == [
        (a.id, b.id),
        (a.id, c.id),
        (b.id, c.id),
    ]


async def test_a_write_committed_after_the_snapshot_began_is_invisible_to_it(
    exchange: PostgresExchangeRepository, graph_service: ArchitectureService
) -> None:
    await graph_service.create_element(element_type=E.NODE, name="before")

    async with exchange.snapshot(ExportScope()) as snapshot:
        assert len(await snapshot.elements_page(0, 10)) == 1
        await graph_service.create_element(element_type=E.NODE, name="after")
        assert len(await snapshot.elements_page(0, 10)) == 1
