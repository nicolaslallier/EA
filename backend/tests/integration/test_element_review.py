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
from tests.conftest import an_approver, an_editor
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
    assert await state_of(engine_at_head, other.id) == ("draft", False, 2)  # a link is a change

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


async def test_an_element_whose_last_author_is_unknown_cannot_be_approved(
    engine_at_head: AsyncEngine,
) -> None:
    """A missing fact about who changed it: the separation rule refuses, it is never skipped."""
    world = World(engine_at_head)
    element_id = await in_review(world)
    revision = (await world.graph.get_element(element_id)).revision
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET last_modified_by_sub = NULL WHERE id = :id"),
            {"id": element_id},
        )

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
            with pytest.raises(ElementLockedError, match=r"reopen|unlock"):
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


# --- The same race, door by door: the approval lands while the write is in flight ---


async def approve_without_committing(session: object, element_id: UUID) -> None:
    await session.execute(  # type: ignore[attr-defined]
        text(
            "UPDATE elements SET approval_state = 'approved', approved_by_sub = 'approver-1', "
            "approved_at = now() WHERE id = :id"
        ),
        {"id": element_id},
    )


async def test_an_approval_landing_during_a_document_write_refuses_it_or_comes_first(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element_id = await in_review(world)
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:  # the approval is in flight, not committed
        await approve_without_committing(session, element_id)
        writer = asyncio.create_task(
            documents.attach_text(element_id, filename="late.md", content="# Late")
        )
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError, match="approved"):
        await writer

    assert await documents.list_for_element(element_id) == ()  # never a document after approval


async def test_a_document_written_after_the_approver_read_makes_the_approval_stale(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element_id = await in_review(world)
    read = (await world.graph.get_element(element_id)).revision
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        # A document write in flight: it holds the element and has moved its revision.
        await session.execute(
            text("UPDATE elements SET revision = revision + 1 WHERE id = :id"), {"id": element_id}
        )
        with acting_as(an_approver()):
            approval = asyncio.create_task(
                world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=read)
            )
            await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ApprovalStaleError):
        await approval

    # and the real write does move the revision
    before = (await world.graph.get_element(element_id)).revision
    await documents.attach_text(element_id, filename="d.md", content="# D")
    assert (await world.graph.get_element(element_id)).revision == before + 1


async def test_an_approval_landing_during_a_filing_refuses_it(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    element_id = await in_review(world)
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        await approve_without_committing(session, element_id)
        mover = asyncio.create_task(world.packages.move_elements([element_id], package.package.id))
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError):
        await mover

    assert (await world.graph.get_element(element_id)).package_id is None


async def test_an_approval_landing_during_a_package_deletion_refuses_the_re_filing(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    element_id = await in_review(world)
    await world.packages.move_elements([element_id], package.package.id)
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        await approve_without_committing(session, element_id)
        deleter = asyncio.create_task(
            world.packages.delete(package.package.id, move_contents_to=None)
        )
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError):
        await deleter

    assert (await world.graph.get_element(element_id)).package_id == package.package.id


async def test_an_approval_landing_during_an_address_change_refuses_it(
    engine_at_head: AsyncEngine,
) -> None:
    """IPAM writes through `update_element`: the same lock, the same refusal."""
    from ea.repositories.architecture_store import PostgresArchitectureRepository
    from ea.services.ipam import IpamService

    world = World(engine_at_head)
    repository = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    ipam = IpamService(world.graph, repository)
    await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
    element_id = await in_review(world)
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        await approve_without_committing(session, element_id)
        assigner = asyncio.create_task(ipam.assign_address(element_id, "10.0.1.12"))
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError):
        await assigner

    assert "ip_address" not in (await world.graph.get_element(element_id)).properties


# --- Every door records who changed the element, and moves its revision (docs/adr/0046) ---


async def authored(engine: AsyncEngine, element_id: UUID) -> tuple[Any, ...]:
    async with engine.connect() as connection:
        row = await connection.execute(
            text("SELECT last_modified_by_sub, revision FROM elements WHERE id = :id"),
            {"id": element_id},
        )
        return tuple(row.one())


async def test_who_adds_a_document_cannot_approve_the_element(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element_id = await in_review(world)  # created and submitted by editor-1

    with acting_as(an_approver()):  # approver-1 adds a document: now the last author
        await documents.attach_text(element_id, filename="d.md", content="# D")
        fresh = (await world.graph.get_element(element_id)).revision
        with pytest.raises(NotAuthorisedError, match="separation"):
            await world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=fresh)
    assert (await authored(engine_at_head, element_id))[0] == "approver-1"

    with acting_as(an_approver(subject="approver-2", username="v")):
        approved = await world.graph.transition_element(
            element_id, Tr.APPROVE, expected_revision=fresh
        )
    assert approved.approval_state.value == "approved"


