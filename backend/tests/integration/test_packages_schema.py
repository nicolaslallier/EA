"""Revision 0009 as the server holds it: sibling names, RESTRICT, and a populated base."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

ELEMENT = "00000000-0000-0000-0000-0000000000e1"
DIAGRAM = "00000000-0000-0000-0000-0000000000d1"


async def a_package(engine: AsyncEngine, name: str, parent: str | None = None) -> str:
    async with engine.begin() as connection:
        found = await connection.execute(
            text(
                "INSERT INTO packages (id, parent_id, name, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :parent, :name, now(), now()) RETURNING id"
            ),
            {"parent": parent, "name": name},
        )
        return str(found.scalar_one())


async def test_a_populated_base_upgrades_with_everything_at_the_root(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0008")
    try:
        async with postgres_engine.begin() as connection:
            # Test setup with literal values only: an element and a diagram that predate packages.
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    f"updated_at) VALUES ('{ELEMENT}', 'node', 'technology', 'active_structure', "
                    "'db-01', now(), now())"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO diagrams (id, name, created_at, updated_at) "
                    f"VALUES ('{DIAGRAM}', 'Vente', now(), now())"
                )
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0009")

        async with postgres_engine.connect() as connection:
            # Catalogue reads with no runtime value: nothing to bind.
            element = await connection.execute(text("SELECT package_id FROM elements"))
            diagram = await connection.execute(text("SELECT package_id FROM diagrams"))
        assert element.scalar_one() is None
        assert diagram.scalar_one() is None

        await asyncio.to_thread(command.downgrade, alembic_config, "0008")
        async with postgres_engine.connect() as connection:
            kept = await connection.execute(text("SELECT count(*) FROM elements"))
        assert kept.scalar_one() == 1
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_two_root_packages_cannot_differ_only_by_case(engine_at_head: AsyncEngine) -> None:
    await a_package(engine_at_head, "Finance")

    with pytest.raises(IntegrityError, match="uq_packages_root_name"):
        await a_package(engine_at_head, "finance")


async def test_two_siblings_cannot_differ_only_by_case(engine_at_head: AsyncEngine) -> None:
    parent = await a_package(engine_at_head, "Finance")
    await a_package(engine_at_head, "Budget", parent)

    with pytest.raises(IntegrityError, match="uq_packages_child_name"):
        await a_package(engine_at_head, "BUDGET", parent)


async def test_the_same_name_is_fine_under_another_parent_or_at_the_root(
    engine_at_head: AsyncEngine,
) -> None:
    finance = await a_package(engine_at_head, "Finance")
    rh = await a_package(engine_at_head, "RH")
    await a_package(engine_at_head, "Budget", finance)
    await a_package(engine_at_head, "Budget", rh)
    await a_package(engine_at_head, "Budget")


async def test_a_package_that_holds_an_element_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    package = await a_package(engine_at_head, "Finance")
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO elements (id, element_type, layer, aspect, name, package_id, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                "'active_structure', 'db-01', :package, now(), now())"
            ),
            {"package": package},
        )

    with pytest.raises(IntegrityError, match="fk_elements_package_id_packages"):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM packages WHERE id = :id"), {"id": package})


async def test_a_package_that_holds_a_package_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    parent = await a_package(engine_at_head, "Finance")
    await a_package(engine_at_head, "Budget", parent)

    with pytest.raises(IntegrityError, match="fk_packages_parent_id_packages"):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM packages WHERE id = :id"), {"id": parent})
