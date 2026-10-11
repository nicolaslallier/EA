"""What approval and locking forbid, and how a change sends an element back to review."""

from __future__ import annotations

import logging
from dataclasses import replace
from uuid import UUID

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.auth import APPROVER_ROLE, Caller
from ea.domain.errors import ElementLockedError, NotAuthorisedError
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryRepository, an_approver

pytestmark = pytest.mark.asyncio


def put_in_state(
    repository: InMemoryRepository, element_id: UUID, state: S, *, locked: bool = False
) -> None:
    """Set the review state directly: the transitions themselves are tested elsewhere."""
    approved = state is S.APPROVED
    repository.elements[element_id] = replace(
        repository.elements[element_id],
        approval_state=state,
        locked=locked,
        approved_by_sub="approver-1" if approved else None,
        approved_at=FIXED_NOW if approved else None,
    )


class TestUpdating:
    async def test_an_editor_cannot_change_an_approved_element(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.update_element(element.id, name="db-02")
        with pytest.raises(ElementLockedError):  # a no-op is refused as well
            await service.update_element(element.id, name="db-01")

        assert audit_log.events == []
        assert repository.elements[element.id].name == "db-01"

    async def test_an_approver_may_and_the_element_goes_back_to_review(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)
        audit_log.events.clear()

        with acting_as(an_approver()):
            updated = await service.update_element(element.id, description="moved")

        assert updated.approval_state is S.IN_REVIEW
        assert updated.approved_by_sub is None
        assert updated.revision == element.revision + 1
        (event,) = audit_log.events
        assert event.after is not None and event.after["approval_state"] == "in_review"

    async def test_an_approver_changing_nothing_does_not_unapprove(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)

        with acting_as(an_approver()):
            kept = await service.update_element(element.id, name="db-01")

        assert kept.approval_state is S.APPROVED and kept.revision == element.revision

    async def test_a_locked_element_refuses_even_the_approver(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED, locked=True)

        with acting_as(an_approver()), pytest.raises(ElementLockedError, match="locked"):
            await service.update_element(element.id, name="x")

    async def test_an_element_in_review_stays_editable_and_in_review(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.IN_REVIEW)

        updated = await service.update_element(element.id, name="db-02")

        assert updated.approval_state is S.IN_REVIEW

    async def test_an_approver_who_is_not_an_editor_cannot_write_at_all(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        approver_alone = Caller("approver-2", "approver-only", frozenset({APPROVER_ROLE}))

        with acting_as(approver_alone), pytest.raises(NotAuthorisedError):
            await service.update_element(element.id, name="x")

    async def test_a_refusal_is_logged_with_its_element_and_leaves_no_event(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)

        with caplog.at_level(logging.WARNING), pytest.raises(ElementLockedError):
            await service.update_element(element.id, name="x")

        (record,) = [r for r in caplog.records if getattr(r, "action", "") == "lock_refused"]
        assert record.element_id == str(element.id)  # type: ignore[attr-defined]


class TestDeleting:
    async def test_approved_and_locked_elements_resist(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        locked = await service.create_element(element_type=E.NODE, name="b")
        put_in_state(repository, approved.id, S.APPROVED)
        put_in_state(repository, locked.id, S.APPROVED, locked=True)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.delete_element(approved.id)
        with acting_as(an_approver()), pytest.raises(ElementLockedError):
            await service.delete_element(locked.id)
        with acting_as(an_approver()):
            await service.delete_element(approved.id)

        assert [e.action for e in audit_log.events] == [AuditAction.ELEMENT_DELETED]
        assert locked.id in repository.elements


class TestLinking:
    async def test_linking_an_approved_element_is_allowed_and_sends_it_back_to_review(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        put_in_state(repository, approved.id, S.APPROVED)
        audit_log.events.clear()

        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=approved.id, target_id=other.id
        )

        assert repository.elements[approved.id].approval_state is S.IN_REVIEW
        assert [e.action for e in audit_log.events] == [
            AuditAction.RELATIONSHIP_CREATED,
            AuditAction.ELEMENT_UPDATED,
        ]

    async def test_linking_or_unlinking_a_locked_element_is_refused_without_a_trace(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        locked = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=locked.id, target_id=other.id
        )
        put_in_state(repository, locked.id, S.APPROVED, locked=True)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.connect(
                relationship_type=R.ASSOCIATION, source_id=other.id, target_id=locked.id
            )
        with pytest.raises(ElementLockedError):
            await service.disconnect(link.id)

        assert audit_log.events == []
        assert link.id in repository.relationships

    async def test_unlinking_an_approved_element_also_sends_it_back_to_review(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=approved.id, target_id=other.id
        )
        put_in_state(repository, approved.id, S.APPROVED)

        await service.disconnect(link.id)

        assert repository.elements[approved.id].approval_state is S.IN_REVIEW

    async def test_a_self_link_reviews_its_one_element_once(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        node = await service.create_element(element_type=E.NODE, name="a")
        put_in_state(repository, node.id, S.APPROVED)
        audit_log.events.clear()

        await service.connect(relationship_type=R.ASSOCIATION, source_id=node.id, target_id=node.id)

        assert [e.action for e in audit_log.events].count(AuditAction.ELEMENT_UPDATED) == 1
