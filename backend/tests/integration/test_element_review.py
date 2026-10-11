"""Review and locking against a real PostgreSQL: decided on the locked row (docs/adr/0046)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.approval import reviewed_after_change
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, EntityKind
from ea.domain.errors import ElementLockedError, ElementNotFoundError
from ea.domain.model import Relationship
from ea.domain.ports import EndsReview
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.caller import acting_as
from tests.conftest import an_approver
from tests.integration.test_audit_registry import START, World

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def put_in_state(
    engine: AsyncEngine, element_id: UUID, state: str, *, locked: bool = False
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE elements SET approval_state = :s, locked = :l, "
                "approved_by_sub = CASE WHEN :s = 'approved' THEN 'approver-1' END, "
                "approved_at = CASE WHEN :s = 'approved' THEN now() END WHERE id = :id"
            ),
            {"s": state, "l": locked, "id": element_id},
        )


async def state_of(engine: AsyncEngine, element_id: UUID) -> tuple[Any, ...]:
    async with engine.connect() as connection:
        row = await connection.execute(
            text("SELECT approval_state, locked, revision FROM elements WHERE id = :id"),
            {"id": element_id},
        )
        return tuple(row.one())


async def wait_until_a_statement_waits_for_a_lock(engine: AsyncEngine) -> None:
    """Deterministic: no sleep, poll the server until a backend is blocked on a lock."""
    for _ in range(500):
        async with engine.connect() as connection:
            waiting = (
                await connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.01)
    pytest.fail("no statement ever waited for the lock")


async def test_an_approver_changing_an_approved_element_leaves_it_in_review_and_a_new_revision(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")
    await put_in_state(engine_at_head, element.id, "approved")

    with pytest.raises(ElementLockedError):
        await world.graph.update_element(element.id, name="db-02")
    assert await state_of(engine_at_head, element.id) == ("approved", False, 1)

    with acting_as(an_approver()):
        await world.graph.update_element(element.id, name="db-02")
    assert await state_of(engine_at_head, element.id) == ("in_review", False, 2)
    updates = [
        e for e in await world.events(EntityKind.ELEMENT) if e.action is AuditAction.ELEMENT_UPDATED
    ]
    assert len(updates) == 1  # the refusal wrote nothing


async def test_a_lock_committed_after_the_service_read_still_refuses_the_write(
    engine_at_head: AsyncEngine,
) -> None:
    """The decision is taken on the row read under the write's own lock, not on an earlier copy."""
    world = World(engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")
    await put_in_state(engine_at_head, element.id, "approved")
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:  # an approver locks the element, not yet committed
        await session.execute(
            text("UPDATE elements SET locked = true WHERE id = :id"), {"id": element.id}
        )
        with acting_as(an_approver()):
            writer = asyncio.create_task(world.graph.update_element(element.id, name="x"))
            await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError, match="locked"):
        await writer

    assert await state_of(engine_at_head, element.id) == ("approved", True, 1)


