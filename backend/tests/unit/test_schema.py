"""The Neo4j schema is declared by hand, so its coverage is checked by machine."""

from __future__ import annotations

import pytest
from neo4j import NotificationMinimumSeverity

from ea.db.schema import ANY_RELATIONSHIP, SCHEMA_STATEMENTS, apply_schema
from ea.domain.archimate import RelationshipType


def test_every_relationship_type_has_an_id_index() -> None:
    """A missing index turns a relationship lookup into a full graph scan."""
    for relationship_type in RelationshipType:
        pattern = f"[r:{relationship_type.label}]"
        assert any(pattern in statement for statement in SCHEMA_STATEMENTS), relationship_type


def test_the_traversal_pattern_lists_every_relationship_type() -> None:
    """A type missing here would be invisible to every traversal endpoint."""
    listed = set(ANY_RELATIONSHIP.split("|"))

    assert listed == {relationship_type.label for relationship_type in RelationshipType}


def test_every_statement_is_idempotent() -> None:
    """Boot applies the whole list every time; none of it may fail on a re-run."""
    for statement in SCHEMA_STATEMENTS:
        assert "IF NOT EXISTS" in statement


class _FakeResult:
    """Just enough of a driver result to be consumed."""

    def __init__(self) -> None:
        self.consumed = False

    async def consume(self) -> None:
        self.consumed = True


class _FakeSession:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements
        self.results: list[_FakeResult] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def run(self, statement: str) -> _FakeResult:
        self._statements.append(statement)
        result = _FakeResult()
        self.results.append(result)
        return result


class _FakeDriver:
    """Records how the schema session is opened and what runs in it."""

    def __init__(self) -> None:
        self.config: dict[str, object] = {}
        self.statements: list[str] = []
        self.sessions: list[_FakeSession] = []

    def session(self, **config: object) -> _FakeSession:
        self.config = config
        session = _FakeSession(self.statements)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_applying_the_schema_runs_every_statement_against_the_database() -> None:
    """The whole list, on the configured database, and each result consumed."""
    driver = _FakeDriver()

    await apply_schema(driver, database="neo4j")  # type: ignore[arg-type]

    assert driver.statements == list(SCHEMA_STATEMENTS)
    assert driver.config["database"] == "neo4j"
    assert all(result.consumed for session in driver.sessions for result in session.results)


@pytest.mark.asyncio
async def test_applying_the_schema_asks_the_server_for_no_information_notices() -> None:
    """`IF NOT EXISTS` means "already there" is the expected answer, not news.

    Every boot after the first would otherwise log one INFORMATION notification
    per statement, saying the constraint it just declared already exists.
    """
    driver = _FakeDriver()

    await apply_schema(driver, database="neo4j")  # type: ignore[arg-type]

    assert driver.config["notifications_min_severity"] == NotificationMinimumSeverity.WARNING
