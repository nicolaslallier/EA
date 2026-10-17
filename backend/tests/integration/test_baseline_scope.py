"""A baseline of one subtree copies that subtree and nothing else (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.domain.baselines import Baseline, BaselineLimits, refuse_oversize
from ea.domain.errors import BaselineScopedError, BaselineTooLargeError, PackageNotFoundError
from ea.repositories.baseline_store import PostgresBaselineRepository, capture_scope, scope_size
from ea.services.baselines import BaselineService
from tests.conftest import never_frozen
from tests.integration.test_package_freeze_triggers import (
    AT,
    a_diagram,
    a_package,
    an_element,
    insert,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def count(engine: AsyncEngine, table: str, baseline_id: UUID) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE baseline_id = :b"),  # nosec B608
                    {"b": baseline_id},
                )
            ).scalar_one()
        )


async def test_only_the_subtree_its_links_and_its_diagrams_are_copied(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        a = await an_element(connection, "a", root)
        b = await an_element(connection, "b", child)
        outside = await an_element(connection, "outside", None)
        for source, target in ((a, b), (a, outside)):
            await insert(
                connection,
                "relationships",
                id=uuid4(),
                relationship_type="association",
                source_id=source,
                target_id=target,
                source_type="node",
                target_type="node",
                created_at=AT,
            )
        diagram = await a_diagram(connection, "Vue", child)
        await insert(connection, "diagram_nodes", diagram_id=diagram, element_id=a, x=0, y=0)
        free = await a_diagram(connection, "Libre", None)
        await insert(connection, "diagram_nodes", diagram_id=free, element_id=outside, x=0, y=0)

    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    baseline = Baseline.create(
        name="Finance v1.0.0",
        description="",
        created_by_sub="s",
        created_by="n",
        now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    async with sessions.begin() as session:
        size = await scope_size(session, frozenset({root, child}))
        stored = await capture_scope(
            session, baseline, frozenset({root, child}), package_id=root, path="Finance"
        )

    assert (size.elements, size.relationships, size.boxes, size.diagrams) == (2, 1, 1, 1)
    assert (stored.element_count, stored.relationship_count, stored.box_count) == (2, 1, 1)
    assert (stored.scope_package_id, stored.scope_path) == (root, "Finance")
    assert await count(engine_at_head, "baseline_elements", baseline.id) == 2
    assert await count(engine_at_head, "baseline_relationships", baseline.id) == 1
    assert await count(engine_at_head, "baseline_boxes", baseline.id) == 1


async def test_an_empty_subtree_gives_an_empty_baseline(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Vide", None)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    baseline = Baseline.create(
        name="Vide v1.0.0",
        description="",
        created_by_sub="s",
        created_by="n",
        now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    async with sessions.begin() as session:
        stored = await capture_scope(
            session, baseline, frozenset({root}), package_id=root, path="Vide"
        )
    assert (stored.element_count, stored.relationship_count, stored.box_count) == (0, 0, 0)


async def test_the_bytes_of_a_subtree_are_measured(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await an_element(connection, "a", root)
        await an_element(connection, "outside", None)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    async with sessions.begin() as session:
        size = await scope_size(session, frozenset({root}))
        empty = await scope_size(session, frozenset({uuid4()}))

    assert size.bytes > 0
    assert empty.bytes == 0
    refuse_oversize(size, BaselineLimits())
    with pytest.raises(BaselineTooLargeError):
        refuse_oversize(size, BaselineLimits(bytes=1))


def a_service(engine: AsyncEngine) -> BaselineService:
    """Nothing is published in these tests: the freeze lookup answers `None`."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return BaselineService(PostgresBaselineRepository(sessions), frozen_by=never_frozen)


async def run(engine: AsyncEngine, statement: str, **values: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(statement), values)


async def exists(engine: AsyncEngine, table: str, row_id: UUID) -> bool:
    async with engine.connect() as connection:
        found = await connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE id = :i"),  # nosec B608 - test constant
            {"i": row_id},
        )
        return bool(found.scalar_one())


async def package_of(engine: AsyncEngine, element_id: UUID) -> UUID | None:
    async with engine.connect() as connection:
        found = await connection.execute(
            text("SELECT package_id FROM elements WHERE id = :i"), {"i": element_id}
        )
        return cast("UUID | None", found.scalar_one())


async def a_link(engine: AsyncEngine, source: UUID, target: UUID) -> UUID:
    link = uuid4()
    async with engine.begin() as connection:
        await insert(
            connection,
            "relationships",
            id=link,
            relationship_type="association",
            source_id=source,
            target_id=target,
            source_type="node",
            target_type="node",
            created_at=AT,
        )
    return link


async def finance_with(engine: AsyncEngine, *names: str) -> tuple[UUID, list[UUID]]:
    async with engine.begin() as connection:
        finance = await a_package(connection, "Finance", None)
        return finance, [await an_element(connection, name, finance) for name in names]


