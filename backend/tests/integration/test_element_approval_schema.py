"""The approval columns: born `draft` on a populated database, and constrained (docs/adr/0046)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


async def test_what_already_existed_becomes_a_draft_nobody_approved_and_nothing_else_moves(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    existing = uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0012")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, properties, "
                    "created_at, updated_at) VALUES (:id, 'node', 'technology', "
                    "'active_structure', 'old', CAST(:props AS jsonb), :at, :at)"
                ),
                {"id": existing, "at": AT, "props": '{"vrf": "default", "ip_address": "10.0.0.1"}'},
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0013")

        async with postgres_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT approval_state, locked, approved_by_sub, approved_at, revision, "
                        "name, properties::text FROM elements WHERE id = :id"
                    ),
                    {"id": existing},
                )
            ).one()
        assert tuple(row[:5]) == ("draft", False, None, None, 1)
        assert row[5] == "old" and '"10.0.0.1"' in row[6]  # data untouched

        await asyncio.to_thread(command.downgrade, alembic_config, "0012")
        async with postgres_engine.connect() as connection:
            columns = {
                r[0]
                for r in await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'elements'"
                    )
                )
            }
            kept = (await connection.execute(text("SELECT count(*) FROM elements"))).scalar_one()
        assert "approval_state" not in columns and "locked" not in columns and kept == 1
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE elements SET locked = true WHERE id = :id", "ck_elements_locked_implies_approved"),
        (
            "UPDATE elements SET approval_state = 'weird' WHERE id = :id",
            "ck_elements_approval_state_known",
        ),
    ],
)
async def test_a_lock_without_an_approval_and_an_unknown_state_are_refused(
    engine_at_head: AsyncEngine, statement: str, constraint: str
) -> None:
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    element = Element.create(element_type=E.NODE, name="a", now=AT)
    await graph.add_element(element, audit=())

    with pytest.raises(IntegrityError) as raised:
        async with engine_at_head.begin() as connection:
            # Static statements from the parametrisation above; the id is bound.
            await connection.execute(text(statement), {"id": element.id})
    assert getattr(raised.value.orig.__cause__, "constraint_name", None) == constraint  # type: ignore[union-attr]


async def test_the_review_fields_survive_a_write_and_a_read(engine_at_head: AsyncEngine) -> None:
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    element = Element.create(element_type=E.NODE, name="a", now=AT)
    approved = replace(
        element,
        approval_state=ApprovalState.APPROVED,
        locked=True,
        approved_by_sub="approver-1",
        approved_at=AT,
    )

    await graph.add_element(approved, audit=())

    assert await graph.get_element(element.id) == approved


async def test_an_element_older_than_the_audit_trail_can_be_approved_by_an_approver(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """Its first journal line is the backfilled creation by `ea-system`: nobody's own change."""
    from ea.domain.approval import Transition
    from ea.services.architecture import ArchitectureService
    from ea.services.caller import acting_as
    from tests.conftest import an_approver

    existing = uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0010")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    "updated_at) VALUES (:id, 'node', 'technology', 'active_structure', 'old', "
                    ":at, :at)"
                ),
                {"id": existing, "at": AT},
            )
        await asyncio.to_thread(command.upgrade, alembic_config, "head")
        service = ArchitectureService(
            PostgresArchitectureRepository(create_session_factory(postgres_engine))
        )

        await service.transition_element(existing, Transition.SUBMIT)  # an editor
        with acting_as(an_approver()):
            approved = await service.transition_element(
                existing, Transition.APPROVE, expected_revision=1
            )

        assert approved.approval_state is ApprovalState.APPROVED
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