async def test_a_document_added_after_the_approver_read_makes_the_approval_stale(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    documents = documents_of(world, engine_at_head)
    element_id = await in_review(world)
    with acting_as(an_approver(subject="v", username="v")):
        read = (await world.graph.get_element(element_id)).revision  # V reads revision n
    with acting_as(an_approver()):  # U adds a document
        await documents.attach_text(element_id, filename="d.md", content="# D")

    with acting_as(an_approver(subject="v", username="v")):
        with pytest.raises(ApprovalStaleError):
            await world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=read)
        # discarding it is a change too
        document = (await documents.list_for_element(element_id))[0]
    after_add = (await authored(engine_at_head, element_id))[1]
    with acting_as(an_editor(subject="e2", username="e2")):
        await documents.discard(document.id)
    assert (await authored(engine_at_head, element_id)) == ("e2", after_add + 1)


async def test_filing_an_element_is_a_change_by_the_one_who_files_it(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    element_id = await in_review(world)
    before = (await authored(engine_at_head, element_id))[1]

    with acting_as(an_approver()):
        await world.packages.move_elements([element_id], package.package.id)
        assert await authored(engine_at_head, element_id) == ("approver-1", before + 1)
        await world.packages.move_elements([element_id], package.package.id)  # nothing moves
        assert await authored(engine_at_head, element_id) == ("approver-1", before + 1)
        fresh = (await world.graph.get_element(element_id)).revision
        with pytest.raises(NotAuthorisedError, match="separation"):
            await world.graph.transition_element(element_id, Tr.APPROVE, expected_revision=fresh)

    with acting_as(an_editor(subject="e3", username="e3")):  # re-parenting counts too
        await world.packages.delete(package.package.id, move_contents_to=None)
    assert await authored(engine_at_head, element_id) == ("e3", before + 2)


async def test_a_link_is_a_change_to_both_of_its_ends(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    one = await world.graph.create_element(element_type=E.NODE, name="a")
    two = await world.graph.create_element(element_type=E.NODE, name="b")

    with acting_as(an_editor(subject="linker", username="linker")):
        await world.graph.connect(
            relationship_type=R.ASSOCIATION, source_id=one.id, target_id=two.id
        )

    assert await authored(engine_at_head, one.id) == ("linker", 2)
    assert await authored(engine_at_head, two.id) == ("linker", 2)


async def test_overlapping_batches_in_opposite_orders_do_not_deadlock(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    first = await world.packages.create(name="One")
    second = await world.packages.create(name="Two")
    ids = [
        (await world.graph.create_element(element_type=E.NODE, name=f"n{i}")).id for i in range(12)
    ]

    results = await asyncio.gather(
        world.packages.move_elements(ids, first.package.id),
        world.packages.move_elements(list(reversed(ids)), second.package.id),
        *(world.graph.update_element(i, description="x") for i in ids[::3]),
        *(world.graph.update_element(i, description="y") for i in reversed(ids[1::3])),
        return_exceptions=True,
    )

    assert [r for r in results if isinstance(r, BaseException)] == []


async def test_the_sql_twin_of_the_rule_agrees_with_it_for_every_state(
    engine_at_head: AsyncEngine,
) -> None:
    """The restoration reads its protection as a set; it must be the rule of every other door."""
    from ea.domain.approval import ApprovalState, WriteKind, guard_element_write
    from ea.repositories.baseline_store import approved_or_locked

    world = World(engine_at_head)
    cases = [("draft", False), ("in_review", False), ("approved", False), ("approved", True)]
    ids = {}
    for index, (state, locked) in enumerate(cases):
        element = await world.graph.create_element(element_type=E.NODE, name=f"n{index}")
        await put_in_state(engine_at_head, element.id, state, locked=locked)
        ids[element.id] = (state, locked)

    sessions = create_session_factory(engine_at_head)
    async with sessions() as session:
        protected = await approved_or_locked(session)
    for element_id, (state, locked) in ids.items():
        element = await world.graph.get_element(element_id)
        assert element.approval_state is ApprovalState(state)
        try:
            guard_element_write(element, WriteKind.RESTORE)
            refused = False
        except ElementLockedError:
            refused = True
        assert (element_id in protected) is refused, (state, locked)