async def test_an_element_moved_out_since_is_modified_not_removed(
    engine_at_head: AsyncEngine,
) -> None:
    finance, (_, moved) = await finance_with(engine_at_head, "a", "moved")
    service = a_service(engine_at_head)
    scoped_id = (await service.create(name="Finance", package_id=finance)).id
    await run(engine_at_head, "UPDATE elements SET package_id = NULL WHERE id = :i", i=moved)

    diff = await service.diff(scoped_id)

    assert [m.id for m in diff.elements_modified] == [moved]
    assert diff.elements_removed == ()


async def test_a_scoped_restore_never_deletes_outside_its_subtree(
    engine_at_head: AsyncEngine,
) -> None:
    finance, (inside,) = await finance_with(engine_at_head, "a")
    service = a_service(engine_at_head)
    scoped_id = (await service.create(name="Finance", package_id=finance)).id
    async with engine_at_head.begin() as connection:
        created_inside = await an_element(connection, "new inside", finance)
        created_at_root = await an_element(connection, "new at the root", None)
        other_root = await an_element(connection, "other at the root", None)
    root_link = await a_link(engine_at_head, created_at_root, other_root)
    boundary_link = await a_link(engine_at_head, inside, created_at_root)

    outcome = await service.restore(scoped_id)
    assert {e.id for e in outcome.plan.elements_to_delete} == {created_inside}
    assert outcome.plan.relationships_to_delete == ()
    await service.restore(scoped_id, dry_run=False, expected_digest=outcome.digest)

    assert not await exists(engine_at_head, "elements", created_inside)
    for table, kept in (
        ("elements", created_at_root),
        ("elements", other_root),
        ("elements", inside),
        ("relationships", root_link),
        ("relationships", boundary_link),
    ):
        assert await exists(engine_at_head, table, kept), (table, kept)


async def test_a_scoped_restore_puts_a_moved_out_element_back(
    engine_at_head: AsyncEngine,
) -> None:
    finance, (moved,) = await finance_with(engine_at_head, "moved")
    service = a_service(engine_at_head)
    scoped_id = (await service.create(name="Finance", package_id=finance)).id
    await run(engine_at_head, "UPDATE elements SET package_id = NULL WHERE id = :i", i=moved)

    outcome = await service.restore(scoped_id)
    assert outcome.plan.elements_to_delete == ()
    await service.restore(scoped_id, dry_run=False, expected_digest=outcome.digest)

    assert await package_of(engine_at_head, moved) == finance


async def test_a_baseline_whose_package_is_gone_is_diffed_but_not_restored(
    engine_at_head: AsyncEngine,
) -> None:
    finance, (inside,) = await finance_with(engine_at_head, "a")
    service = a_service(engine_at_head)
    scoped_id = (await service.create(name="Finance", package_id=finance)).id
    # Deleted with move_contents_to=root: its element goes to the root first.
    await run(engine_at_head, "UPDATE elements SET package_id = NULL WHERE id = :i", i=inside)
    await run(engine_at_head, "DELETE FROM packages WHERE id = :p", p=finance)

    diff = await service.diff(scoped_id)
    assert [m.id for m in diff.elements_modified] == [inside]
    with pytest.raises(BaselineScopedError):
        await service.restore(scoped_id)


def _a_baseline(name: str) -> Baseline:
    return Baseline.create(
        name=name,
        description="",
        created_by_sub="s",
        created_by="n",
        now=datetime(2026, 10, 12, tzinfo=UTC),
    )


def _no_audit(_stored: Baseline) -> tuple[()]:
    return ()


async def _count_rows(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(text(f"SELECT count(*) FROM {table}"))  # nosec B608
            ).scalar_one()
        )


async def _finance_and_budget(engine: AsyncEngine) -> UUID:
    async with engine.begin() as connection:
        finance = await a_package(connection, "Finance", None)
        budget = await a_package(connection, "Budget", finance)
        await an_element(connection, "a", finance)
        await an_element(connection, "b", budget)
        await an_element(connection, "outside", None)
    return finance


async def test_the_repository_captures_a_subtree_by_its_package(
    engine_at_head: AsyncEngine,
) -> None:
    finance = await _finance_and_budget(engine_at_head)
    repository = PostgresBaselineRepository(async_sessionmaker(engine_at_head))

    stored = await repository.create(_a_baseline("Finance"), audit=_no_audit, scope=finance)

    assert (stored.scope_package_id, stored.scope_path, stored.element_count) == (
        finance,
        "Finance",
        2,
    )


async def test_an_unknown_package_is_a_404_and_copies_nothing(
    engine_at_head: AsyncEngine,
) -> None:
    repository = PostgresBaselineRepository(async_sessionmaker(engine_at_head))

    with pytest.raises(PackageNotFoundError):
        await repository.create(_a_baseline("x"), audit=_no_audit, scope=uuid4())

    assert await _count_rows(engine_at_head, "baselines") == 0


async def test_a_subtree_past_the_ceiling_is_refused_before_it_is_copied(
    engine_at_head: AsyncEngine,
) -> None:
    finance = await _finance_and_budget(engine_at_head)
    small = PostgresBaselineRepository(
        async_sessionmaker(engine_at_head), limits=BaselineLimits(elements=1)
    )

    with pytest.raises(BaselineTooLargeError):
        await small.create(_a_baseline("Finance"), audit=_no_audit, scope=finance)

    assert await _count_rows(engine_at_head, "baseline_elements") == 0
