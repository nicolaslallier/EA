"""Every write use case leaves exactly one event; every refusal leaves none."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AUDIT_LISTED_IDS, AuditAction
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DiagramNotFoundError,
    DocumentNotFoundError,
    DuplicateDocumentError,
    ElementNotFoundError,
    IllegalRelationshipError,
    NotAuthorisedError,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from tests.conftest import FIXED_NOW, InMemoryAuditLog, a_reader, lay_out

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
            "relationships": {
                "count": 1,
                "items": [{"id": str(link.id), "relationship_type": "association"}],
            },
            "documents": {"count": 0, "ids": []},
            "diagrams": {"count": 0, "ids": []},
        }

    async def test_a_hub_deleted_lists_a_hundred_links_and_counts_them_all(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        hub = await service.create_element(element_type=E.NODE, name="hub")
        for index in range(AUDIT_LISTED_IDS + 20):
            spoke = await service.create_element(element_type=E.NODE, name=f"s{index}")
            await service.connect(
                relationship_type=R.ASSOCIATION, source_id=hub.id, target_id=spoke.id
            )
        audit_log.events.clear()

        await service.delete_element(hub.id)

        (event,) = audit_log.events
        assert event.before is not None
        cascaded = event.before["cascaded"]["relationships"]
        assert cascaded["count"] == AUDIT_LISTED_IDS + 20
        assert len(cascaded["items"]) == AUDIT_LISTED_IDS

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


class TestDocuments:
    async def test_attach_revise_discard_each_leave_one_event_without_the_text(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        document = await document_service.attach_text(
            element.id, filename="runbook.md", content="# Secret plan\n"
        )
        await document_service.revise_text(
            document.id, filename="runbook.md", content="# Other plan\n"
        )
        await document_service.discard(document.id)

        attached, revised, discarded = audit_log.events
        assert [e.action for e in audit_log.events] == [
            AuditAction.DOCUMENT_ATTACHED,
            AuditAction.DOCUMENT_REVISED,
            AuditAction.DOCUMENT_DISCARDED,
        ]
        assert "plan" not in str(audit_log.events)
        assert attached.after is not None and attached.after["sha256"]
        assert revised.before is not None and revised.after is not None
        assert revised.before["sha256"] != revised.after["sha256"]
        assert discarded.before is not None and discarded.after is None

    async def test_a_refused_document_write_leaves_no_event(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        await document_service.attach_text(element.id, filename="a.md", content="# A\n")
        audit_log.events.clear()

        with pytest.raises(DuplicateDocumentError):
            await document_service.attach_text(element.id, filename="a.md", content="# B\n")
        with pytest.raises(DocumentNotFoundError):
            await document_service.discard(uuid4())

        assert audit_log.events == []

    async def test_reindexing_is_not_a_change_of_content(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        await document_service.attach_text(element.id, filename="a.md", content="# A\n")
        audit_log.events.clear()

        await document_service.reindex_all()

        assert audit_log.events == []


class TestDiagrams:
    async def test_every_diagram_write_leaves_one_event(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        diagram = await diagram_service.create(name="Vue", description="")
        await diagram_service.update(diagram.id, name="Vue 2")
        await lay_out(diagram_service, diagram.id, [DiagramNode(element.id, 1, 2)])
        await diagram_service.delete(diagram.id)

        assert [e.action for e in audit_log.events] == [
            AuditAction.DIAGRAM_CREATED,
            AuditAction.DIAGRAM_UPDATED,
            AuditAction.DIAGRAM_LAYOUT_REPLACED,
            AuditAction.DIAGRAM_DELETED,
        ]
        layout = audit_log.events[2]
        assert layout.before == {"diagram_revision": 0, "nodes": 0, "objects": 0, "edges": 0}
        assert layout.after is not None
        assert (
            layout.after["diagram_revision"],
            layout.after["nodes"],
            layout.after["nodes_added"],
        ) == (
            1,
            1,
            1,
        )

    async def test_a_refused_diagram_write_leaves_no_event(
        self, diagram_service: DiagramService, audit_log: InMemoryAuditLog
    ) -> None:
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.delete(uuid4())
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.update(uuid4(), name="x")

        assert audit_log.events == []
