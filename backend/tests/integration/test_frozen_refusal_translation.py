"""The database's refusal reaches the caller as a typed error, and leaves a trace."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import apply_rows, set_refusal_recorder, shared_transaction, work
from ea.domain.errors import PackageFrozenError
from ea.domain.package_versions import FrozenRefusal
from ea.domain.ports import RowRefusal
from tests.integration.test_package_freeze_triggers import (
    a_package,
    an_element,
    publish,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_recorder_left_behind():  # type: ignore[no-untyped-def]
    yield
    set_refusal_recorder(None)


async def test_a_refused_write_becomes_a_typed_error_and_is_recorded_once(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root, "v2.0.0")
    seen: list[FrozenRefusal] = []

    async def recorder(refusal: FrozenRefusal) -> None:
        seen.append(refusal)

    set_refusal_recorder(recorder)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)

    with pytest.raises(PackageFrozenError) as raised:
        async with work(sessions) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
            )

    assert raised.value.refusal == seen[0]
    assert (seen[0].package_id, seen[0].version, seen[0].table, seen[0].operation) == (
        root,
        "v2.0.0",
        "elements",
        "UPDATE",
    )
    assert len(seen) == 1
    assert "inside" not in str(raised.value)


async def test_a_recorder_that_fails_never_hides_the_refusal(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)

    async def broken(refusal: FrozenRefusal) -> None:
        raise RuntimeError(str(uuid4()))

    set_refusal_recorder(broken)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    with pytest.raises(PackageFrozenError):
        async with work(sessions) as session:
            await session.execute(text("DELETE FROM elements WHERE id = :id"), {"id": element})


async def _names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(text("SELECT name FROM elements"))
        return {row[0] for row in rows}


async def _insert_element(session, name: str) -> None:  # type: ignore[no-untyped-def]
    await session.execute(
        text(
            "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, updated_at)"
            " VALUES (:id, 'node', 'technology', 'active_structure', :name, now(), now())"
        ),
        {"id": uuid4(), "name": name},
    )


async def test_the_trace_is_written_outside_the_unit_it_was_refused_in(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)

    async def recorder(refusal: FrozenRefusal) -> None:
        async with work(sessions) as session:
            await _insert_element(session, "trace")

    set_refusal_recorder(recorder)
    with pytest.raises(PackageFrozenError):
        async with shared_transaction(sessions, commit=False):
            async with work(sessions) as session:
                await _insert_element(session, "dry-run-write")
            async with work(sessions) as session:
                await session.execute(
                    text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
                )

    names = await _names(engine_at_head)
    assert "trace" in names
    assert "dry-run-write" not in names


async def test_a_row_refused_by_the_freeze_is_one_refusal_and_the_others_stay(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    seen: list[FrozenRefusal] = []

    async def recorder(refusal: FrozenRefusal) -> None:
        seen.append(refusal)

    set_refusal_recorder(recorder)

    async def apply(row: str) -> None:
        async with work(sessions) as session:
            if row == "frozen":
                await session.execute(
                    text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
                )
            else:
                await _insert_element(session, row)

    async with shared_transaction(sessions, commit=True):
        refused = await apply_rows(["first", "frozen", "last"], apply)

    assert len(refused) == 1
    assert isinstance(refused[0], RowRefusal)
    assert refused[0].index == 1
    assert isinstance(refused[0].error, PackageFrozenError)
    assert len(seen) == 1
    assert {"first", "last"} <= await _names(engine_at_head)
