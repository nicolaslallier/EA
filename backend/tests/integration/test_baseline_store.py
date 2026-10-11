"""Capturing the model into a baseline, and reading it back (docs/adr/0045)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, AuditFilter
from ea.domain.baselines import Baseline, BaselineLimits, RestorePlan, diff_snapshots
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import BaselineNotFoundError, BaselineTooLargeError, DuplicateBaselineError
from ea.domain.model import Element, Relationship
from ea.domain.packages import Package
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.document_store import PostgresDocumentRepository
from ea.repositories.package_store import PostgresPackageRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_event(
    action: AuditAction, entity_id: UUID | None = None, *, at: datetime = NOW
) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=at,
        actor_sub="editor-1",
        actor_name="editor",
        action=action,
        entity_kind=action.kind,
        entity_id=entity_id,
        before=None,
        after={},
        request_id="req",
        channel=AuditChannel.HTTP,
    )


def created(stored: Baseline) -> tuple[AuditEvent, ...]:
    return (an_event(AuditAction.BASELINE_CREATED, stored.id),)


def a_baseline(name: str = "v1") -> Baseline:
    return Baseline.create(name=name, description="", created_by_sub="s", created_by="n", now=NOW)


class Model:
    """A small model on a real database: two filed elements, a link, a diagram, a document."""

    def __init__(self, engine: AsyncEngine, **limits: int) -> None:
        sessions = create_session_factory(engine)
        self.graph = PostgresArchitectureRepository(sessions)
        self.diagrams = PostgresDiagramRepository(sessions)
        self.packages = PostgresPackageRepository(sessions)
        self.documents = PostgresDocumentRepository(sessions)
        self.baselines = PostgresBaselineRepository(sessions, limits=BaselineLimits(**limits))
        self.trail = PostgresAuditRepository(sessions)

    async def seed(self) -> None:
        core = await self.packages.add(Package.create(name="Core", now=NOW), audit=())
        apps = await self.packages.add(
            Package.create(name="Apps", parent_id=core.id, now=NOW), audit=()
        )
        self.apps = apps
        self.left = await self.graph.add_element(
            Element.create(element_type=E.NODE, name="a", now=NOW, properties={"owner": "ops"}),
            audit=(),
        )
        self.right = await self.graph.add_element(
            Element.create(element_type=E.NODE, name="b", now=NOW), audit=()
        )
        await self.packages.move_elements([self.left.id], apps.id, audit=())
        self.link = await self.graph.add_relationship(
            Relationship.between(R.ASSOCIATION, self.left, self.right, now=NOW), audit=()
        )
        self.diagram = await self.diagrams.add(Diagram.create(name="Vue", now=NOW), audit=())
        await self.diagrams.replace_layout(
            self.diagram.id, [DiagramNode(self.left.id, 5, 6)], now=NOW, audit=()
        )
        await self.documents.add(
            Document.create(element_id=self.left.id, filename="a.md", content="# A\n", now=NOW),
            audit=(),
        )


@contextmanager
def statements_seen(engine: AsyncEngine) -> Iterator[list[tuple[str, str]]]:
    """(statement, isolation level it ran under) for every statement of the engine."""
    seen: list[tuple[str, str]] = []
    busy = False

    def record(connection, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal busy
        if busy or statement.lstrip().upper().startswith("SHOW"):
            return
        busy = True
        try:
            level = connection.exec_driver_sql("SHOW transaction_isolation").scalar_one()
        finally:
            busy = False
        seen.append((statement, level))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_a_capture_copies_elements_links_boxes_and_the_place_of_each_element(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()

    baseline = await model.baselines.create(a_baseline(), audit=created)

    assert (baseline.element_count, baseline.relationship_count, baseline.box_count) == (2, 1, 1)
    snapshot = await model.baselines.snapshot_of(baseline.id)
    current = await model.baselines.current_snapshot()
    assert sorted(snapshot.elements, key=lambda e: e.name) == sorted(
        current.elements, key=lambda e: e.name
    )
    assert [r.id for r in snapshot.relationships] == [model.link.id]
    assert [(b.diagram_id, b.x) for b in snapshot.boxes] == [(model.diagram.id, 5.0)]
    assert snapshot.package_paths == {model.apps.id: "Core / Apps"}
    assert (await model.baselines.get(baseline.id)) == baseline
    assert [b.name for b in await model.baselines.list_all()] == ["v1"]
    assert diff_snapshots(snapshot, current).is_empty


async def test_the_copy_is_frozen_while_the_model_moves_on_and_outlives_what_it_copied(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)

    await model.graph.update_element(model.left.id, lambda e: e.rename("a2", now=NOW), audit=())
    await model.graph.delete_element(model.left.id, audit=())
    await model.packages.delete(model.apps.id, into=None, audit=())

    diff = diff_snapshots(
        await model.baselines.snapshot_of(baseline.id), await model.baselines.current_snapshot()
    )
    assert [e.label for e in diff.elements_removed] == ["a"]
    assert diff.relationships_removed  # the link went with the element
    assert (await model.baselines.snapshot_of(baseline.id)).package_paths == {
        model.apps.id: "Core / Apps"
    }


async def test_a_name_is_used_once_and_a_delete_removes_the_copy_with_its_trace(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    first = await model.baselines.create(a_baseline(), audit=created)

    with pytest.raises(DuplicateBaselineError):
        await model.baselines.create(a_baseline(), audit=created)
    assert [e.action for e in (await model.trail.page(AuditFilter())).events] == [
        AuditAction.BASELINE_CREATED
    ]

    def deleted(stored: Baseline) -> tuple[AuditEvent, ...]:
        assert stored.element_count == 2  # the repository hands over what it deletes
        return (an_event(AuditAction.BASELINE_DELETED, stored.id, at=NOW + timedelta(seconds=1)),)

    assert await model.baselines.delete(first.id, audit=deleted)
    assert not await model.baselines.delete(first.id, audit=deleted)
    assert await model.baselines.get(first.id) is None
    assert (await model.baselines.snapshot_of(first.id)).elements == ()
    assert [e.action for e in (await model.trail.page(AuditFilter())).events] == [
        AuditAction.BASELINE_DELETED,
        AuditAction.BASELINE_CREATED,
    ]
    assert len(await model.graph.list_elements(_everything())) == 2  # the model is untouched


async def test_a_capture_reads_one_instant_of_the_model(engine_at_head: AsyncEngine) -> None:
    """Elements, links and boxes are three statements: under READ COMMITTED a link
    could be copied without its element. Every statement of a capture runs in one
    REPEATABLE READ transaction."""
    model = Model(engine_at_head)
    await model.seed()

    with statements_seen(engine_at_head) as seen:
        await model.baselines.create(a_baseline(), audit=created)
        await model.baselines.current_snapshot()
        await model.baselines.snapshot_of(uuid4())

    copies = [level for statement, level in seen if "INSERT INTO baseline_" in statement]
    reads = [level for statement, level in seen if statement.lstrip().startswith("SELECT")]
    assert len(copies) == 3 and set(copies) == {"repeatable read"}
    assert "read committed" not in reads


#: Each kind of row a baseline holds, lowered so that the seeded model (2 elements,
#: 1 link, 1 box, 1 diagram) is over it. Injected: no fixture of a hundred thousand rows.
LOWERED = {
    "elements": {"elements": 1},
    "relationships": {"relationships": 0},
    "boxes": {"boxes": 0},
    "bytes": {"bytes": 100},
    "diagrams": {"diagrams": 0},
}
ROW_KINDS = ["elements", "relationships", "boxes", "bytes"]
CASES = [
    *((path, kind) for path in ("create", "current_snapshot", "snapshot_of") for kind in ROW_KINDS),
    *((path, kind) for path in ("restore_plan", "apply_restore") for kind in LOWERED),
]
#: A statement that reads rows into Python, as opposed to counting them.
ROW_TABLES = (
    "FROM elements",
    "FROM relationships",
    "FROM diagram_nodes",
    "FROM diagrams",
    "FROM packages",
    "FROM baseline_",
    "INSERT INTO",
)


@pytest.mark.parametrize(("path", "kind"), CASES)
async def test_every_path_refuses_a_model_beyond_the_bound_before_reading_a_row(
    engine_at_head: AsyncEngine, path: str, kind: str
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    tight = PostgresBaselineRepository(
        create_session_factory(engine_at_head), limits=BaselineLimits(**LOWERED[kind])
    )
    calls = {
        "create": lambda: tight.create(a_baseline("big"), audit=created),
        "current_snapshot": lambda: tight.current_snapshot(),
        "snapshot_of": lambda: tight.snapshot_of(baseline.id),
        "restore_plan": lambda: tight.restore_plan(baseline.id, keep_new=False),
        "apply_restore": lambda: tight.apply_restore(
            baseline.id, keep_new=False, expected=RestorePlan(), now=NOW, audit=lambda _: ()
        ),
    }

    with statements_seen(engine_at_head) as seen, pytest.raises(BaselineTooLargeError):
        await calls[path]()

    reading = [
        statement
        for statement, _ in seen
        if any(table in statement for table in ROW_TABLES) and "count(" not in statement.lower()
    ]
    assert reading == []  # only counts ran: not one row was loaded or written
    assert [b.name for b in await model.baselines.list_all()] == ["v1"]
    assert [e.action for e in (await model.trail.page(AuditFilter())).events] == [
        AuditAction.BASELINE_CREATED
    ]


async def test_nested_package_paths_are_read_from_the_tree(engine_at_head: AsyncEngine) -> None:
    model = Model(engine_at_head)
    await model.seed()

    current = await model.baselines.current_snapshot()

    assert current.package_paths[model.apps.id] == "Core / Apps"


async def test_the_plan_counts_what_a_restoration_would_take_and_what_it_cannot_bring_back(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    empty = await model.baselines.create(a_baseline("empty"), audit=created)
    await model.seed()
    await model.graph.delete_element(model.right.id, audit=())
    baseline = await model.baselines.create(a_baseline("without-b"), audit=created)
    await model.graph.add_element(
        Element.create(element_type=E.NODE, name="late", now=NOW), audit=()
    )

    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)
    assert [e.name for e in plan.elements_to_delete] == ["late"]
    assert plan.documents_lost == 0 and plan.elements_to_create == ()

    wipe = await model.baselines.restore_plan(empty.id, keep_new=False)
    assert wipe.documents_lost == 1  # a.md goes with `a`
    assert wipe.boxes_lost == 1 and not wipe.is_blocked
    assert {e.name for e in wipe.elements_to_delete} == {"a", "late"}

    with pytest.raises(BaselineNotFoundError):
        await model.baselines.restore_plan(uuid4(), keep_new=False)


def _everything():  # type: ignore[no-untyped-def]
    from ea.domain.ports import ElementFilter

    return ElementFilter()
