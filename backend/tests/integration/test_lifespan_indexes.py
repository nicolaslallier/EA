"""Revision 0014: the validity indexes, and what becomes of dates written before the rule."""

from __future__ import annotations

import asyncio
import json

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

INDEXES = {
    "ix_elements_valid_from",
    "ix_elements_valid_until",
    "ix_relationships_valid_from",
    "ix_relationships_valid_until",
}


async def _index_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            # A catalogue read with no runtime value: nothing to bind.
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename IN ('elements', 'relationships')"
            )
        )
    return {row[0] for row in rows}


async def _insert_element(engine: AsyncEngine, name: str, properties: dict[str, object]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            # Test setup: the properties are bound, the rest is literal.
            text(
                "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                "'active_structure', :name, CAST(:properties AS jsonb), now(), now())"
            ),
            {"name": name, "properties": json.dumps(properties)},
        )


async def _properties_of(engine: AsyncEngine, name: str) -> dict[str, object]:
    async with engine.connect() as connection:
        row = await connection.execute(
            text("SELECT properties FROM elements WHERE name = :name"), {"name": name}
        )
        found = row.scalar_one()
    assert isinstance(found, dict)
    return found


async def test_head_carries_the_four_indexes(engine_at_head: AsyncEngine) -> None:
    assert await _index_names(engine_at_head) >= INDEXES


async def test_a_range_on_valid_from_can_be_served_by_its_index(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService
) -> None:
    await graph_service.create_element(
        element_type=E.PLATEAU, name="To-Be", properties={"valid_from": "2027-01-01"}
    )

    async with engine_at_head.begin() as connection:
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = await connection.execute(
            # Literal values only: EXPLAIN of the range the roadmap asks for.
            text(
                "EXPLAIN SELECT id FROM elements "
                "WHERE (properties ->> 'valid_from') <= '2027-06-01'"
            )
        )
        rendered = " ".join(row[0] for row in plan)

    assert "ix_elements_valid_from" in rendered


async def test_the_chain_goes_down_and_up_again_on_a_populated_base(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0013")
    try:
        await _insert_element(postgres_engine, "db-01", {"valid_from": "2026-01-01", "owner": "x"})
        await asyncio.to_thread(command.upgrade, alembic_config, "0014")
        assert await _index_names(postgres_engine) >= INDEXES
        assert await _properties_of(postgres_engine, "db-01") == {
            "valid_from": "2026-01-01",
            "owner": "x",
        }

        await asyncio.to_thread(command.downgrade, alembic_config, "0013")
        assert not INDEXES & await _index_names(postgres_engine)
        assert await _properties_of(postgres_engine, "db-01") == {
            "valid_from": "2026-01-01",
            "owner": "x",
        }
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_0014_canonicalises_removes_blanks_and_moves_what_is_not_a_day(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """The migration never refuses (a failure at boot is an outage) and never loses a value."""
    await asyncio.to_thread(command.upgrade, alembic_config, "0013")
    try:
        await _insert_element(postgres_engine, "spaced", {"valid_from": " 20260301 ", "k": "v"})
        await _insert_element(postgres_engine, "blank", {"valid_until": "  "})
        await _insert_element(postgres_engine, "partial", {"valid_until": "2027-06"})
        await _insert_element(postgres_engine, "number", {"valid_from": 2027})
        await _insert_element(postgres_engine, "long", {"valid_from": "2" * 5000})
        await _insert_element(postgres_engine, "fine", {"valid_until": "2027-12-31"})

        await asyncio.to_thread(command.upgrade, alembic_config, "0014")

        assert await _properties_of(postgres_engine, "spaced") == {
            "valid_from": "2026-03-01",
            "k": "v",
        }
        assert await _properties_of(postgres_engine, "blank") == {}
        assert await _properties_of(postgres_engine, "partial") == {"legacy_valid_until": "2027-06"}
        assert await _properties_of(postgres_engine, "number") == {"legacy_valid_from": "2027"}
        assert await _properties_of(postgres_engine, "long") == {"legacy_valid_from": "2" * 5000}
        assert await _properties_of(postgres_engine, "fine") == {"valid_until": "2027-12-31"}
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
