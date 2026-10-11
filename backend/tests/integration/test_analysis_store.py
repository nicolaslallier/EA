"""The three reads behind the matrix and the quality checks, on a real PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def a_small_graph(service: ArchitectureService):  # type: ignore[no-untyped-def]
    api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order to cash")
    loner = await service.create_element(element_type=E.NODE, name="Loner")
    link = await service.connect(
        relationship_type=R.SERVING, source_id=api.id, target_id=process.id
    )
    return api, process, loner, link


async def test_elements_come_back_by_name_and_absent_ids_are_missing(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, _, _ = await a_small_graph(graph_service)

    found = await graph_repository.elements_by_ids([process.id, api.id, uuid4()])

    assert [element.name for element in found] == ["Invoice API", "Order to cash"]
    assert await graph_repository.elements_by_ids([]) == ()


async def test_links_between_keeps_the_direction(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, _, link = await a_small_graph(graph_service)

    forward = await graph_repository.links_between([api.id], [process.id])
    backward = await graph_repository.links_between([process.id], [api.id])

    assert [found.id for found in forward] == [link.id]
    assert backward == ()
    assert await graph_repository.links_between([], [process.id]) == ()


async def test_links_touching_finds_a_link_by_either_end(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api, process, loner, link = await a_small_graph(graph_service)

    assert [found.id for found in await graph_repository.links_touching([api.id])] == [link.id]
    assert [found.id for found in await graph_repository.links_touching([process.id])] == [link.id]
    assert await graph_repository.links_touching([loner.id]) == ()
