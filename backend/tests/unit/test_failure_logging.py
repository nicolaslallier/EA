"""A failure's traceback never quotes the file: the sanitiser fails closed.

A driver's message names the value it refused (`DETAIL: Key (name)=(...) already exists`,
`Failing row contains (...)`), and SQLAlchemy's carries the statement and its parameters. During
an import those are the file's content. Nothing here recognises the sensitive text by its shape:
a failure with the database in its chain is logged as its structural fields only (class,
SQLSTATE, constraint, table, column, schema); a failure of any other type is logged with its
message replaced while an import is the reason it happened; and if the sanitiser itself fails,
a fixed sentence and the class are all that is said.
"""

from __future__ import annotations

import logging
import traceback

import pytest
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from ea.api import errors
from ea.api.errors import safe_failure

MARKER = "SECRET-MARKER-3f9a"


class FakePostgresError(Exception):
    """What asyncpg raises: the message carries the DETAIL, the fields are the structure."""

    sqlstate = "23505"
    constraint_name = "uq_elements_type_name"
    table_name = "elements"
    column_name = None
    schema_name = "public"
    detail = f"Key (name)=({MARKER}) already exists."


FakePostgresError.__module__ = "asyncpg.exceptions"


def a_unique_violation() -> IntegrityError:
    try:
        raise FakePostgresError(f"duplicate key value violates unique constraint: {MARKER}")
    except FakePostgresError as driver:
        wrapper = IntegrityError(
            "INSERT INTO elements (name) VALUES ($1)", {"name": MARKER}, driver
        )
        wrapper.__cause__ = driver
        return wrapper


def rendered(info: tuple[type, BaseException, object]) -> str:
    return "".join(traceback.format_exception(*info))  # type: ignore[arg-type]


def everything_said(info: tuple[type, BaseException, object], fields: dict[str, object]) -> str:
    return rendered(info) + repr(fields) + repr(info[1].args) + str(info[1])


def test_a_unique_violation_is_logged_as_its_structure_only() -> None:
    info, fields = safe_failure(a_unique_violation(), importing=True)

    said = everything_said(info, fields)
    assert MARKER not in said and "INSERT INTO" not in said
    assert fields == {
        "db_error": "IntegrityError",
        "sqlstate": "23505",
        "constraint_name": "uq_elements_type_name",
        "table_name": "elements",
        "schema_name": "public",
    }


@pytest.mark.parametrize("importing", [True, False])
def test_the_database_is_never_quoted_import_or_not(importing: bool) -> None:
    info, fields = safe_failure(a_unique_violation(), importing=importing)

    assert MARKER not in everything_said(info, fields)


def test_a_check_violation_and_a_too_long_value_are_covered_the_same_way() -> None:
    for kind in (DataError, OperationalError):
        try:
            raise FakePostgresError(f"Failing row contains ({MARKER}, 'x')")
        except FakePostgresError as driver:
            error = kind("INSERT ... VALUES ($1)", {"name": MARKER * 300}, driver)
            error.__cause__ = driver

        info, fields = safe_failure(error, importing=True)

        assert MARKER not in everything_said(info, fields)
        assert fields["db_error"] == kind.__name__


def test_the_chain_is_followed_a_failure_raised_from_the_database_is_not_quoted_either() -> None:
    try:
        try:
            raise a_unique_violation()
        except IntegrityError as db:
            raise ValueError(f"could not store {MARKER}") from db
    except ValueError as outer:
        error = outer

    info, fields = safe_failure(error, importing=False)

    assert MARKER not in everything_said(info, fields)
    assert fields["db_error"] == "IntegrityError"


def test_an_exception_group_with_the_database_in_it_is_covered() -> None:
    group = ExceptionGroup("a task group failed", [RuntimeError("fine"), a_unique_violation()])

    info, fields = safe_failure(group, importing=True)

    assert MARKER not in everything_said(info, fields)
    assert fields["db_error"] == "IntegrityError"


def test_a_failure_of_any_other_type_has_its_message_replaced_while_an_import_runs() -> None:
    error = KeyError(f"no column {MARKER}")

    during, fields = safe_failure(error, importing=True)
    outside, _ = safe_failure(error, importing=False)

    assert MARKER not in everything_said(during, fields)
    assert fields == {"error": "KeyError"}
    assert MARKER in rendered(outside)  # outside an import the failure is logged as it always was


def test_a_chain_too_long_to_be_followed_is_not_trusted() -> None:
    error: BaseException = RuntimeError(MARKER)
    for _ in range(40):
        outer = RuntimeError("wrap")
        outer.__cause__ = error
        error = outer

    info, fields = safe_failure(error, importing=False)

    assert MARKER not in everything_said(info, fields)


def test_a_sanitiser_that_fails_says_one_fixed_sentence_and_the_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_: BaseException) -> dict[str, object]:
        raise RuntimeError(f"the sanitiser itself failed on {MARKER}")

    monkeypatch.setattr(errors, "_structure_of", broken)

    info, fields = safe_failure(a_unique_violation(), importing=True)

    assert MARKER not in everything_said(info, fields)
    assert "unsanitisable database error" in str(info[1])
    assert fields == {"db_error": "IntegrityError"}


def test_an_error_that_is_not_recognised_as_the_database_is_still_replaced_in_an_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutation of the recogniser: it knows no driver. During an import nothing leaks anyway,
    because the message of what is not recognised is replaced, not matched."""
    monkeypatch.setattr(errors, "_DRIVERS", ("nothing.at.all",))

    info, fields = safe_failure(a_unique_violation(), importing=True)

    assert MARKER not in everything_said(info, fields)


def test_the_logger_of_the_statements_is_not_open_by_default() -> None:
    from ea.core.config import Settings
    from ea.core.logging import SQL_LOGGER, logging_config

    config = logging_config(Settings(debug=True, auth_enabled=False))

    assert Settings(debug=True, auth_enabled=False).log_sql is False
    assert config["loggers"][SQL_LOGGER]["level"] == "WARNING"  # parameters are not logged
    assert logging.getLevelName(logging.WARNING) == "WARNING"
