"""What `apply_restore` decides, without a database: refusals, order of writes, translation.

The SQL itself is proved against PostgreSQL in `tests/integration/`; what is
checked here is the orchestration around it, with a session that only records.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from ea.db.models.baseline import BaselineElementRecord, BaselineRelationshipRecord
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.baselines import (
    LOGGED_BLOCKED,
    BaselineLimits,
    BoxSnapshot,
    ElementUpdate,
    ModelSize,
    RestorePlan,
)
from ea.domain.errors import (
    BaselineTooLargeError,
    RestoreBlockedError,
    RestoreConflictError,
)
from ea.domain.model import Element, Relationship
from ea.repositories import baseline_store
from ea.repositories.baseline_store import (
    PostgresBaselineRepository,
    _box,
    _copied_element,
    _copied_relationship,
)
from ea.repositories.conflicts import is_serialization_failure

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def node(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


class RecordingSession:
    def __init__(self, *, flush_raises: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.flush_raises = flush_raises

    async def connection(self, **_: Any) -> None:
        self.calls.append("connection")

    async def execute(self, statement: Any) -> list[Any]:
        self.calls.append(str(statement).split(" ")[0] + " " + str(statement).split()[2])
        return []

    async def get(self, model: Any, identifier: Any) -> Any:
        return SimpleNamespace(properties={})  # a row the update assigns to

    def add(self, row: Any) -> None:
        self.calls.append(f"add {type(row).__name__}")

    def add_all(self, rows: Any) -> None:
        self.calls.extend(f"add {type(row).__name__}" for row in rows)

    async def flush(self) -> None:
        self.calls.append("flush")
        if self.flush_raises is not None:
            raise self.flush_raises

    def expunge_all(self) -> None:
        self.calls.append("expunge")


class Unit:
    def __init__(self, session: RecordingSession) -> None:
        self.session = session

    def begin(self) -> Unit:
        return self

    async def __aenter__(self) -> RecordingSession:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


def repository(session: RecordingSession, plan: RestorePlan) -> PostgresBaselineRepository:
    repo = PostgresBaselineRepository(Unit(session))  # type: ignore[arg-type]

    async def planned(*_: object, **__: object) -> RestorePlan:
        return plan

    repo._plan = planned  # type: ignore[method-assign]
    return repo


async def apply(repo: PostgresBaselineRepository, expected: RestorePlan) -> None:
    await repo.apply_restore(
        uuid4(), keep_new=False, expected=expected, now=NOW, audit=lambda _: ()
    )


def driver_error(sqlstate: str | None = None, constraint: str | None = None) -> Exception:
    driver = Exception("refused")
    driver.sqlstate = sqlstate  # type: ignore[attr-defined]
    driver.constraint_name = constraint  # type: ignore[attr-defined]
    adapter = Exception("refused")
    adapter.__cause__ = driver
    return adapter


def the_whole_plan() -> RestorePlan:
    a, b, c, d = node("a"), node("b"), node("c"), node("d")
    return RestorePlan(
        elements_to_create=(a,),
        elements_to_update=(ElementUpdate(b, replace(b, name="b2"), ()),),
        elements_to_delete=(c,),
        relationships_to_create=(Relationship.between(R.ASSOCIATION, a, d, now=NOW),),
        relationships_to_delete=(Relationship.between(R.ASSOCIATION, c, d, now=NOW),),
        boxes_to_restore=(BoxSnapshot(uuid4(), a.id, 1, 2, 3, 4),),
    )


@pytest.mark.asyncio
class TestApplyingAPlan:
    async def test_a_plan_touching_a_protected_element_writes_nothing(self) -> None:
        session = RecordingSession()
        blocked = RestorePlan(blocked=(node("locked"),))

        with pytest.raises(RestoreBlockedError, match="locked"):
            await apply(repository(session, blocked), RestorePlan())

        assert session.calls == ["connection", "LOCK elements,"]  # the freeze lock comes first

    async def test_a_plan_that_is_not_the_one_expected_writes_nothing(self) -> None:
        session = RecordingSession()

        with pytest.raises(RestoreConflictError, match="since the plan"):
            await apply(repository(session, the_whole_plan()), RestorePlan())

        assert session.calls == ["connection", "LOCK elements,"]  # the freeze lock comes first

    async def test_the_write_order_of_a_plan(
        self,
    ) -> None:
        session = RecordingSession()
        plan = the_whole_plan()

        await apply(repository(session, plan), plan)

        assert session.calls == [
            "connection",
            "LOCK elements,",  # before any read: the snapshot must not predate a freeze
            "expunge",
            "SELECT relationships.target_id",  # who the deleted elements' links reach...
            "UPDATE SET",  # ...a link changing is a change to its ends: a new revision
            "UPDATE SET",  # the diagrams showing what is deleted: a new revision, before the cascade
            "DELETE relationships",
            "DELETE elements",
            "flush",  # every element to put back steps aside under its id...
            "flush",  # ...then takes its final values, flushed to name a clash at once
            "add ElementRecord",
            "flush",
            "add RelationshipRecord",
            "flush",
            "INSERT diagram_nodes",
            "UPDATE SET",  # the diagrams whose boxes came back: a new revision, so no stale save
        ]

    async def test_every_refusal_is_logged_before_it_is_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        blocked, stale = RestorePlan(blocked=(node("locked"),)), the_whole_plan()

        with caplog.at_level("WARNING"):
            for plan, expected, error in (
                (blocked, RestorePlan(), RestoreBlockedError),
                (stale, RestorePlan(), RestoreConflictError),
            ):
                with pytest.raises(error):
                    await apply(repository(RecordingSession(), plan), expected)

        actions = [getattr(r, "action", None) for r in caplog.records]
        assert actions == ["restore_blocked", "restore_conflict"]
        assert caplog.records[0].blocked_count == 1  # type: ignore[attr-defined]

    async def test_five_thousand_blocked_elements_make_a_small_message_and_a_small_record(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        many = tuple(node("x" * 1000 + str(i)) for i in range(5000))

        with caplog.at_level("WARNING"), pytest.raises(RestoreBlockedError) as raised:
            await apply(repository(RecordingSession(), RestorePlan(blocked=many)), RestorePlan())

        assert len(str(raised.value)) < 3000 and "5000" in str(raised.value)
        (record,) = caplog.records
        assert record.blocked_count == 5000  # type: ignore[attr-defined]
        assert len(record.blocked) == LOGGED_BLOCKED  # type: ignore[attr-defined]
        assert len(repr(record.blocked)) < 5000  # type: ignore[attr-defined]
        assert len(record.getMessage()) < 3000

    async def test_a_name_taken_meanwhile_names_the_element(self) -> None:
        session = RecordingSession()
        session.flush_raises = IntegrityError(
            "INSERT", {}, driver_error(constraint="uq_elements_element_type_name")
        )
        plan = RestorePlan(elements_to_create=(node("web"),))

        with pytest.raises(RestoreConflictError, match="'web'"):
            await apply(repository(session, plan), plan)

    async def test_an_unknown_refusal_is_left_alone(self) -> None:
        session = RecordingSession()
        session.flush_raises = IntegrityError("INSERT", {}, driver_error(constraint="mystery"))
        plan = RestorePlan(elements_to_create=(node("web"),))

        with pytest.raises(IntegrityError):
            await apply(repository(session, plan), plan)

    @pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
    async def test_a_serialization_failure_is_a_replayable_conflict(self, sqlstate: str) -> None:
        session = RecordingSession()
        session.flush_raises = DBAPIError("UPDATE", {}, driver_error(sqlstate=sqlstate))
        plan = RestorePlan(elements_to_create=(node("web"),))

        with pytest.raises(RestoreConflictError, match="during the restoration"):
            await apply(repository(session, plan), plan)

    async def test_a_key_that_vanished_meanwhile_is_a_replayable_conflict(self) -> None:
        session = RecordingSession()
        session.flush_raises = IntegrityError(
            "INSERT", {}, driver_error(constraint="fk_relationships_target_id_elements")
        )
        a, b = node("a"), node("b")
        plan = RestorePlan(
            relationships_to_create=(Relationship.between(R.ASSOCIATION, a, b, now=NOW),)
        )

        with pytest.raises(RestoreConflictError, match="removed meanwhile"):
            await apply(repository(session, plan), plan)

    async def test_any_other_driver_error_is_not_ours_to_translate(self) -> None:
        session = RecordingSession()
        session.flush_raises = DBAPIError("UPDATE", {}, driver_error(sqlstate="XX000"))
        plan = RestorePlan(elements_to_create=(node("web"),))

        with pytest.raises(DBAPIError):
            await apply(repository(session, plan), plan)


@pytest.mark.asyncio
class TestTheBoundsBeforeTheRows:
    async def test_a_guard_refuses_on_the_counts_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def measured(*_: object, **__: object) -> ModelSize:
            return ModelSize(elements=3)

        monkeypatch.setattr(baseline_store, "_measure", measured)
        repo = PostgresBaselineRepository(
            Unit(RecordingSession()), limits=BaselineLimits(elements=2)
        )  # type: ignore[arg-type]

        with pytest.raises(BaselineTooLargeError, match="the model holds 3 elements"):
            await repo._guard_live(RecordingSession(), diagrams=False)  # type: ignore[arg-type]
        with pytest.raises(BaselineTooLargeError, match="the baseline holds 3 elements"):
            await repo._guard_baseline(RecordingSession(), uuid4())  # type: ignore[arg-type]


class TestReadingRows:
    def test_a_copied_element_and_link_read_back_as_the_model_values(self) -> None:
        package = uuid4()
        row = BaselineElementRecord(
            baseline_id=uuid4(),
            id=uuid4(),
            element_type="node",
            layer="technology",
            aspect="active_structure",
            name="db",
            description="d",
            documentation="",
            properties={"owner": "ops"},
            package_id=package,
            package_path="A",
            created_at=NOW,
            updated_at=NOW,
        )
        link = BaselineRelationshipRecord(
            baseline_id=uuid4(),
            id=uuid4(),
            relationship_type="access",
            source_id=uuid4(),
            target_id=uuid4(),
            source_type="node",
            target_type="node",
            name="",
            access_type="read",
            directed=True,
            properties={},
            created_at=NOW,
        )

        element = _copied_element(row)
        relationship = _copied_relationship(link)

        assert (element.name, element.package_id, dict(element.properties)) == (
            "db",
            package,
            {"owner": "ops"},
        )
        assert relationship.access_type is not None and relationship.directed

    def test_a_box_reads_the_same_from_either_table(self) -> None:
        row = SimpleNamespace(diagram_id=uuid4(), element_id=uuid4(), x=1, y=2, width=3, height=4)

        assert _box(row) == BoxSnapshot(row.diagram_id, row.element_id, 1, 2, 3, 4)  # type: ignore[arg-type]

    def test_only_a_serialization_failure_or_a_deadlock_is_replayable(self) -> None:
        assert is_serialization_failure(DBAPIError("", {}, driver_error("40001")))
        assert not is_serialization_failure(DBAPIError("", {}, Exception("no cause")))


class TestTheStatementsThatCountAndWalk:
    """Compiled, not run: the shape of the SQL that a database test would only imply."""

    def compiled(self, statement: Any) -> str:
        from sqlalchemy.dialects import postgresql

        return str(statement.compile(dialect=postgresql.dialect()))

    def test_package_paths_walk_the_tree_from_its_roots_with_the_separator_bound(self) -> None:
        from sqlalchemy import select

        sql = self.compiled(select(baseline_store._paths().c.path))

        assert "WITH RECURSIVE package_paths" in sql
        assert "parent_id IS NULL" in sql and "||" in sql
        assert " / " not in sql  # a bound parameter, not text spliced into the statement

    def test_a_baseline_is_listed_with_one_count_per_kind_of_copied_row(self) -> None:
        sql = self.compiled(baseline_store._counted())

        for table in ("baseline_elements", "baseline_relationships", "baseline_boxes"):
            assert f"FROM {table}" in sql
        assert sql.count("count(*)") == 3

    def test_a_stored_baseline_reads_back_with_its_counts(self) -> None:
        from ea.db.models.baseline import BaselineRecord

        row = BaselineRecord(
            id=uuid4(),
            name="v1",
            description="",
            created_by_sub="s",
            created_by="n",
            created_at=NOW,
        )

        baseline = baseline_store._baseline(row, 2, 1, 0)

        assert (baseline.name, baseline.element_count, baseline.relationship_count) == ("v1", 2, 1)
