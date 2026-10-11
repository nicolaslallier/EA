"""A deadlock or a serialization failure is a replayable 409, never a 500 (docs/adr/0048)."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from ea.domain.errors import WriteConflictError
from ea.repositories import architecture_store, document_store, package_store
from ea.repositories.conflicts import is_serialization_failure, replayable
from tests.unit.test_baseline_store_pure import driver_error


def failing_with(error: Exception):  # type: ignore[no-untyped-def]
    @replayable
    async def write() -> str:
        raise error

    return write


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_a_serialization_failure_or_a_deadlock_becomes_a_replayable_conflict(
    sqlstate: str,
) -> None:
    with pytest.raises(WriteConflictError, match="again") as raised:
        await failing_with(DBAPIError("", {}, driver_error(sqlstate)))()

    assert isinstance(raised.value.__cause__, DBAPIError)  # the log keeps the real cause


@pytest.mark.asyncio
async def test_any_other_database_failure_passes_through_untouched() -> None:
    other = IntegrityError("", {}, driver_error("23505"))
    with pytest.raises(IntegrityError):
        await failing_with(other)()
    with pytest.raises(ValueError, match="nothing to do with the database"):
        await failing_with(ValueError("nothing to do with the database"))()


@pytest.mark.asyncio
async def test_a_write_that_succeeds_returns_what_it_returned() -> None:
    @replayable
    async def write(value: int) -> int:
        return value + 1

    assert await write(1) == 2


def test_the_recognised_states_are_the_two_that_postgresql_asks_to_retry() -> None:
    assert is_serialization_failure(DBAPIError("", {}, driver_error("40001")))
    assert is_serialization_failure(DBAPIError("", {}, driver_error("40P01")))
    assert not is_serialization_failure(DBAPIError("", {}, driver_error("23505")))
    assert not is_serialization_failure(DBAPIError("", {}, Exception("no cause")))


@pytest.mark.parametrize(
    ("store", "writers"),
    [
        (
            architecture_store.PostgresArchitectureRepository,
            [
                "add_element",
                "update_element",
                "transition_element",
                "delete_element",
                "add_relationship",
                "update_relationship",
                "delete_relationship",
            ],
        ),
        (document_store.PostgresDocumentRepository, ["add", "replace", "delete"]),
        (package_store.PostgresPackageRepository, ["move_elements", "delete"]),
    ],
)
def test_every_writer_that_locks_elements_is_replayable(store: type, writers: list[str]) -> None:
    for name in writers:
        assert getattr(getattr(store, name), "__replayable__", False), f"{store.__name__}.{name}"
