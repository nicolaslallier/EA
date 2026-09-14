"""The graph tables of migration 0005, as the server holds them — docs/adr/0033."""

from __future__ import annotations

import pytest
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
