"""The baseline tables as the server holds them (docs/adr/0045)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

NEW_BASELINE = text(
    "INSERT INTO baselines (id, name, created_by_sub, created_by, created_at) "
    "VALUES (:id, :name, 's', 'n', :at)"
)


async def test_a_baseline_name_is_unique_and_its_copy_goes_with_it(
    engine_at_head: AsyncEngine,
) -> None:
    baseline_id, element_id = uuid4(), uuid4()
    async with engine_at_head.begin() as connection:
        await connection.execute(NEW_BASELINE, {"id": baseline_id, "name": "v1", "at": AT})
        await connection.execute(
            text(
                "INSERT INTO baseline_elements (baseline_id, id, element_type, layer, aspect, "
                "name, created_at, updated_at) "
                "VALUES (:b, :e, 'node', 'technology', 'active_structure', 'n', :at, :at)"
            ),
            {"b": baseline_id, "e": element_id, "at": AT},
        )

    with pytest.raises(IntegrityError) as raised:
        async with engine_at_head.begin() as connection:
            await connection.execute(NEW_BASELINE, {"id": uuid4(), "name": "v1", "at": AT})
    assert getattr(raised.value.orig.__cause__, "constraint_name", None) == "uq_baselines_name"  # type: ignore[union-attr]

    async with engine_at_head.begin() as connection:
        await connection.execute(text("DELETE FROM baselines"))
    async with engine_at_head.connect() as connection:
        remaining = (
            await connection.execute(text("SELECT count(*) FROM baseline_elements"))
        ).scalar_one()
    assert remaining == 0


async def test_a_baseline_keeps_no_foreign_key_to_the_live_model(
    engine_at_head: AsyncEngine,
) -> None:
    """The copy must outlive the elements, the packages and the diagrams it remembers."""
    async with engine_at_head.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid::regclass::text LIKE 'baseline%'"
            )
        )
    assert {row[0] for row in rows} == {
        "fk_baseline_elements_baseline_id_baselines",
        "fk_baseline_relationships_baseline_id_baselines",
        "fk_baseline_boxes_baseline_id_baselines",
    }


async def test_the_migration_climbs_and_descends_on_a_populated_catalogue(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    element_id = uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0011")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, "
                    "created_at, updated_at) "
                    "VALUES (:id, 'node', 'technology', 'active_structure', 'db-01', :at, :at)"
                ),
                {"id": element_id, "at": AT},
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0012")
        async with postgres_engine.begin() as connection:
            await connection.execute(NEW_BASELINE, {"id": uuid4(), "name": "v1", "at": AT})
        async with postgres_engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT count(*) FROM elements"))
            ).scalar_one() == 1

        await asyncio.to_thread(command.downgrade, alembic_config, "0011")
        async with postgres_engine.connect() as connection:
            gone = (await connection.execute(text("SELECT to_regclass('baselines')"))).scalar_one()
            kept = (await connection.execute(text("SELECT count(*) FROM elements"))).scalar_one()
        assert gone is None and kept == 1

        await asyncio.to_thread(command.upgrade, alembic_config, "0012")
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
