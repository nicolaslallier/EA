"""PATCH /relationships/{id}: a link can say something else, not connect something else."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.archimate import AccessType
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    ElementLockedError,
    ElementNotFoundError,
    InvalidLifespanError,
    NotAuthenticatedError,
    NotAuthorisedError,
)
from ea.domain.model import Relationship
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryRepository, a_reader

pytestmark = pytest.mark.asyncio


async def a_link(
    service: ArchitectureService, relationship_type: R = R.ASSOCIATION, **fields: object
) -> Relationship:
    source_type = E.APPLICATION_COMPONENT if relationship_type is R.ACCESS else E.NODE
    target_type = E.DATA_OBJECT if relationship_type is R.ACCESS else E.NODE
    if relationship_type is R.ACCESS:
        source_type = E.APPLICATION_FUNCTION
    source = await service.create_element(element_type=source_type, name="a")
    target = await service.create_element(element_type=target_type, name="b")
    return await service.connect(
        relationship_type=relationship_type,
        source_id=source.id,
        target_id=target.id,
        **fields,  # type: ignore[arg-type]
    )


def put_in_state(
    repository: InMemoryRepository, element_id: UUID, state: S, *, locked: bool = False
) -> None:
    repository.elements[element_id] = replace(
        repository.elements[element_id],
        approval_state=state,
        locked=locked,
        approved_by_sub="approver-1" if state is S.APPROVED else None,
        approved_at=FIXED_NOW if state is S.APPROVED else None,
    )


class TestWhatChanges:
    async def test_the_name_the_direction_and_the_properties_change_together(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        link = await a_link(service, properties={"k": "old"})

        updated = await service.update_relationship(
            link.id, name=" owns ", directed=True, properties={"k": "new"}
        )

        assert (updated.name, updated.directed, dict(updated.properties)) == (
            "owns",
            True,
            {"k": "new"},
        )
        assert repository.relationships[link.id] == updated
        # What it connects never moves.
        assert (updated.relationship_type, updated.source_id, updated.target_id) == (
            link.relationship_type,
            link.source_id,
            link.target_id,
        )

    async def test_an_omitted_field_keeps_its_value_and_properties_are_replaced_whole(
        self, service: ArchitectureService
    ) -> None:
        link = await a_link(service, name="keep", properties={"a": "1", "b": "2"})

        updated = await service.update_relationship(link.id, properties={"c": "3"})

        assert updated.name == "keep"
        assert dict(updated.properties) == {"c": "3"}  # replaced, not merged

    async def test_the_access_type_of_an_access_link_can_change(
        self, service: ArchitectureService
    ) -> None:
        link = await a_link(service, R.ACCESS)

        updated = await service.update_relationship(link.id, access_type=AccessType.WRITE)

        assert updated.access_type is AccessType.WRITE

    async def test_an_access_type_on_another_relationship_is_refused(
        self, service: ArchitectureService
    ) -> None:
        link = await a_link(service)

        with pytest.raises(ValueError, match="access_type"):
            await service.update_relationship(link.id, access_type=AccessType.READ)

    async def test_a_malformed_validity_date_is_refused(self, service: ArchitectureService) -> None:
        link = await a_link(service)

        with pytest.raises(InvalidLifespanError):
            await service.update_relationship(link.id, properties={"valid_from": "2027-06"})

    async def test_an_unknown_link_is_a_not_found(self, service: ArchitectureService) -> None:
        with pytest.raises(ElementNotFoundError):
            await service.update_relationship(uuid4(), name="x")


class TestTheTrail:
    async def test_one_event_carries_the_link_before_and_after(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        link = await a_link(service, name="before")
        audit_log.events.clear()

        await service.update_relationship(link.id, name="after")

        (event,) = audit_log.events
        assert event.action is AuditAction.RELATIONSHIP_UPDATED
        assert event.before is not None and event.before["name"] == "before"
        assert event.after is not None and event.after["name"] == "after"
        assert event.entity_id == link.id

    async def test_changing_nothing_writes_nothing(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        link = await a_link(service, name="same")
        put_in_state(repository, link.source_id, S.APPROVED)
        audit_log.events.clear()

        await service.update_relationship(link.id, name="same")

        assert audit_log.events == []
        assert repository.elements[link.source_id].approval_state is S.APPROVED


class TestTheEndsAreReviewed:
    async def test_an_approved_end_goes_back_to_review_and_says_so(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        link = await a_link(service)
        put_in_state(repository, link.source_id, S.APPROVED)
        audit_log.events.clear()

        await service.update_relationship(link.id, name="renamed")

        assert repository.elements[link.source_id].approval_state is S.IN_REVIEW
        assert [e.action for e in audit_log.events] == [
            AuditAction.RELATIONSHIP_UPDATED,
            AuditAction.ELEMENT_UPDATED,
        ]

    async def test_a_locked_end_refuses_without_a_trace(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        link = await a_link(service, name="before")
        put_in_state(repository, link.target_id, S.APPROVED, locked=True)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.update_relationship(link.id, name="after")

        assert audit_log.events == []
        assert repository.relationships[link.id].name == "before"


class TestWho:
    async def test_a_reader_cannot_change_a_link(self, service: ArchitectureService) -> None:
        link = await a_link(service)

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await service.update_relationship(link.id, name="x")

    async def test_nobody_is_refused(
        self, service: ArchitectureService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await service.update_relationship(uuid4(), name="x")
