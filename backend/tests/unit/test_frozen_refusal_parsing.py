"""Reading the database's refusal of a write into a published subtree."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.exc import DBAPIError

from ea.db.unit_of_work import frozen_refusal_of


def an_error(sqlstate: str | None, detail: str | None) -> DBAPIError:
    cause = Exception("driver error")
    cause.sqlstate = sqlstate  # type: ignore[attr-defined]
    cause.detail = detail  # type: ignore[attr-defined]
    orig = Exception("wrapped")
    orig.__cause__ = cause
    return DBAPIError("statement", {}, orig)


def test_the_detail_is_read_into_ids_a_version_a_table_and_an_operation() -> None:
    package = uuid4()
    refusal = frozen_refusal_of(an_error("EA001", f"{package}|v1.2.0|elements|UPDATE"))
    assert refusal is not None
    assert (refusal.package_id, refusal.version, refusal.table, refusal.operation) == (
        package,
        "v1.2.0",
        "elements",
        "UPDATE",
    )


def test_any_other_error_is_not_ours() -> None:
    assert frozen_refusal_of(an_error("23505", "dup")) is None
    assert frozen_refusal_of(an_error(None, None)) is None


def test_a_refusal_whose_detail_does_not_read_is_still_a_refusal() -> None:
    for detail in (None, "", "not-a-uuid|v1|t", "a|b"):
        refusal = frozen_refusal_of(an_error("EA001", detail))
        assert refusal is not None
        assert refusal.package_id is None
