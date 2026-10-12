"""A baseline of one subtree copies that subtree and nothing else (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.domain.baselines import Baseline
from ea.repositories.baseline_store import capture_scope, scope_size
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
