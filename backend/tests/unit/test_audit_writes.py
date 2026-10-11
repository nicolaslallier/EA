"""Every write use case leaves exactly one event; every refusal leaves none."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    ElementNotFoundError,
    IllegalRelationshipError,
    NotAuthorisedError,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, a_reader

pytestmark = pytest.mark.asyncio


class TestTheGraph:
    async def test_creating_an_element_says_who_when_and_what(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing", properties={"owner": "ops"}
        )

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_CREATED
        assert (event.actor_sub, event.actor_name, event.at) == ("editor-1", "editor", FIXED_NOW)
        assert event.entity_id == element.id
        assert event.before is None
        assert event.after is not None and event.after["properties"] == {"owner": "ops"}

    async def test_updating_records_the_element_before_and_after(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        await service.update_element(element.id, name="db-02", description="primary")

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_UPDATED
        assert {c.field for c in event.changes} == {"name", "description"}
        assert event.before is not None and event.before["name"] == "db-01"
        assert event.after is not None and event.after["name"] == "db-02"

    async def test_deleting_lists_what_the_foreign_keys_took_with_the_element(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        doomed = await service.create_element(element_type=E.NODE, name="db-01")
        other = await service.create_element(element_type=E.NODE, name="db-02")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=doomed.id, target_id=other.id
        )
        audit_log.events.clear()

        await service.delete_element(doomed.id)

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_DELETED
        assert event.after is None
        assert event.before is not None
        assert event.before["name"] == "db-01"
        assert event.before["cascaded"] == {
            "relationships": [{"id": str(link.id), "relationship_type": "association"}],
            "documents": [],
            "diagrams": [],
        }

    async def test_connecting_and_disconnecting_each_leave_one_event(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        left = await service.create_element(element_type=E.NODE, name="a")
        right = await service.create_element(element_type=E.NODE, name="b")
        audit_log.events.clear()

        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=left.id, target_id=right.id
        )
        await service.disconnect(link.id)

        created, deleted = audit_log.events
        assert (created.action, deleted.action) == (
            AuditAction.RELATIONSHIP_CREATED,
            AuditAction.RELATIONSHIP_DELETED,
        )
        assert created.after is not None and created.after["source_id"] == str(left.id)
        assert deleted.before is not None and deleted.before["id"] == str(link.id)

    async def test_a_refusal_leaves_no_event(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="h1", properties={"ip_address": "10.0.0.1", "vrf": "default"}
        )
        data = await service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
        function = await service.create_element(element_type=E.APPLICATION_FUNCTION, name="Inv")
        audit_log.events.clear()

        with pytest.raises(ElementNotFoundError):
            await service.update_element(uuid4(), name="x")
        with pytest.raises(ElementNotFoundError):
            await service.delete_element(uuid4())
        with pytest.raises(ElementNotFoundError):
            await service.disconnect(uuid4())
        with pytest.raises(AddressAlreadyAssignedError):
            await service.create_element(
                element_type=E.NODE,
                name="h2",
                properties={"ip_address": "10.0.0.1", "vrf": "default"},
            )
        with pytest.raises(IllegalRelationshipError):
            await service.connect(
                relationship_type=R.ACCESS, source_id=data.id, target_id=function.id
            )
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await service.create_element(element_type=E.NODE, name="nope")

        assert audit_log.events == []
