"""What changed the catalogue, and when.

Reads are covered by the access line the middleware writes (docs/adr/0021).
Writes are not the same thing: "POST /elements -> 201" says an element was
created and not *which*, and `/mcp` reaches the very same use cases without
passing through HTTP at all. So the audit line is written where both adapters
meet — in the service — and it carries the id, which is the only thing that
lets a line here be joined to the element it made.
"""

from __future__ import annotations

import logging

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.ports import ElementFilter
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.ipam import IpamService

AUDIT = "ea.services"


def audit(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith(AUDIT)]


def only(caplog: pytest.LogCaptureFixture, action: str) -> logging.LogRecord:
    """The one audit line for that action — `action` is why it is a field."""
    (line,) = [record for record in audit(caplog) if getattr(record, "action", None) == action]
    return line


pytestmark = pytest.mark.asyncio


class TestTheGraph:
    async def test_a_created_element_is_named_by_its_id(
        self, service: ArchitectureService, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            element = await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
            )

        line = only(caplog, "created")
        assert line.element_id == str(element.id)  # type: ignore[attr-defined]
        assert "Facturation" in line.getMessage()

    async def test_a_read_writes_nothing(
        self, service: ArchitectureService, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An audit trail that logs reads is an audit trail nobody can read."""
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await service.get_element(element.id)
            await service.list_elements(criteria=ElementFilter())

        assert audit(caplog) == []

    async def test_an_update_and_a_deletion_are_both_recorded(
        self, service: ArchitectureService, caplog: pytest.LogCaptureFixture
    ) -> None:
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await service.update_element(element.id, name="Facturation v2")
            await service.delete_element(element.id)

        assert [line.action for line in audit(caplog)] == ["updated", "deleted"]  # type: ignore[attr-defined]

    async def test_a_link_says_which_two_elements_and_which_type(
        self, service: ArchitectureService, caplog: pytest.LogCaptureFixture
    ) -> None:
        source = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        target = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Recouvrement"
        )
        caplog.clear()
        with caplog.at_level(logging.INFO):
            link = await service.connect(
                source_id=source.id,
                target_id=target.id,
                relationship_type=RelationshipType.SERVING,
            )

        line = only(caplog, "connected")
        assert line.relationship_id == str(link.id)  # type: ignore[attr-defined]
        assert line.source_id == str(source.id)  # type: ignore[attr-defined]
        assert line.target_id == str(target.id)  # type: ignore[attr-defined]


class TestTheDocuments:
    async def test_an_attached_file_is_recorded_by_name_and_size(
        self,
        document_service: DocumentService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        element = await service.create_element(element_type=ElementType.NODE, name="srv-app-01")
        caplog.clear()
        with caplog.at_level(logging.INFO):
            document = await document_service.attach_text(
                element.id, filename="runbook.md", content="# Runbook\n\nRedémarrer."
            )

        line = only(caplog, "attached")
        assert line.document_id == str(document.id)  # type: ignore[attr-defined]
        assert "runbook.md" in line.getMessage()

    async def test_the_text_itself_never_reaches_the_log(
        self,
        document_service: DocumentService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A corpus copied line by line into a log file is a second corpus."""
        element = await service.create_element(element_type=ElementType.NODE, name="srv-app-01")
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            await document_service.attach_text(
                element.id, filename="runbook.md", content="# Secret\n\nmot de passe interne"
            )

        assert all("mot de passe interne" not in line.getMessage() for line in caplog.records)


class TestTheAddresses:
    async def test_an_allocation_says_what_was_handed_out(
        self,
        ipam: IpamService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Handing out an address twice is the failure this trail exists for."""
        network = await ipam.declare_network(name="LAN", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=ElementType.NODE, name="srv-app-01")
        caplog.clear()
        with caplog.at_level(logging.INFO):
            assignment = await ipam.allocate_next(network.element.id, host.id)

        line = only(caplog, "address_assigned")
        assert str(assignment.address) in line.getMessage()
        assert line.element_id == str(host.id)  # type: ignore[attr-defined]
