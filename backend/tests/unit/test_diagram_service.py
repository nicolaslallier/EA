"""The use cases over saved diagrams, against in-memory stores.

A diagram is an ArchiMate *view* (docs/adr/0031): it records which elements are
shown and where, and owns no fact. The rules tested here are the ones that
follow from that — a layout may only place elements that exist, a reading skips
what has since been deleted, and deleting an element takes its boxes with it.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ea.api.schemas import MAX_DIAGRAM_NODES, DiagramCreate, DiagramLayout
from ea.domain.archimate import ElementType
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    DiagramNotFoundError,
    DuplicateDiagramError,
    UnknownLayoutElementError,
)
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from tests.conftest import FIXED_NOW, InMemoryDiagrams


async def an_element(service: ArchitectureService, name: str = "Facturation") -> Element:
    return await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)


@pytest.mark.asyncio
class TestCreatingAndListing:
    async def test_a_new_diagram_is_named_and_empty(self, diagram_service: DiagramService) -> None:
        diagram = await diagram_service.create(name="  Paysage applicatif ", description="v1")

        assert diagram.name == "Paysage applicatif"
        assert diagram.description == "v1"
        assert diagram.node_count == 0
        assert diagram.created_at == FIXED_NOW

    async def test_a_blank_name_is_refused(self, diagram_service: DiagramService) -> None:
        with pytest.raises(ValueError, match="blank"):
            await diagram_service.create(name="   ")

    async def test_two_diagrams_cannot_share_a_name(self, diagram_service: DiagramService) -> None:
        await diagram_service.create(name="Paysage")

        with pytest.raises(DuplicateDiagramError):
            await diagram_service.create(name="Paysage")

    async def test_the_listing_is_sorted_by_name_and_counts_the_boxes(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        element = await an_element(service)
        second = await diagram_service.create(name="Zeta")
        await diagram_service.create(name="Alpha")
        await diagram_service.replace_layout(second.id, [DiagramNode(element.id, 10, 20)])

        listed = await diagram_service.list_diagrams()

        assert [(d.name, d.node_count) for d in listed] == [("Alpha", 0), ("Zeta", 1)]


@pytest.mark.asyncio
class TestReading:
    async def test_a_diagram_comes_with_its_elements_and_the_links_between_them(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        api = await service.create_element(element_type=ElementType.APPLICATION_SERVICE, name="API")
        process = await service.create_element(
            element_type=ElementType.BUSINESS_PROCESS, name="Commande"
        )
        outsider = await service.create_element(
            element_type=ElementType.BUSINESS_PROCESS, name="Relance"
        )
        link = await service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )
        await service.connect(relationship_type=R.SERVING, source_id=api.id, target_id=outsider.id)
        diagram = await diagram_service.create(name="Vente")
        await diagram_service.replace_layout(
            diagram.id, [DiagramNode(api.id, 0, 0), DiagramNode(process.id, 200, 0)]
        )

        detail = await diagram_service.open(diagram.id)

        assert {e.id for e in detail.graph.elements} == {api.id, process.id}
        assert [r.id for r in detail.graph.relationships] == [link.id]
        assert {n.element_id for n in detail.nodes} == {api.id, process.id}

    async def test_a_node_whose_element_is_gone_is_skipped(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        diagrams: InMemoryDiagrams,
    ) -> None:
        """The second line of defence, for an orphan the cascade failed to remove."""
        element = await an_element(service)
        diagram = await diagram_service.create(name="Vente")
        diagrams.nodes[diagram.id] = (DiagramNode(element.id, 0, 0), DiagramNode(uuid4(), 5, 5))

        detail = await diagram_service.open(diagram.id)

        assert [n.element_id for n in detail.nodes] == [element.id]

    async def test_an_unknown_diagram_is_not_found(self, diagram_service: DiagramService) -> None:
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.open(uuid4())


@pytest.mark.asyncio
class TestUpdatingAndDeleting:
    async def test_a_rename_keeps_the_description(self, diagram_service: DiagramService) -> None:
        diagram = await diagram_service.create(name="Vente", description="garde")

        renamed = await diagram_service.update(diagram.id, name="Ventes")

        assert (renamed.name, renamed.description) == ("Ventes", "garde")

    async def test_a_rename_onto_a_taken_name_is_refused(
        self, diagram_service: DiagramService
    ) -> None:
        await diagram_service.create(name="Vente")
        other = await diagram_service.create(name="Achat")

        with pytest.raises(DuplicateDiagramError):
            await diagram_service.update(other.id, name="Vente")

    async def test_updating_an_unknown_diagram_is_not_found(
        self, diagram_service: DiagramService
    ) -> None:
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.update(uuid4(), name="X")

    async def test_a_deleted_diagram_is_gone_and_a_second_delete_says_so(
        self, diagram_service: DiagramService
    ) -> None:
        diagram = await diagram_service.create(name="Vente")

        await diagram_service.delete(diagram.id)

        with pytest.raises(DiagramNotFoundError):
            await diagram_service.delete(diagram.id)


@pytest.mark.asyncio
class TestTheLayout:
    async def test_a_layout_replaces_the_previous_one_whole(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        first = await an_element(service, "A")
        second = await an_element(service, "B")
        diagram = await diagram_service.create(name="Vente")
        await diagram_service.replace_layout(diagram.id, [DiagramNode(first.id, 0, 0)])

        await diagram_service.replace_layout(diagram.id, [DiagramNode(second.id, 1.5, -2)])

        detail = await diagram_service.open(diagram.id)
        assert detail.nodes == (DiagramNode(second.id, 1.5, -2),)

    async def test_a_layout_placing_an_element_that_does_not_exist_is_refused(
        self, diagram_service: DiagramService
    ) -> None:
        diagram = await diagram_service.create(name="Vente")
        ghost = uuid4()

        with pytest.raises(UnknownLayoutElementError, match=str(ghost)):
            await diagram_service.replace_layout(diagram.id, [DiagramNode(ghost, 0, 0)])

    async def test_a_layout_placing_one_element_twice_is_refused(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        element = await an_element(service)
        diagram = await diagram_service.create(name="Vente")

        with pytest.raises(ValueError, match="twice"):
            await diagram_service.replace_layout(
                diagram.id, [DiagramNode(element.id, 0, 0), DiagramNode(element.id, 9, 9)]
            )

    async def test_a_layout_for_an_unknown_diagram_is_not_found(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(DiagramNotFoundError):
            await diagram_service.replace_layout(uuid4(), [DiagramNode(element.id, 0, 0)])


@pytest.mark.asyncio
class TestTheAuditTrail:
    async def test_every_write_logs_one_line_naming_the_diagram(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        element = await an_element(service)
        with caplog.at_level(logging.INFO, logger="ea.services"):
            diagram = await diagram_service.create(name="Vente")
            await diagram_service.update(diagram.id, description="d")
            await diagram_service.replace_layout(diagram.id, [DiagramNode(element.id, 0, 0)])
            await diagram_service.open(diagram.id)
            await diagram_service.delete(diagram.id)

        actions = [
            (record.action, record.diagram_id)  # type: ignore[attr-defined]
            for record in caplog.records
            if str(getattr(record, "action", "")).startswith("diagram_")
        ]
        assert actions == [
            ("diagram_created", str(diagram.id)),
            ("diagram_updated", str(diagram.id)),
            ("diagram_layout_replaced", str(diagram.id)),
            ("diagram_deleted", str(diagram.id)),
        ]


class TestTheBounds:
    """Declared once in `api/schemas.py`, and stopped before any service runs."""

    def test_a_layout_holds_at_most_the_declared_number_of_boxes(self) -> None:
        node = {"element_id": str(uuid4()), "x": 0, "y": 0}

        DiagramLayout.model_validate({"nodes": [node] * MAX_DIAGRAM_NODES})
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate({"nodes": [node] * (MAX_DIAGRAM_NODES + 1)})

    @pytest.mark.parametrize("x", [100_001, -100_001, "nan", "inf"])
    def test_a_coordinate_outside_the_canvas_is_refused(self, x: object) -> None:
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate({"nodes": [{"element_id": str(uuid4()), "x": x, "y": 0}]})

    def test_an_unknown_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            DiagramCreate.model_validate({"name": "Vente", "colour": "red"})

    def test_a_name_is_at_most_two_hundred_characters(self) -> None:
        with pytest.raises(ValidationError):
            DiagramCreate.model_validate({"name": "x" * 201})