async def test_deleting_is_guarded_on_the_locked_row_too(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")
    await put_in_state(engine_at_head, element.id, "approved", locked=True)

    with acting_as(an_approver()), pytest.raises(ElementLockedError):
        await world.graph.delete_element(element.id)

    assert await world.graph.get_element(element.id) is not None
    deleted = [
        e for e in await world.events(EntityKind.ELEMENT) if e.action is AuditAction.ELEMENT_DELETED
    ]
    assert deleted == []


async def test_a_link_and_the_review_it_causes_commit_or_fail_together(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    approved = await world.graph.create_element(element_type=E.NODE, name="a")
    other = await world.graph.create_element(element_type=E.NODE, name="b")
    await put_in_state(engine_at_head, approved.id, "approved")
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    ends = EndsReview(change=reviewed_after_change, audit=lambda change: ())
    ghost = await world.graph.create_element(element_type=E.NODE, name="ghost")
    missing = Relationship.between(R.ASSOCIATION, approved, ghost, now=START)
    async with engine_at_head.begin() as connection:
        await connection.execute(text("DELETE FROM elements WHERE id = :id"), {"id": ghost.id})

    with pytest.raises(ElementNotFoundError):
        await graph.add_relationship(missing, audit=(), ends=ends)
    assert await state_of(engine_at_head, approved.id) == ("approved", False, 1)

    link = Relationship.between(R.ASSOCIATION, approved, other, now=START)
    await graph.add_relationship(link, audit=(), ends=ends)
    assert await state_of(engine_at_head, approved.id) == ("in_review", False, 2)
    assert await state_of(engine_at_head, other.id) == ("draft", False, 1)

    await put_in_state(engine_at_head, approved.id, "approved")
    assert await graph.delete_relationship(link.id, audit=(), ends=ends)
    assert (await state_of(engine_at_head, approved.id))[0] == "in_review"


async def test_a_locked_end_refuses_the_link_and_nothing_is_written(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    locked = await world.graph.create_element(element_type=E.NODE, name="a")
    other = await world.graph.create_element(element_type=E.NODE, name="b")
    await put_in_state(engine_at_head, locked.id, "approved", locked=True)

    with pytest.raises(ElementLockedError):
        await world.graph.connect(
            relationship_type=R.ASSOCIATION, source_id=other.id, target_id=locked.id
        )

    assert (await world.graph.relations_of(other.id)).relationships == ()
    assert [
        e for e in await world.events(EntityKind.RELATIONSHIP) if e.action.value.endswith("created")
    ] == []


async def test_a_concurrent_lock_and_link_never_leave_a_link_on_a_locked_element(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    one = await world.graph.create_element(element_type=E.NODE, name="a")
    two = await world.graph.create_element(element_type=E.NODE, name="b")
    await put_in_state(engine_at_head, one.id, "approved")
    sessions = create_session_factory(engine_at_head)

    async def link() -> object:
        return await world.graph.connect(
            relationship_type=R.ASSOCIATION, source_id=one.id, target_id=two.id
        )

    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE elements SET locked = true WHERE id = :id"), {"id": one.id}
        )
        writer = asyncio.create_task(link())
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError):
        await writer

    assert (await world.graph.relations_of(one.id)).relationships == ()


# --- Transitions: decided on the row read under the lock (docs/adr/0046) ----

from ea.domain.approval import Transition as Tr  # noqa: E402
from ea.domain.errors import (  # noqa: E402
    ApprovalStaleError,
    InvalidTransitionError,
    NotAuthorisedError,
)


async def in_review(world: World) -> UUID:
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")  # editor-1
    await world.graph.transition_element(element.id, Tr.SUBMIT)
    return element.id


async def test_an_approval_is_refused_when_the_element_changed_after_it_was_read(
    engine_at_head: AsyncEngine,
) -> None:
    """A is still writing when B approves what B read: the lock makes B wait, then see A."""
    world = World(engine_at_head)
    element_id = await in_review(world)
    read_by_the_approver = (await world.graph.get_element(element_id)).revision
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:  # A's write, not yet committed
        await session.execute(
            text("UPDATE elements SET description = 'A', revision = revision + 1 WHERE id = :id"),
            {"id": element_id},
        )
        with acting_as(an_approver()):
            approval = asyncio.create_task(
                world.graph.transition_element(
                    element_id, Tr.APPROVE, expected_revision=read_by_the_approver
                )
            )
            await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ApprovalStaleError):
        await approval
    assert (await state_of(engine_at_head, element_id))[0] == "in_review"

    with acting_as(an_approver()):  # B reads again, then approves what A wrote
        fresh = (await world.graph.get_element(element_id)).revision
        approved = await world.graph.transition_element(
            element_id, Tr.APPROVE, expected_revision=fresh
        )
    assert approved.approval_state.value == "approved" and approved.revision == fresh

    with acting_as(an_approver()):  # and a later change sends it back to review
        await world.graph.update_element(element_id, description="B")
    assert (await state_of(engine_at_head, element_id))[0] == "in_review"


async def test_the_last_author_is_read_from_the_journal_in_the_transition_and_refuses_them(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    element_id = await in_review(world)

    with acting_as(an_approver()):
        await world.graph.update_element(element_id, description="by B")
        fresh = (await world.graph.get_element(element_id)).revision
        with pytest.raises(NotAuthorisedError, match="separation"):
            await world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=fresh)
    with acting_as(an_approver(subject="approver-2", username="other")):
        approved = await world.graph.transition_element(
            element_id, Tr.APPROVE, expected_revision=fresh
        )

    assert approved.approved_by_sub == "approver-2"


async def test_a_concurrent_change_and_approval_by_the_same_person_never_end_approved(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    element_id = await in_review(world)
    revision = (await world.graph.get_element(element_id)).revision

    with acting_as(an_approver()):
        outcomes = await asyncio.gather(
            world.graph.update_element(element_id, description="by B"),
            world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=revision),
            return_exceptions=True,
        )

    assert not isinstance(outcomes[0], BaseException)  # the change always lands
    state, _, final_revision = await state_of(engine_at_head, element_id)
    assert state == "in_review" and final_revision == revision + 1
    # Approve first: legal (the last author was the editor) and then undone by B's change;
    # change first: stale. Either way no approval stands over B's unreviewed change.
    assert isinstance(outcomes[1], ApprovalStaleError) or outcomes[1].approval_state.value == (
        "approved"
    )


async def test_two_simultaneous_transitions_let_exactly_one_through(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    element_id = await in_review(world)
    revision = (await world.graph.get_element(element_id)).revision

    async def approve(who: str) -> object:
        with acting_as(an_approver(subject=who, username=who)):
            return await world.graph.transition_element(
                element_id, Tr.APPROVE, expected_revision=revision
            )

    outcomes = await asyncio.gather(
        approve("approver-a"), approve("approver-b"), return_exceptions=True
    )

    failures = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(failures) == 1 and isinstance(failures[0], InvalidTransitionError)
    assert (await state_of(engine_at_head, element_id))[0] == "approved"
    approvals = [
        e
        for e in await world.events(EntityKind.ELEMENT)
        if e.action is AuditAction.ELEMENT_APPROVED
    ]
    assert len(approvals) == 1


async def test_an_element_older_than_the_journal_cannot_be_approved_by_anyone_who_cannot_be_checked(
    engine_at_head: AsyncEngine,
) -> None:
    """No journal entry about the element: the separation rule refuses, it is never skipped."""
    world = World(engine_at_head)
    element_id = await in_review(world)
    revision = (await world.graph.get_element(element_id)).revision
    async with engine_at_head.begin() as connection:
        await connection.execute(text("ALTER TABLE audit_events DISABLE TRIGGER ALL"))
        await connection.execute(
            text("DELETE FROM audit_events WHERE entity_id = :id"), {"id": element_id}
        )
        await connection.execute(text("ALTER TABLE audit_events ENABLE TRIGGER ALL"))

    with acting_as(an_approver()), pytest.raises(NotAuthorisedError, match="unknown"):
        await world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=revision)


# --- The other doors into an element: its documents and its filing ----------


def documents_of(world: World, engine: AsyncEngine):  # type: ignore[no-untyped-def]
    from ea.repositories.document_store import PostgresDocumentRepository
    from ea.services.documents import DocumentService

    return DocumentService(
        PostgresDocumentRepository(create_session_factory(engine)),
        world.graph,
        clock=lambda: START,
    )


async def test_the_documents_of_an_approved_or_locked_element_are_frozen_with_it(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.audit import UNAUDITED_REINDEX
    from ea.repositories.document_store import PostgresDocumentRepository

    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")
    kept = await documents.attach_text(element.id, filename="runbook.md", content="# Runbook")
    doomed = await documents.attach_text(element.id, filename="old.md", content="# Old")
    await put_in_state(engine_at_head, element.id, "in_review")
    await documents.attach_text(element.id, filename="notes.md", content="# Fine in review")

    for locked in (False, True):
        await put_in_state(engine_at_head, element.id, "approved", locked=locked)
        with acting_as(an_approver()):  # not even an approver: reopen first
            with pytest.raises(ElementLockedError, match="reopen"):
                await documents.attach_text(element.id, filename="x.md", content="# X")
            with pytest.raises(ElementLockedError):
                await documents.revise_text(kept.id, filename="runbook.md", content="# Changed")
            with pytest.raises(ElementLockedError):
                await documents.discard(doomed.id)

    assert {d.filename for d in await documents.list_for_element(element.id)} == {
        "runbook.md",
        "old.md",
        "notes.md",
    }
    assert (await documents.get(kept.id)).content == "# Runbook"
    assert [
        e
        for e in await world.events(EntityKind.DOCUMENT)
        if e.action is AuditAction.DOCUMENT_REVISED
    ] == []
    # A reindex rewrites passages, not content: it is not a change.
    stored = await documents.get(kept.id)
    await PostgresDocumentRepository(create_session_factory(engine_at_head)).replace(
        stored, (), audit=UNAUDITED_REINDEX
    )


async def test_a_lock_committed_while_a_document_is_being_attached_still_refuses_it(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="db-01")
    await put_in_state(engine_at_head, element.id, "approved")
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE elements SET locked = true WHERE id = :id"), {"id": element.id}
        )
        with acting_as(an_approver()):
            writer = asyncio.create_task(
                documents.attach_text(element.id, filename="x.md", content="# X")
            )
            await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError, match="locked"):
        await writer

    assert await documents.list_for_element(element.id) == ()


