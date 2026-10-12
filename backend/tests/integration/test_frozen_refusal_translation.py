"""The database's refusal reaches the caller as a typed error, and leaves a trace."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import set_refusal_recorder, work
from ea.domain.errors import PackageFrozenError
from ea.domain.package_versions import FrozenRefusal
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
