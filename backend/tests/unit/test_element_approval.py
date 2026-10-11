"""Moving an element through review: roles, states, four eyes, one event each (docs/adr/0046)."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.approval import Transition as Tr
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    ApprovalStaleError,
    ElementNotFoundError,
    InvalidTransitionError,
    NotAuthenticatedError,
    NotAuthorisedError,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemoryRepository,
    a_reader,
    an_approver,
)

pytestmark = pytest.mark.asyncio


def reviewing(
    repository: InMemoryRepository, *, separate_duties: bool = True
) -> ArchitectureService:
    return ArchitectureService(repository, clock=lambda: FIXED_NOW, separate_duties=separate_duties)


async def test_the_whole_life_of_an_element_leaves_one_event_per_step(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")  # editor-1
    started = len(audit_log.events)

    await service.transition_element(element.id, Tr.SUBMIT)  # the editor
    with acting_as(an_approver()):
        approved = await service.transition_element(
            element.id, Tr.APPROVE, expected_revision=element.revision
        )
        await service.transition_element(element.id, Tr.LOCK)
        await service.transition_element(element.id, Tr.UNLOCK)
        reopened = await service.transition_element(element.id, Tr.REOPEN)

    assert approved.approved_by_sub == "approver-1"
    assert reopened.approval_state is S.DRAFT and reopened.approved_at is None
    steps = audit_log.events[started:]
    assert [e.action for e in steps] == [
        AuditAction.ELEMENT_SUBMITTED,
        AuditAction.ELEMENT_APPROVED,
        AuditAction.ELEMENT_LOCKED,
        AuditAction.ELEMENT_UNLOCKED,
        AuditAction.ELEMENT_REOPENED,
    ]
    assert steps[1].before is not None and steps[1].after is not None
    assert steps[1].after["approval_state"] == "approved"


async def test_an_editor_cannot_approve_and_a_reader_cannot_even_submit(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog, caplog: pytest.LogCaptureFixture
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    await service.transition_element(element.id, Tr.SUBMIT)
    audit_log.events.clear()

    with caplog.at_level(logging.WARNING), pytest.raises(NotAuthorisedError):
        await service.transition_element(element.id, Tr.APPROVE, expected_revision=1)
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await service.transition_element(element.id, Tr.SUBMIT)

    assert audit_log.events == []
    assert [r for r in caplog.records if getattr(r, "action", "") == "transition_refused"] == []


async def test_a_transition_the_state_forbids_is_refused_without_an_event_but_logged(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog, caplog: pytest.LogCaptureFixture
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    audit_log.events.clear()

    with acting_as(an_approver()):
        with caplog.at_level(logging.WARNING), pytest.raises(InvalidTransitionError):
            await service.transition_element(element.id, Tr.APPROVE, expected_revision=1)
        with pytest.raises(InvalidTransitionError):
            await service.transition_element(element.id, Tr.LOCK)
    await service.transition_element(element.id, Tr.SUBMIT)
    with pytest.raises(InvalidTransitionError):
        await service.transition_element(element.id, Tr.SUBMIT)  # already submitted

    assert [e.action for e in audit_log.events] == [AuditAction.ELEMENT_SUBMITTED]
    refused = [r for r in caplog.records if getattr(r, "action", "") == "transition_refused"]
    assert refused and refused[0].element_id == str(element.id)  # type: ignore[attr-defined]


async def test_the_one_who_changed_an_element_last_cannot_approve_it(
    repository: InMemoryRepository,
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    with acting_as(an_approver()):  # approver-1 makes the last change
        changed = await service.update_element(element.id, description="tuned")
        await service.transition_element(element.id, Tr.SUBMIT)
        with pytest.raises(NotAuthorisedError, match="separation"):
            await service.transition_element(
                element.id, Tr.APPROVE, expected_revision=changed.revision
            )
    with acting_as(an_approver(subject="approver-2", username="other")):
        approved = await service.transition_element(
            element.id, Tr.APPROVE, expected_revision=changed.revision
        )

    assert approved.approved_by_sub == "approver-2"


async def test_the_creator_counts_as_the_last_author_when_nothing_changed_since(
    repository: InMemoryRepository,
) -> None:
    service = reviewing(repository)
    with acting_as(an_approver()):
        element = await service.create_element(element_type=E.NODE, name="db-01")
        await service.transition_element(element.id, Tr.SUBMIT)
        with pytest.raises(NotAuthorisedError, match="separation"):
            await service.transition_element(
                element.id, Tr.APPROVE, expected_revision=element.revision
            )


async def test_with_no_known_author_the_approval_is_refused(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    await service.transition_element(element.id, Tr.SUBMIT)
    audit_log.events.clear()  # the journal says nothing about who last changed it

    with acting_as(an_approver()), pytest.raises(NotAuthorisedError, match="unknown"):
        await service.transition_element(element.id, Tr.APPROVE, expected_revision=1)


async def test_separation_can_be_switched_off_explicitly_for_a_one_person_repository(
    repository: InMemoryRepository,
) -> None:
    service = reviewing(repository, separate_duties=False)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    with acting_as(an_approver()):
        changed = await service.update_element(element.id, description="tuned")
        await service.transition_element(element.id, Tr.SUBMIT)

        approved = await service.transition_element(
            element.id, Tr.APPROVE, expected_revision=changed.revision
        )

    assert approved.approval_state is S.APPROVED


async def test_approving_a_revision_that_changed_since_it_was_read_is_refused(
    repository: InMemoryRepository,
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    await service.transition_element(element.id, Tr.SUBMIT)
    read = (await service.get_element(element.id)).revision
    await service.update_element(element.id, description="changed after the approver read it")

    with acting_as(an_approver()):
        with pytest.raises(ApprovalStaleError):
            await service.transition_element(element.id, Tr.APPROVE, expected_revision=read)
        with pytest.raises(ApprovalStaleError):
            await service.transition_element(element.id, Tr.APPROVE)  # none given
        fresh = (await service.get_element(element.id)).revision
        approved = await service.transition_element(element.id, Tr.APPROVE, expected_revision=fresh)

    assert approved.approval_state is S.APPROVED


async def test_a_change_after_the_approval_puts_the_element_back_in_review(
    repository: InMemoryRepository,
) -> None:
    service = reviewing(repository)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    await service.transition_element(element.id, Tr.SUBMIT)
    with acting_as(an_approver()):
        await service.transition_element(element.id, Tr.APPROVE, expected_revision=1)
        changed = await service.update_element(element.id, description="after")

    assert changed.approval_state is S.IN_REVIEW and changed.approved_by_sub is None


async def test_an_unknown_element_is_a_not_found(repository: InMemoryRepository) -> None:
    with pytest.raises(ElementNotFoundError):
        await reviewing(repository).transition_element(uuid4(), Tr.SUBMIT)


@pytest.mark.usefixtures("nobody_calling")
async def test_nobody_calling_is_refused(repository: InMemoryRepository) -> None:
    with pytest.raises(NotAuthenticatedError):
        await reviewing(repository).transition_element(uuid4(), Tr.SUBMIT)
