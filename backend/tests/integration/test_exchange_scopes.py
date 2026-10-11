"""Export scopes against a real PostgreSQL: a package subtree, a saved query, whole views."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.exchange import ExportScope, ScopeKind
from ea.domain.query import ElementQuery
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from tests.integration.exchange_support import file_element, make_package, make_view

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_a_package_scope_keeps_elements_links_packages_and_whole_views_inside(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresExchangeRepository(sessions)
    top, other = await make_package(sessions, "Top"), await make_package(sessions, "Other")
    sub = await make_package(sessions, "Sub", top)
    a = await graph_service.create_element(element_type=E.NODE, name="a")
    b = await graph_service.create_element(element_type=E.NODE, name="b")
    c = await graph_service.create_element(element_type=E.NODE, name="c")
    for element, package in ((a, top), (b, sub), (c, other)):
        await file_element(sessions, element.id, package)
    await graph_service.connect(relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id)
    await graph_service.connect(relationship_type=R.ASSOCIATION, source_id=a.id, target_id=c.id)
    await make_view(sessions, "inside", [a.id, b.id])
    await make_view(sessions, "straddling", [a.id, c.id])
    scope = ExportScope(ScopeKind.PACKAGE, package_id=top, package_ids=frozenset({top, sub}))

    async with store.snapshot(scope) as snapshot:
        assert [e.name for e in await snapshot.elements_page(0, 10)] == ["a", "b"]
        assert len(await snapshot.relationships_page(0, 10)) == 1
        assert [v.name for v in await snapshot.views_page(0, 10)] == ["inside"]
        assert {p.name for p in await snapshot.packages()} == {"Top", "Sub"}
    assert await store.count_skipped_views(scope) == 1


async def test_a_package_scope_without_its_subpackages(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresExchangeRepository(sessions)
    top = await make_package(sessions, "Top")
    sub = await make_package(sessions, "Sub", top)
    a = await graph_service.create_element(element_type=E.NODE, name="a")
    b = await graph_service.create_element(element_type=E.NODE, name="b")
    await file_element(sessions, a.id, top)
    await file_element(sessions, b.id, sub)

    async with store.snapshot(
        ExportScope(ScopeKind.PACKAGE, package_id=top, package_ids=frozenset({top}))
    ) as snapshot:
        assert [e.name for e in await snapshot.elements_page(0, 10)] == ["a"]


async def test_a_query_scope_applies_the_saved_filter_and_keeps_the_whole_tree(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresExchangeRepository(sessions)
    await make_package(sessions, "Skeleton")
    srv = await graph_service.create_element(element_type=E.NODE, name="srv")
    alice = await graph_service.create_element(element_type=E.BUSINESS_ACTOR, name="Alice")
    other = await graph_service.create_element(element_type=E.NODE, name="other")
    await graph_service.connect(
        relationship_type=R.ASSOCIATION, source_id=srv.id, target_id=alice.id
    )
    await graph_service.connect(
        relationship_type=R.ASSOCIATION, source_id=srv.id, target_id=other.id
    )
    await make_view(sessions, "nodes only", [srv.id, other.id])
    await make_view(sessions, "with actor", [srv.id, alice.id])
    scope = ExportScope(ScopeKind.QUERY, query=ElementQuery(element_types=(E.NODE,)))

    async with store.snapshot(scope) as snapshot:
        assert [e.name for e in await snapshot.elements_page(0, 10)] == ["other", "srv"]
        assert len(await snapshot.relationships_page(0, 10)) == 1  # srv-other, never srv-alice
        assert [v.name for v in await snapshot.views_page(0, 10)] == ["nodes only"]
        assert [p.name for p in await snapshot.packages()] == ["Skeleton"]
    assert await store.count_skipped_views(scope) == 1


async def test_the_whole_model_skips_nothing_and_filters_nothing(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresExchangeRepository(sessions)
    a = await graph_service.create_element(element_type=E.NODE, name="a")
    await make_view(sessions, "v", [a.id])

    async with store.snapshot(ExportScope()) as snapshot:
        assert len(await snapshot.views_page(0, 10)) == 1
    assert await store.count_skipped_views(ExportScope()) == 0
