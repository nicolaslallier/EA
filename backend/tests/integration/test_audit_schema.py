"""The audit table as the server holds it: append-only, and born with a history."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0011_audit_events.py"

INSERT_EVENT = text(
    "INSERT INTO audit_events (id, at, actor_sub, actor_name, action, entity_kind, "
    "request_id, channel) VALUES (:id, :at, 'a', 'a', 'element.created', 'element', '-', 'http')"
)


async def test_a_stored_event_cannot_be_changed_removed_or_truncated(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        await connection.execute(INSERT_EVENT, {"id": uuid4(), "at": AT})

    for statement in (
        "UPDATE audit_events SET actor_name = 'someone else'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                # Static statements, no runtime value: the three ways to erase a trace.
                await connection.execute(text(statement))
        assert "append-only" in str(raised.value)

    async with engine_at_head.connect() as connection:
        remaining = (
            await connection.execute(text("SELECT count(*) FROM audit_events"))
        ).scalar_one()
    assert remaining == 1


async def test_the_migration_gives_an_existing_catalogue_a_first_line_of_history(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    element_id, other_id, link_id = uuid4(), uuid4(), uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0010")
    try:
        async with postgres_engine.begin() as connection:
            for identifier, name in ((element_id, "Billing"), (other_id, "db-01")):
                await connection.execute(
                    text(
                        "INSERT INTO elements (id, element_type, layer, aspect, name, "
                        "created_at, updated_at) "
                        "VALUES (:id, :type, :layer, :aspect, :name, :at, :at)"
                    ),
                    {
                        "id": identifier,
                        "type": E.NODE.value,
                        "layer": E.NODE.layer.value,
                        "aspect": E.NODE.aspect.value,
                        "name": name,
                        "at": AT,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO relationships (id, relationship_type, source_id, target_id, "
                    "source_type, target_type, created_at) VALUES "
                    "(:id, 'association', :s, :t, 'node', 'node', :at)"
                ),
                {"id": link_id, "s": element_id, "t": other_id, "at": AT},
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0011")

        select_all = text(
            "SELECT action, entity_id, actor_sub, correlation, at, after "
            "FROM audit_events ORDER BY action, entity_id"
        )
        async with postgres_engine.connect() as connection:
            rows = (await connection.execute(select_all)).all()
        assert [row[0] for row in rows] == ["element.created"] * 2 + ["relationship.created"]
        assert {row[1] for row in rows} == {element_id, other_id, link_id}
        assert {row[2] for row in rows} == {"ea-system"}
        assert {row[3] for row in rows} == {"backfill"}
        assert {row[4] for row in rows} == {AT}
        assert {row[5]["name"] for row in rows if row[0] == "element.created"} == {
            "Billing",
            "db-01",
        }
        assert all("package_id" in row[5] for row in rows if row[0] == "element.created")

        # Replaying the backfill statements doubles nothing.
        spec = importlib.util.spec_from_file_location("audit_migration", MIGRATION)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        async with postgres_engine.begin() as connection:
            await connection.execute(text(module._BACKFILL_ELEMENTS))
            await connection.execute(text(module._BACKFILL_RELATIONSHIPS))
        async with postgres_engine.connect() as connection:
            assert len((await connection.execute(select_all)).all()) == 3

        await asyncio.to_thread(command.downgrade, alembic_config, "0010")
        async with postgres_engine.connect() as connection:
            exists = (
                await connection.execute(text("SELECT to_regclass('audit_events')"))
            ).scalar_one()
            function = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_proc "
                        "WHERE proname = 'audit_events_are_append_only'"
                    )
                )
            ).scalar_one()
            kept = (await connection.execute(text("SELECT count(*) FROM elements"))).scalar_one()
        assert exists is None
        assert function == 0
        assert kept == 2

        # Up again from the populated base: the history is rebuilt, once.
        await asyncio.to_thread(command.upgrade, alembic_config, "0011")
        async with postgres_engine.connect() as connection:
            assert len((await connection.execute(select_all)).all()) == 3
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
