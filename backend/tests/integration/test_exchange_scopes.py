"""Export scopes against a real PostgreSQL: a package subtree, a saved query, whole views."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.exchange import ExportRequest, ExportScope, ScopeKind
from ea.domain.query import ElementQuery
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from tests.integration.exchange_support import file_element, make_package, make_view
from tests.integration.test_exchange_roundtrip import whole  # noqa: F401

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
        assert await snapshot.skipped_views() == 1


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
        assert await snapshot.skipped_views() == 1


async def test_the_whole_model_skips_nothing_and_filters_nothing(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresExchangeRepository(sessions)
    a = await graph_service.create_element(element_type=E.NODE, name="a")
    await make_view(sessions, "v", [a.id])

    async with store.snapshot(ExportScope()) as snapshot:
        assert len(await snapshot.views_page(0, 10)) == 1
        assert await snapshot.skipped_views() == 0


async def test_the_count_and_the_file_are_one_reading_of_the_database(
    whole: Any,  # noqa: F811
) -> None:
    """A view that straddles the scope is committed after the export began: neither the count
    nor the file knows it — they would disagree if the count were taken apart from the stream."""
    architecture, _, packages, exchange, (sessions, _) = whole
    top = (await packages.create(name="Top")).package.id
    other = (await packages.create(name="Other")).package.id
    a = await architecture.create_element(element_type=E.NODE, name="a")
    c = await architecture.create_element(element_type=E.NODE, name="c")
    await packages.move_elements([a.id], top)
    await packages.move_elements([c.id], other)
    await make_view(sessions, "inside", [a.id])
    await make_view(sessions, "straddling-before", [a.id, c.id])

    export = await exchange.export(
        ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=top))
    )
    await make_view(sessions, "straddling-late", [a.id, c.id])  # committed meanwhile
    text = b"".join([chunk async for chunk in export.chunks]).decode()

    assert export.skipped_views == 1
    assert "view-inside" in text
    assert "straddling" not in text  # neither the one counted nor the one that came late


async def test_a_saved_query_the_database_cannot_run_fails_before_any_header(
    whole: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import Integer, cast

    from ea.db.models.architecture import ElementRecord
    from ea.domain.errors import UnknownScopeError

    architecture, _, _, exchange, (_, queries) = whole
    await architecture.create_element(element_type=E.NODE, name="not a number")
    saved = await queries.create(name="q", query=ElementQuery())
    # A filter that parses and compiles, and fails when it meets a row: `cast(name as int)`.
    monkeypatch.setattr(
        "ea.repositories.exchange_store._scope_clauses",
        lambda scope: [cast(ElementRecord.name, Integer) > 0],
    )

    with pytest.raises(UnknownScopeError) as raised:
        await exchange.export(ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=saved.id)))

    assert "not a number" not in str(raised.value) and "integer" not in str(raised.value)
    # and the snapshot it had opened was given back: the next export runs
    monkeypatch.undo()
    export = await exchange.export(ExportRequest())
    assert b"not a number" in b"".join([chunk async for chunk in export.chunks])
