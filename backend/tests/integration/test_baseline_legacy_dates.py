"""A baseline captured before revision 0014 may hold any string under the validity keys.

Reading it back must neither raise nor invent: the date is set aside as `legacy_*`
(docs/adr/0059), so a restore never writes an unreadable date back into the model.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent
from ea.domain.baselines import Baseline
from ea.repositories.baseline_store import PostgresBaselineRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


async def test_a_stored_unreadable_date_is_read_back_set_aside(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        await connection.execute(
            # Test setup: the properties are bound, the rest is literal.
            text(
                "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                "'active_structure', 'db-01', CAST(:p AS jsonb), now(), now())"
            ),
            {"p": json.dumps({"owner": "x"})},
        )
    repository = PostgresBaselineRepository(create_session_factory(engine_at_head))
    baseline = Baseline.create(
        name="before", description="", created_by_sub="s", created_by="n", now=NOW
    )

    def created(stored: Baseline) -> tuple[AuditEvent, ...]:
        return (
            AuditEvent(
                id=uuid4(),
                at=NOW,
                actor_sub="s",
                actor_name="n",
                action=AuditAction.BASELINE_CREATED,
                entity_kind=AuditAction.BASELINE_CREATED.kind,
                entity_id=stored.id,
                before=None,
                after={},
                request_id="r",
                channel=AuditChannel.HTTP,
            ),
        )

    await repository.create(baseline, audit=created)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            # A copy written by the code that predates the rule: one stored date is no day.
            text(
                "UPDATE baseline_elements SET properties = CAST(:p AS jsonb) WHERE baseline_id = :b"
            ),
            {"p": json.dumps({"valid_until": "2027-06", "owner": "x"}), "b": baseline.id},
        )

    snapshot = await repository.snapshot_of(baseline.id)

    (element,) = snapshot.elements
    assert dict(element.properties) == {"legacy_valid_until": "2027-06", "owner": "x"}
