"""A statement the database cancelled for taking too long is a typed 503, not a 500."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError

from ea.db.unit_of_work import is_query_canceled


class QueryCanceledError(Exception):
    sqlstate = "57014"


class OtherDbError(Exception):
    sqlstate = "23505"


def test_statement_timeout_is_recognised_by_its_sqlstate() -> None:
    assert is_query_canceled(DBAPIError("select", {}, QueryCanceledError()))


def test_another_failure_is_not() -> None:
    assert not is_query_canceled(DBAPIError("select", {}, OtherDbError()))


# --- What `grouped` does with the database's answers, through a doubled session ---------------

from contextlib import asynccontextmanager  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from ea.domain.analytics import RAW_GROUP_CEILING, Dimension  # noqa: E402
from ea.domain.errors import AnalysisTimeoutError, ScopeTooLargeError  # noqa: E402
from ea.repositories import architecture_store as store  # noqa: E402


def a_repository(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> Any:
    class Session:
        calls = 0

        async def execute(self, statement: object) -> Any:
            self.calls += 1
            if self.calls > 1 and isinstance(outcome, Exception):  # 1st: set_config
                raise outcome
            return SimpleNamespace(all=lambda: outcome)

    @asynccontextmanager
    async def reading(_: object):  # type: ignore[no-untyped-def]
        yield Session()

    monkeypatch.setattr(store, "reading", reading)
    return store.PostgresArchitectureRepository(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_cancelled_statement_is_a_typed_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = a_repository(monkeypatch, DBAPIError("select", {}, QueryCanceledError()))

    with pytest.raises(AnalysisTimeoutError):
        await repository.grouped((Dimension.parse("layer"),), None)


@pytest.mark.asyncio
async def test_more_groups_than_the_ceiling_is_refused_never_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [SimpleNamespace(k0=str(n), n=1) for n in range(RAW_GROUP_CEILING + 1)]
    repository = a_repository(monkeypatch, rows)

    with pytest.raises(ScopeTooLargeError):
        await repository.grouped((Dimension.parse("layer"),), None)
