"""The scene use cases: revision, groups, bends — against in-memory stores."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagram_objects import DiagramEdge, DiagramObject, ObjectKind, Point
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import DiagramRevisionConflictError, NotAuthorisedError
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from tests.conftest import a_reader, lay_out


async def an_element(service: ArchitectureService, name: str) -> object:
    return await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)


def a_group(**overrides: object) -> DiagramObject:
    fields: dict[str, object] = {
        "id": uuid4(),
        "kind": ObjectKind.GROUP,
        "x": 0.0,
        "y": 0.0,
        "width": 300.0,
        "height": 200.0,
        "text": "Domaine",
    }
    return DiagramObject(**{**fields, **overrides})  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestRevision:
    async def test_every_save_moves_the_revision_on_by_one(
        self, diagram_service: DiagramService
    ) -> None:
        diagram = await diagram_service.create(name="Vente")
        assert diagram.revision == 0

        first = await diagram_service.replace_layout(diagram.id, revision=0, nodes=[])
        second = await diagram_service.replace_layout(diagram.id, revision=1, nodes=[])

        assert (first, second) == (1, 2)
        assert (await diagram_service.get(diagram.id)).revision == 2

    async def test_a_stale_revision_is_refused_and_the_winner_is_kept(
        self, diagram_service: DiagramService, service: ArchitectureService
    ) -> None:
        winner = await an_element(service, "Gagnant")
        loser = await an_element(service, "Perdant")
        diagram = await diagram_service.create(name="Vente")
        await diagram_service.replace_layout(
            diagram.id,
            revision=0,
            nodes=[DiagramNode(winner.id, 0, 0)],  # type: ignore[attr-defined]
        )

        with pytest.raises(DiagramRevisionConflictError, match="revision 1"):
            await diagram_service.replace_layout(
                diagram.id,
                revision=0,
                nodes=[DiagramNode(loser.id, 0, 0)],  # type: ignore[attr-defined]
            )

        opened = await diagram_service.open(diagram.id)
        assert [node.element_id for node in opened.nodes] == [winner.id]  # type: ignore[attr-defined]
        assert opened.diagram.revision == 1

    async def test_a_reader_cannot_save_a_scene(self, diagram_service: DiagramService) -> None:
        diagram = await diagram_service.create(name="Vente")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await diagram_service.replace_layout(diagram.id, revision=0, nodes=[])


@pytest.mark.asyncio
class TestTheScene:
    async def test_groups_notes_and_bends_come_back_as_saved(
        self, diagram_service: DiagramService, service: ArchitectureService
    ) -> None:
        source = await an_element(service, "A")
        target = await an_element(service, "B")
        link = await service.connect(
            relationship_type=RelationshipType.ASSOCIATION,
            source_id=source.id,  # type: ignore[attr-defined]
            target_id=target.id,  # type: ignore[attr-defined]
        )
        diagram = await diagram_service.create(name="Vente")
        group = a_group()
        note = a_group(
            id=uuid4(), kind=ObjectKind.NOTE, text="Voir ADR", anchor_element_id=source.id
        )  # type: ignore[attr-defined]
        edge = DiagramEdge(link.id, (Point(50, 80), Point(90, 40)))

        await lay_out(
            diagram_service,
            diagram.id,
            [
                DiagramNode(source.id, 10, 10, parent_object_id=group.id),  # type: ignore[attr-defined]
                DiagramNode(target.id, 400, 10),  # type: ignore[attr-defined]
            ],
            [group, note],
            [edge],
        )
        opened = await diagram_service.open(diagram.id)

        assert {obj.id for obj in opened.objects} == {group.id, note.id}
        assert opened.edges == (edge,)
        assert (
            opened.nodes[0].parent_object_id == group.id
            or opened.nodes[1].parent_object_id == group.id
        )

    async def test_removing_a_group_with_its_children_lifted_keeps_them_where_they_are_put(
        self, diagram_service: DiagramService, service: ArchitectureService
    ) -> None:
        element = await an_element(service, "A")
        diagram = await diagram_service.create(name="Vente")
        outer = a_group()
        inner = a_group(parent_id=outer.id)
        await lay_out(
            diagram_service,
            diagram.id,
            [DiagramNode(element.id, 5, 5, parent_object_id=inner.id)],  # type: ignore[attr-defined]
            [outer, inner],
        )

        # The editor lifts the box to the grandparent when it removes `inner`.
        await lay_out(
            diagram_service,
            diagram.id,
            [DiagramNode(element.id, 5, 5, parent_object_id=outer.id)],  # type: ignore[attr-defined]
            [outer],
        )
        opened = await diagram_service.open(diagram.id)

        assert [obj.id for obj in opened.objects] == [outer.id]
        assert opened.nodes[0].parent_object_id == outer.id
        assert len(opened.graph.elements) == 1

    async def test_a_route_for_a_link_that_is_not_between_two_placed_elements_is_refused(
        self, diagram_service: DiagramService, service: ArchitectureService
    ) -> None:
        source = await an_element(service, "A")
        target = await an_element(service, "B")
        link = await service.connect(
            relationship_type=RelationshipType.ASSOCIATION,
            source_id=source.id,  # type: ignore[attr-defined]
            target_id=target.id,  # type: ignore[attr-defined]
        )
        diagram = await diagram_service.create(name="Vente")

        with pytest.raises(ValueError, match="between two elements"):
            await lay_out(
                diagram_service,
                diagram.id,
                [DiagramNode(source.id, 0, 0)],  # type: ignore[attr-defined]
                edges=[DiagramEdge(link.id, ())],
            )

    async def test_a_group_loop_is_refused_before_anything_is_stored(
        self, diagram_service: DiagramService
    ) -> None:
        diagram = await diagram_service.create(name="Vente")
        first, second = a_group(), a_group()
        first = a_group(id=first.id, parent_id=second.id)
        second = a_group(id=second.id, parent_id=first.id)

        with pytest.raises(ValueError, match="loop"):
            await lay_out(diagram_service, diagram.id, [], [first, second])

        assert (await diagram_service.open(diagram.id)).objects == ()
