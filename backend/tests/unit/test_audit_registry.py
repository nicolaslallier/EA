"""Packages and saved queries leave one audit event per use case, and a refusal none."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, EntityKind
from ea.domain.errors import (
    CyclicPackageError,
    DuplicatePackageError,
    DuplicateSavedQueryError,
    ElementNotFoundError,
    InvalidQueryError,
    NotAuthorisedError,
    PackageNotEmptyError,
    PackageNotFoundError,
    SavedQueryNotFoundError,
)
from ea.domain.packages import MAX_MOVE_BATCH
from ea.domain.query import ElementQuery, to_json
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemorySavedQueries,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio


class TestPackages:
    async def test_create_rename_move_and_delete_each_leave_one_event(
        self, package_service: PackageService, audit_log: InMemoryAuditLog
    ) -> None:
        root = await package_service.create(name="Finance")
        child = await package_service.create(name="Budget", parent_id=root.package.id)
        await package_service.update(child.package.id, name="Budgets")
        await package_service.update(child.package.id, parent_id=None)
        await package_service.delete(child.package.id)

        created, _, renamed, moved, deleted = audit_log.events
        assert [e.action for e in audit_log.events] == [
            AuditAction.PACKAGE_CREATED,
            AuditAction.PACKAGE_CREATED,
            AuditAction.PACKAGE_UPDATED,
            AuditAction.PACKAGE_UPDATED,
            AuditAction.PACKAGE_DELETED,
        ]
        assert created.entity_kind is EntityKind.PACKAGE
        assert (created.actor_sub, created.at) == ("editor-1", FIXED_NOW)
        assert created.before is None and created.after is not None
        assert created.after["name"] == "Finance"
        assert {c.field for c in renamed.changes} == {"name"}
        assert {c.field for c in moved.changes} == {"parent_id"}
        assert deleted.after is None and deleted.before is not None
        assert deleted.before["name"] == "Budgets"

    async def test_deleting_with_a_destination_traces_what_was_moved(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        diagram_service: DiagramService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        holder = await package_service.create(name="Holder")
        inner = await package_service.create(name="Inner", parent_id=holder.package.id)
        element = await service.create_element(element_type=E.NODE, name="n")
        diagram = await diagram_service.create(name="Vue", package_id=holder.package.id)
        await package_service.move_elements([element.id], holder.package.id)
        audit_log.events.clear()

        await package_service.delete(holder.package.id, move_contents_to=None)

        (event,) = audit_log.events
        assert event.action is AuditAction.PACKAGE_DELETED
        assert event.before is not None
        assert event.before["cascaded"] == {
            "moved_to": "root",
            "sub_packages": {"count": 1, "ids": [str(inner.package.id)]},
            "elements": {"count": 1, "ids": [str(element.id)]},
            "diagrams": {"count": 1, "ids": [str(diagram.id)]},
        }

    async def test_moving_elements_names_where_they_came_from_and_where_they_went(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        source = await package_service.create(name="Source")
        target = await package_service.create(name="Target")
        first = await service.create_element(element_type=E.NODE, name="a")
        second = await service.create_element(element_type=E.NODE, name="b")
        await package_service.move_elements([first.id], source.package.id)
        audit_log.events.clear()

        moved = await package_service.move_elements([first.id, second.id], target.package.id)

        assert moved == 2
        (event,) = audit_log.events
        assert event.action is AuditAction.PACKAGE_ELEMENTS_MOVED
        assert event.entity_id == target.package.id
        assert event.before == {
            "origins": {
                str(source.package.id): [str(first.id)],
                "root": [str(second.id)],
            }
        }
        assert event.after == {
            "destination": str(target.package.id),
            "element_ids": sorted([str(first.id), str(second.id)]),
            "count": 2,
        }

    async def test_moving_to_the_root_says_so(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        package = await package_service.create(name="P")
        element = await service.create_element(element_type=E.NODE, name="a")
        await package_service.move_elements([element.id], package.package.id)
        audit_log.events.clear()

        await package_service.move_elements([element.id], None)

        (event,) = audit_log.events
        assert event.entity_id is None
        assert event.after is not None and event.after["destination"] == "root"

    async def test_nothing_to_move_is_not_an_event(
        self, package_service: PackageService, audit_log: InMemoryAuditLog
    ) -> None:
        assert await package_service.move_elements([], None) == 0

        assert audit_log.events == []

    async def test_a_refusal_leaves_no_event(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        root = await package_service.create(name="Root")
        child = await package_service.create(name="Child", parent_id=root.package.id)
        element = await service.create_element(element_type=E.NODE, name="a")
        await package_service.move_elements([element.id], child.package.id)
        audit_log.events.clear()

        with pytest.raises(DuplicatePackageError):
            await package_service.create(name="root")
        with pytest.raises(PackageNotFoundError):
            await package_service.create(name="X", parent_id=uuid4())
        with pytest.raises(CyclicPackageError):
            await package_service.update(root.package.id, parent_id=child.package.id)
        with pytest.raises(PackageNotFoundError):
            await package_service.update(uuid4(), name="x")
        with pytest.raises(PackageNotEmptyError):
            await package_service.delete(root.package.id)
        with pytest.raises(PackageNotFoundError):
            await package_service.delete(uuid4())
        with pytest.raises(CyclicPackageError):
            await package_service.delete(root.package.id, move_contents_to=child.package.id)
        with pytest.raises(ElementNotFoundError):
            await package_service.move_elements([uuid4()], root.package.id)
        with pytest.raises(PackageNotFoundError):
            await package_service.move_elements([element.id], uuid4())
        with pytest.raises(ValueError, match="at most"):
            await package_service.move_elements(
                [uuid4() for _ in range(MAX_MOVE_BATCH + 1)], root.package.id
            )
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await package_service.create(name="nope")

        assert audit_log.events == []


class TestSavedQueries:
    async def test_create_update_and_delete_each_leave_one_event(
        self, saved_query_service: SavedQueryService, audit_log: InMemoryAuditLog
    ) -> None:
        with acting_as(an_editor(username="alice", subject="sub-alice")):
            saved = await saved_query_service.create(
                name="Les noeuds", query=ElementQuery(text="db")
            )
        with acting_as(an_editor(username="bob", subject="sub-bob")):
            await saved_query_service.update(saved.id, query=ElementQuery(text="web"))
            await saved_query_service.delete(saved.id)

        created, updated, deleted = audit_log.events
        assert [e.action for e in audit_log.events] == [
            AuditAction.SAVED_QUERY_CREATED,
            AuditAction.SAVED_QUERY_UPDATED,
            AuditAction.SAVED_QUERY_DELETED,
        ]
        assert created.entity_kind is EntityKind.SAVED_QUERY
        assert created.after is not None and created.after["created_by"] == "alice"
        assert created.after["query"] == to_json(ElementQuery(text="db"))
        assert (updated.actor_sub, deleted.actor_sub) == ("sub-bob", "sub-bob")
        assert {c.field for c in updated.changes} == {"query.text"}
        assert deleted.before is not None and deleted.after is None
        assert deleted.before["created_by"] == "alice"  # the author is never rewritten

    async def test_a_refusal_leaves_no_event(
        self,
        saved_query_service: SavedQueryService,
        saved_queries: InMemorySavedQueries,
        audit_log: InMemoryAuditLog,
    ) -> None:
        taken = await saved_query_service.create(name="A", query=ElementQuery())
        other = await saved_query_service.create(name="B", query=ElementQuery())
        saved_queries.corrupt.add(other.id)
        audit_log.events.clear()

        with pytest.raises(DuplicateSavedQueryError):
            await saved_query_service.create(name="A", query=ElementQuery())
        with pytest.raises(InvalidQueryError):
            await saved_query_service.update(other.id, name="C")
        with pytest.raises(SavedQueryNotFoundError):
            await saved_query_service.update(uuid4(), name="x")
        with pytest.raises(SavedQueryNotFoundError):
            await saved_query_service.delete(uuid4())
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await saved_query_service.delete(taken.id)

        assert audit_log.events == []

    async def test_a_row_the_code_can_no_longer_read_is_still_traced_when_deleted(
        self,
        saved_query_service: SavedQueryService,
        saved_queries: InMemorySavedQueries,
        audit_log: InMemoryAuditLog,
    ) -> None:
        stale = await saved_query_service.create(name="A", query=ElementQuery())
        saved_queries.corrupt.add(stale.id)
        audit_log.events.clear()

        await saved_query_service.delete(stale.id)

        (event,) = audit_log.events
        assert event.action is AuditAction.SAVED_QUERY_DELETED