async def test_filing_an_approved_or_locked_element_is_refused_whole(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    free = await world.graph.create_element(element_type=E.NODE, name="free")
    frozen = await world.graph.create_element(element_type=E.NODE, name="frozen")
    await put_in_state(engine_at_head, frozen.id, "approved")
    before = len(await world.events(EntityKind.PACKAGE))

    with pytest.raises(ElementLockedError, match="frozen"):
        await world.packages.move_elements([free.id, frozen.id], package.package.id)

    assert (await world.graph.get_element(free.id)).package_id is None  # none of them moved
    assert len(await world.events(EntityKind.PACKAGE)) == before

    await put_in_state(engine_at_head, frozen.id, "in_review")
    await world.packages.move_elements([free.id, frozen.id], package.package.id)
    assert (await world.graph.get_element(frozen.id)).package_id == package.package.id


async def test_deleting_a_package_does_not_re_file_what_is_approved_or_locked(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    element = await world.graph.create_element(element_type=E.NODE, name="n")
    await world.packages.move_elements([element.id], package.package.id)
    await put_in_state(engine_at_head, element.id, "approved", locked=True)

    with pytest.raises(ElementLockedError, match="n"):
        await world.packages.delete(package.package.id, move_contents_to=None)

    assert (await world.graph.get_element(element.id)).package_id == package.package.id
    assert await world.package_store.get(package.package.id) is not None

    await put_in_state(engine_at_head, element.id, "draft")
    await world.packages.delete(package.package.id, move_contents_to=None)
    assert (await world.graph.get_element(element.id)).package_id is None
