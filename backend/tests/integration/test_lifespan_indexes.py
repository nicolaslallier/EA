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


async def test_0014_survives_thousands_of_hostile_rows_and_indexes_them(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """No CAST, no refusal, one pass: hostile values neither fail nor stall the boot."""
    hostile = [
        "9" * 100_000,
        "infinity",
        "-infinity",
        "NaN",
        "+2027-01-01",
        "-2027-01-01",
        "0000-01-01",
        "9999-12-31",
        "10000-01-01",
        "\uff12\uff10\uff12\uff17-\uff10\uff11-\uff10\uff11",
        "2027\N{NO-BREAK SPACE}01-01",
        "\t",
        "' OR 1=1 --",
        "2027-02-30",
    ]
    await asyncio.to_thread(command.upgrade, alembic_config, "0013")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                # Test setup: everything is bound; thousands of rows in one statement.
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                    "created_at, updated_at) SELECT gen_random_uuid(), 'node', 'technology', "
                    "'active_structure', 'n' || g, jsonb_build_object('valid_from', "
                    "CASE WHEN g % 3 = 0 THEN '2027-01-0' || (g % 9 + 1) "
                    "WHEN g % 3 = 1 THEN 'junk' || g ELSE ' 2027-02-01 ' END, "
                    "'valid_until', CAST(:hostile AS jsonb) ->> (g % :n)::int), now(), now() "
                    "FROM generate_series(1, 5000) AS g"
                ),
                {"hostile": json.dumps(hostile), "n": len(hostile)},
            )
        await asyncio.to_thread(command.upgrade, alembic_config, "0014")

        async with postgres_engine.connect() as connection:
            unreadable = await connection.execute(
                text(
                    "SELECT count(*) FROM elements WHERE "
                    "(properties ->> 'valid_from') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
                    "OR (properties ->> 'valid_until') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"
                )
            )
            assert unreadable.scalar_one() == 0
            kept = await connection.execute(
                text("SELECT count(*) FROM elements WHERE properties ? 'legacy_valid_until'")
            )
            assert kept.scalar_one() > 0
        assert await _index_names(postgres_engine) >= INDEXES

        await asyncio.to_thread(command.downgrade, alembic_config, "0013")
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
        await asyncio.to_thread(command.upgrade, alembic_config, "head")
        await asyncio.to_thread(command.downgrade, alembic_config, "0013")
        await asyncio.to_thread(command.upgrade, alembic_config, "0014")
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


async def test_0014_never_overwrites_a_legacy_key_that_is_already_there(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    taken = {
        "legacy_valid_from": "keep me",
        **{f"legacy_valid_from_{n}": f"kept {n}" for n in range(2, 10)},
    }
    await asyncio.to_thread(command.upgrade, alembic_config, "0013")
    try:
        await _insert_element(
            postgres_engine, "one", {"valid_from": "not a date", "legacy_valid_from": "keep me"}
        )
        await _insert_element(
            postgres_engine,
            "long",
            {"valid_until": "9" * 40, "legacy_valid_until": "old", "legacy_valid_until_2": "older"},
        )
        await _insert_element(postgres_engine, "full", {"valid_from": "x", **taken})

        await asyncio.to_thread(command.upgrade, alembic_config, "0014")

        assert await _properties_of(postgres_engine, "one") == {
            "legacy_valid_from": "keep me",
            "legacy_valid_from_2": "not a date",
        }
        assert await _properties_of(postgres_engine, "long") == {
            "legacy_valid_until": "old",
            "legacy_valid_until_2": "older",
            "legacy_valid_until_3": "9" * 40,
        }
        full = await _properties_of(postgres_engine, "full")
        assert full["legacy_valid_from"] == "keep me"
        assert full["legacy_valid_from_9"] == "kept 9\nx"  # every suffix taken: appended, not lost
        assert "valid_from" not in full

        await asyncio.to_thread(command.downgrade, alembic_config, "0013")
        await asyncio.to_thread(command.upgrade, alembic_config, "0014")
        assert (await _properties_of(postgres_engine, "one"))["legacy_valid_from"] == "keep me"
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
