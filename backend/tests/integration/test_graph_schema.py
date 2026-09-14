"""The graph tables of migration 0005, as the server holds them — docs/adr/0033."""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

EXPECTED_INDEXES = {
    "pk_elements",
    "uq_elements_element_type_name",
    "ix_elements_element_type",
    "ix_elements_layer",
    "uq_elements_vrf_ip_address",
    "uq_elements_vrf_cidr",
    "pk_relationships",
    "ix_relationships_source_id",
    "ix_relationships_target_id",
}


async def test_the_graph_tables_carry_every_index_they_are_declared_with(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.connect() as connection:
        rows = await connection.execute(
            # A catalogue read with no runtime value: nothing to bind.
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename IN ('elements', 'relationships')"
            )
        )

    assert {row[0] for row in rows} >= EXPECTED_INDEXES


async def test_revision_0006_refuses_to_run_before_the_graph_is_imported(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """Otherwise every document and every box would look orphaned, and be purged."""
    await asyncio.to_thread(command.upgrade, alembic_config, "0005")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                # Test setup with literal values only: a document with no element yet.
                text(
                    "INSERT INTO element_documents (id, element_id, filename, content, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), gen_random_uuid(), "
                    "'a.md', '# A', now(), now())"
                )
            )

        with pytest.raises(Exception, match="make graph-import"):
            await asyncio.to_thread(command.upgrade, alembic_config, "head")
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_revision_0006_purges_true_orphans_once_elements_exist(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0005")
    try:
        async with postgres_engine.begin() as connection:
            # Test setup with literal values only.
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    "updated_at) VALUES ('00000000-0000-0000-0000-000000000001', 'node', "
                    "'technology', 'active_structure', 'db-01', now(), now())"
                )
            )
            await connection.execute(
                # Test setup with literal values only: one attached, one orphan.
                text(
                    "INSERT INTO element_documents (id, element_id, filename, content, "
                    "created_at, updated_at) VALUES "
                    "(gen_random_uuid(), '00000000-0000-0000-0000-000000000001', 'kept.md', "
                    "'# K', now(), now()), "
                    "(gen_random_uuid(), gen_random_uuid(), 'orphan.md', '# O', now(), now())"
                )
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        async with postgres_engine.connect() as connection:
            # A read with no runtime value: nothing to bind.
            names = await connection.scalars(text("SELECT filename FROM element_documents"))
            assert list(names) == ["kept.md"]
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
