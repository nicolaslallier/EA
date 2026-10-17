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

from ea.api.schemas import (
    MAX_BOX_SIZE,
    MAX_DIAGRAM_EDGES,
    MAX_DIAGRAM_NODES,
    MAX_DIAGRAM_OBJECTS,
    MIN_BOX_SIZE,
    DiagramCreate,
    DiagramLayout,
)
from ea.domain.archimate import ElementType
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    DiagramLimitError,
    DiagramNotFoundError,
    DiagramRevisionConflictError,
    DuplicateDiagramError,
    PackageNotFoundError,
    ScopeTooLargeError,
    UnknownLayoutElementError,
)
from ea.domain.model import Element
from ea.domain.packages import Package
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from tests.conftest import FIXED_NOW, InMemoryDiagrams, InMemoryPackages, lay_out


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
        await lay_out(diagram_service, second.id, [DiagramNode(element.id, 10, 20)])

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
        await lay_out(
            diagram_service,
            diagram.id,
            [DiagramNode(api.id, 0, 0), DiagramNode(process.id, 200, 0)],
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
        await lay_out(diagram_service, diagram.id, [DiagramNode(first.id, 0, 0)])

        await lay_out(diagram_service, diagram.id, [DiagramNode(second.id, 1.5, -2)])

        detail = await diagram_service.open(diagram.id)
        assert detail.nodes == (DiagramNode(second.id, 1.5, -2),)

    async def test_a_layout_placing_an_element_that_does_not_exist_is_refused(
        self, diagram_service: DiagramService
    ) -> None:
        diagram = await diagram_service.create(name="Vente")
        ghost = uuid4()

        with pytest.raises(UnknownLayoutElementError, match=str(ghost)):
            await lay_out(diagram_service, diagram.id, [DiagramNode(ghost, 0, 0)])

    async def test_a_layout_placing_one_element_twice_is_refused(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        element = await an_element(service)
        diagram = await diagram_service.create(name="Vente")

        with pytest.raises(ValueError, match="twice"):
            await lay_out(
                diagram_service,
                diagram.id,
                [DiagramNode(element.id, 0, 0), DiagramNode(element.id, 9, 9)],
            )

    async def test_a_layout_for_an_unknown_diagram_is_not_found(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(DiagramNotFoundError):
            await lay_out(diagram_service, uuid4(), [DiagramNode(element.id, 0, 0)])

    async def test_a_stale_save_naming_an_element_deleted_since_is_a_conflict_not_a_422(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        """A 422 reads as permanent to the SPA; a stale revision must say "reload" first."""
        element = await an_element(service)
        diagram = await diagram_service.create(name="Vente")
        seen = await lay_out(diagram_service, diagram.id, [DiagramNode(element.id, 0, 0)])
        await lay_out(diagram_service, diagram.id, [])  # someone else moved it on
        await service.delete_element(element.id)

        with pytest.raises(DiagramRevisionConflictError):
            await diagram_service.replace_layout(
                diagram.id, revision=seen, nodes=[DiagramNode(element.id, 5, 5)]
            )

    async def test_a_diagram_deleted_between_the_read_and_the_write_is_not_found(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        diagrams: InMemoryDiagrams,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        element = await an_element(service)
        diagram = await diagram_service.create(name="Vente")

        async def gone(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(diagrams, "replace_layout", gone)

        with pytest.raises(DiagramNotFoundError):
            await lay_out(diagram_service, diagram.id, [DiagramNode(element.id, 0, 0)])


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
            await lay_out(diagram_service, diagram.id, [DiagramNode(element.id, 0, 0)])
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

    @staticmethod
    def body(
        nodes: list[dict[str, object]] | None = None,
        objects: list[dict[str, object]] | None = None,
        edges: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {"revision": 0, "nodes": nodes or [], "objects": objects or [], "edges": edges or []}

    @staticmethod
    def box(**overrides: object) -> dict[str, object]:
        return {
            "element_id": str(uuid4()),
            "x": 0,
            "y": 0,
            "width": 132,
            "height": 46,
            "parent_object_id": None,
            "z": 0,
            **overrides,
        }

    @staticmethod
    def note(**overrides: object) -> dict[str, object]:
        return {
            "id": str(uuid4()),
            "kind": "note",
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 50,
            "text": "",
            "colour": None,
            "z": 0,
            "parent_id": None,
            "anchor_element_id": None,
            **overrides,
        }

    def test_a_layout_holds_at_most_the_declared_number_of_boxes(self) -> None:
        node = self.box()

        DiagramLayout.model_validate(self.body([node] * MAX_DIAGRAM_NODES))
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(self.body([node] * (MAX_DIAGRAM_NODES + 1)))

    def test_a_scene_holds_at_most_the_declared_notes_groups_and_routes(self) -> None:
        DiagramLayout.model_validate(self.body(objects=[self.note()] * MAX_DIAGRAM_OBJECTS))
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(
                self.body(objects=[self.note()] * (MAX_DIAGRAM_OBJECTS + 1))
            )
        edge = {"relationship_id": str(uuid4()), "waypoints": []}
        DiagramLayout.model_validate(self.body(edges=[edge] * MAX_DIAGRAM_EDGES))
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(self.body(edges=[edge] * (MAX_DIAGRAM_EDGES + 1)))

    @pytest.mark.parametrize(
        "bad",
        [
            {"text": "x" * 2001},
            {"colour": 'red" onload="x'},
            {"colour": "#12345"},
            {"z": 10_001},
            {"x": float("inf")},
            {"width": 10},
        ],
    )
    def test_an_object_outside_its_bounds_is_refused(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(self.body(objects=[self.note(**bad)]))

    def test_a_route_has_at_most_twenty_bends_and_the_revision_is_not_negative(self) -> None:
        bends = [{"x": 0, "y": 0}] * 21
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(
                self.body(edges=[{"relationship_id": str(uuid4()), "waypoints": bends}])
            )
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate({**self.body(), "revision": -1})

    @pytest.mark.parametrize("x", [100_001, -100_001, "nan", "inf"])
    def test_a_coordinate_outside_the_canvas_is_refused(self, x: object) -> None:
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(self.body([self.box(x=x)]))

    @pytest.mark.parametrize("size", [MIN_BOX_SIZE - 1, MAX_BOX_SIZE + 1, "nan", "inf"])
    def test_a_box_size_outside_the_bounds_is_refused(self, size: object) -> None:
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(self.body([self.box(width=size)]))

    def test_a_box_states_its_size(self) -> None:
        """Required rather than defaulted, so the read and write schemas stay one."""
        with pytest.raises(ValidationError):
            DiagramLayout.model_validate(
                self.body(
                    [{"element_id": str(uuid4()), "x": 0, "y": 0, "parent_object_id": None, "z": 0}]
                )
            )

    def test_an_unknown_field_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            DiagramCreate.model_validate({"name": "Vente", "colour": "red"})

    def test_a_name_is_at_most_two_hundred_characters(self) -> None:
        with pytest.raises(ValidationError):
            DiagramCreate.model_validate({"name": "x" * 201})


async def a_package(
    packages: InMemoryPackages, name: str, parent: Package | None = None
) -> Package:
    return await packages.add(
        Package.create(name=name, parent_id=parent.id if parent else None, now=FIXED_NOW),
        audit=(),
    )


@pytest.mark.asyncio
class TestDiagramsInPackages:
    async def test_a_diagram_is_created_in_a_package(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")

        diagram = await diagram_service.create(name="Vente", package_id=finance.id)

        assert diagram.package_id == finance.id

    async def test_a_package_that_does_not_exist_is_a_not_found(
        self, diagram_service: DiagramService
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await diagram_service.create(name="Vente", package_id=uuid4())

    async def test_updating_leaves_the_package_unless_it_is_given(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")
        diagram = await diagram_service.create(name="Vente", package_id=finance.id)

        renamed = await diagram_service.update(diagram.id, name="Ventes")
        assert renamed.package_id == finance.id

        at_the_root = await diagram_service.update(diagram.id, package_id=None)
        assert at_the_root.package_id is None

        moved_again = await diagram_service.update(diagram.id, package_id=finance.id)
        assert moved_again.package_id == finance.id

    async def test_listing_by_package_takes_the_subpackages_unless_told_otherwise(
        self, diagram_service: DiagramService, packages: InMemoryPackages
    ) -> None:
        finance = await a_package(packages, "Finance")
        budget = await a_package(packages, "Budget", finance)
        await diagram_service.create(name="Racine")
        await diagram_service.create(name="Compta", package_id=finance.id)
        await diagram_service.create(name="Prévisions", package_id=budget.id)

        with_children = await diagram_service.list_diagrams(package_id=finance.id)
        alone = await diagram_service.list_diagrams(
            package_id=finance.id, include_subpackages=False
        )
        unfiled = await diagram_service.list_diagrams(root_only=True)

        assert [d.name for d in with_children] == ["Compta", "Prévisions"]
        assert [d.name for d in alone] == ["Compta"]
        assert [d.name for d in unfiled] == ["Racine"]

    async def test_asking_for_the_root_and_a_package_at_once_is_refused(
        self, diagram_service: DiagramService
    ) -> None:
        with pytest.raises(ValueError, match="both"):
            await diagram_service.list_diagrams(package_id=uuid4(), root_only=True)


@pytest.mark.asyncio
class TestTheNumberOfDiagramsIsBounded:
    async def test_a_diagram_beyond_the_cap_is_refused_and_a_list_never_exceeds_it(
        self, diagram_service: DiagramService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tests.conftest.MAX_DIAGRAMS", 3)
        for i in range(3):
            await diagram_service.create(name=f"D{i}")

        with pytest.raises(DiagramLimitError):
            await diagram_service.create(name="one too many")

        assert len(await diagram_service.list_diagrams()) == 3

    async def test_a_store_already_over_the_cap_is_refused_on_listing_not_cut_silently(
        self, diagram_service: DiagramService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for i in range(4):
            await diagram_service.create(name=f"D{i}")
        monkeypatch.setattr("ea.services.diagrams.MAX_DIAGRAMS", 3)

        with pytest.raises(ScopeTooLargeError):
            await diagram_service.list_diagrams()
