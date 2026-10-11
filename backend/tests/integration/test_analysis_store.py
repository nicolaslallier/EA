"""The three reads behind the matrix and the quality checks, on a real PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.analytics import Dimension, Metric, group_elements, summarise
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


DIRTY = [
    ("A", {"cost": "1 200,50", "risk": "Élevé", "lifecycle": "en production"}),
    ("B", {"cost": "300", "risk": "faible", "lifecycle": "en production"}),
    ("C", {"cost": "1 200 €", "risk": "élevé"}),
    ("D", {}),
    ("E", {"cost": "   ", "risk": "inconnu", "lifecycle": "retiré"}),
    ("F", {"cost": "1 000", "risk": "Faible"}),  # noqa: RUF001
]


async def dirty_model(service: ArchitectureService):  # type: ignore[no-untyped-def]
    return [
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name=name, properties=props
        )
        for name, props in DIRTY
    ]


@pytest.mark.parametrize("metric", [Metric.COUNT, Metric.SUM, Metric.AVG, Metric.MIN, Metric.MAX])
async def test_the_sql_aggregate_is_the_pure_one_on_dirty_values(
    graph_service: ArchitectureService,
    graph_repository: PostgresArchitectureRepository,
    metric: Metric,
) -> None:
    elements = await dirty_model(graph_service)
    key = None if metric is Metric.COUNT else "cost"
    for text in ("property:risk", "layer", "element_type"):
        dims = (Dimension.parse(text),)

        from_sql = summarise(await graph_repository.grouped(dims, key), dims, metric, key)
        from_python = summarise(group_elements(elements, dims, key), dims, metric, key)

        assert from_sql == from_python, text


async def test_two_dimensions_agree_too(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

    from_sql = summarise(await graph_repository.grouped(dims, "cost"), dims, Metric.SUM, "cost")
    from_python = summarise(group_elements(elements, dims, "cost"), dims, Metric.SUM, "cost")

    assert from_sql == from_python


async def test_unreadable_costs_are_counted_not_cast(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    """`'1 200 €'::numeric` would abort the query: the CASE keeps the cast behind the regex."""
    await dirty_model(graph_service)
    dims = (Dimension.parse("layer"),)

    result = summarise(await graph_repository.grouped(dims, "cost"), dims, Metric.SUM, "cost")

    assert result.unreadable == 1
    assert result.buckets[0].value == 1200.5 + 300 + 1000


async def test_a_scope_narrows_the_groups(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    dims = (Dimension.parse("property:risk"),)

    inside = await graph_repository.grouped(dims, None, scope=[elements[1].id, elements[5].id])

    assert sum(group.count for group in inside) == 2


async def test_a_key_is_a_bound_parameter_never_spliced_into_the_statement(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    elements = await dirty_model(graph_service)
    hostile = "x'; DROP TABLE elements; --"

    groups = await graph_repository.grouped((Dimension.parse("layer"),), hostile)

    assert sum(group.count for group in groups) == len(DIRTY)
    assert (
        await graph_repository.get_element(elements[0].id) is not None
    )  # the table is still there


async def test_an_absurd_number_is_unreadable_in_sql_as_in_python(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    """Cast behind a bounded pattern: 131 072 digits must not reach `numeric`'s overflow."""
    elements = [
        await graph_service.create_element(
            element_type=E.NODE, name=name, properties={"cost": cost}
        )
        for name, cost in (("big", "9" * 131_072), ("huge", "9" * 400), ("fine", "5"))
    ]
    dims = (Dimension.parse("layer"),)

    from_sql = summarise(await graph_repository.grouped(dims, "cost"), dims, Metric.SUM, "cost")
    from_python = summarise(group_elements(elements, dims, "cost"), dims, Metric.SUM, "cost")

    assert from_sql == from_python
    assert (from_sql.buckets[0].value, from_sql.unreadable) == (5.0, 2)
