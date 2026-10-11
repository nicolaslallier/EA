from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagram_objects import DiagramObject, ObjectKind
from ea.domain.diagram_templates import Binding
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import DuplicateDiagramError, NotAuthorisedError
from ea.domain.ports import ElementFilter
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.templates import TemplateService
from tests.conftest import InMemoryAuditLog, a_reader, lay_out

BODY: dict[str, Any] = {
    "version": 1,
    "slots": [
        {
            "key": "app",
            "element_type": "application_component",
            "label": "Application",
            "x": 10,
            "y": 10,
            "width": 132,
            "height": 46,
            "stereotype": None,
            "parent_key": "zone",
        },
        {
            "key": "host",
            "element_type": "node",
            "label": "Hôte",
            "x": 10,
            "y": 200,
            "width": 132,
            "height": 46,
            "stereotype": None,
            "parent_key": None,
        },
    ],
    "groups": [
        {
            "key": "zone",
            "text": "Zone",
            "x": 0,
            "y": 0,
            "width": 300,
            "height": 120,
            "colour": None,
            "parent_key": None,
        }
    ],
    "notes": [
        {
            "key": "why",
            "text": "Un hôte dédié",
            "x": 320,
            "y": 10,
            "width": 180,
            "height": 60,
            "colour": None,
            "parent_key": None,
            "anchor_key": "host",
        }
    ],
    "links": [{"source_key": "host", "target_key": "app", "relationship_type": "realization"}],
}


async def count(service: ArchitectureService) -> int:
    return len(await service.list_elements(ElementFilter()))


@pytest.mark.asyncio
class TestInstantiating:
    async def test_it_creates_the_diagram_and_the_elements_asked_for_and_no_relationship_by_default(
        self, template_service: TemplateService, service: ArchitectureService
    ) -> None:
        template = await template_service.create_template(name="Application + hôte", body=BODY)

        detail = await template_service.instantiate(
            template.id,
            name="Facturation",
            bindings=[
                Binding("app", create_name="Facturation"),
                Binding("host", create_name="srv-01"),
            ],
        )

        assert {e.name for e in detail.graph.elements} == {"Facturation", "srv-01"}
        assert detail.graph.relationships == ()
        assert await service.list_relationships() == ()
        assert [o.kind for o in detail.objects].count(ObjectKind.GROUP) == 1
        app = next(n for n in detail.nodes if n.parent_object_id is not None)
        assert app.parent_object_id == next(
            o.id for o in detail.objects if o.kind is ObjectKind.GROUP
        )
        note = next(o for o in detail.objects if o.kind is ObjectKind.NOTE)
        assert note.anchor_element_id is not None

    async def test_relationships_are_created_only_on_request_through_the_architecture_service(
        self, template_service: TemplateService, service: ArchitectureService
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)

        detail = await template_service.instantiate(
            template.id,
            name="Avec liens",
            bindings=[Binding("app", create_name="A"), Binding("host", create_name="H")],
            create_relationships=True,
        )

        assert [r.relationship_type for r in detail.graph.relationships] == [
            RelationshipType.REALIZATION
        ]

    async def test_a_relationship_that_already_exists_is_not_created_twice(
        self, template_service: TemplateService, service: ArchitectureService
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)
        app = await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name="A")
        host = await service.create_element(element_type=ElementType.NODE, name="H")
        await service.connect(
            relationship_type=RelationshipType.REALIZATION, source_id=host.id, target_id=app.id
        )

        await template_service.instantiate(
            template.id,
            name="V",
            bindings=[Binding("app", element_id=app.id), Binding("host", element_id=host.id)],
            create_relationships=True,
        )

        assert len(await service.list_relationships()) == 1

    async def test_an_omitted_slot_leaves_its_note_unanchored_and_its_links_out(
        self, template_service: TemplateService
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)

        detail = await template_service.instantiate(
            template.id,
            name="Sans hôte",
            bindings=[Binding("app", create_name="A")],
            create_relationships=True,
        )

        assert len(detail.nodes) == 1
        assert detail.graph.relationships == ()
        assert (
            next(o for o in detail.objects if o.kind is ObjectKind.NOTE).anchor_element_id is None
        )

    async def test_a_binding_of_the_wrong_type_is_refused_and_nothing_is_written(
        self,
        template_service: TemplateService,
        service: ArchitectureService,
        diagram_service: DiagramService,
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)
        node = await service.create_element(element_type=ElementType.NODE, name="H")
        before = await count(service)

        with pytest.raises(ValueError, match="application_component"):
            await template_service.instantiate(
                template.id,
                name="Mauvais",
                bindings=[Binding("app", element_id=node.id), Binding("host", create_name="X")],
            )

        assert await count(service) == before
        assert await diagram_service.list_diagrams() == ()

    async def test_a_taken_diagram_name_fails_before_any_element_is_created(
        self,
        template_service: TemplateService,
        service: ArchitectureService,
        diagram_service: DiagramService,
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)
        await diagram_service.create(name="Pris")
        before = await count(service)

        with pytest.raises(DuplicateDiagramError):
            await template_service.instantiate(
                template.id, name="Pris", bindings=[Binding("app", create_name="A")]
            )

        assert await count(service) == before

    async def test_a_reader_cannot_instantiate(self, template_service: TemplateService) -> None:
        template = await template_service.create_template(name="T", body=BODY)

        with acting_as(a_reader()):
            assert [t.name for t in await template_service.list_templates()] == ["T"]
            with pytest.raises(NotAuthorisedError):
                await template_service.instantiate(template.id, name="X", bindings=[])


@pytest.mark.asyncio
class TestAsTemplate:
    async def test_a_diagram_becomes_a_skeleton_that_instantiates_to_the_same_shape(
        self,
        template_service: TemplateService,
        service: ArchitectureService,
        diagram_service: DiagramService,
    ) -> None:
        app = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        host = await service.create_element(element_type=ElementType.NODE, name="srv-01")
        await service.connect(
            relationship_type=RelationshipType.REALIZATION, source_id=host.id, target_id=app.id
        )
        diagram = await diagram_service.create(name="Source")
        note = DiagramObject(
            uuid4(), ObjectKind.NOTE, 300, 10, 150, 60, text="Voir", anchor_element_id=host.id
        )
        await lay_out(
            diagram_service,
            diagram.id,
            [DiagramNode(app.id, 10, 10), DiagramNode(host.id, 10, 200)],
            [note],
        )

        template = await template_service.create_from_diagram(diagram.id, name="Modèle")
        detail = await template_service.instantiate(
            template.id,
            name="Copie",
            bindings=[Binding("s1", create_name="A2"), Binding("s2", create_name="H2")],
            create_relationships=True,
        )

        assert sorted(s.element_type.value for s in template.body.slots) == [
            "application_component",
            "node",
        ]
        assert len(detail.nodes) == 2 and len(detail.graph.relationships) == 1
        assert [o.text for o in detail.objects] == ["Voir"]
        assert {e.name for e in detail.graph.elements} == {"A2", "H2"}


@pytest.mark.asyncio
class TestTheTrailAndTheLimits:
    async def test_a_template_write_leaves_one_event_with_counts_and_no_note_text(
        self, template_service: TemplateService, audit_log: InMemoryAuditLog
    ) -> None:
        template = await template_service.create_template(name="T", body=BODY)
        await template_service.update_template(template.id, name="U")
        await template_service.delete_template(template.id)

        assert [e.action.value for e in audit_log.events] == [
            "template.created",
            "template.updated",
            "template.deleted",
        ]
        assert audit_log.events[0].after is not None and audit_log.events[0].after["slots"] == 2
        assert "dédié" not in str(audit_log.events)

    async def test_a_slot_that_requires_a_stereotype_cannot_be_filled_by_creating(
        self, template_service: TemplateService
    ) -> None:
        slots = [{**BODY["slots"][0], "stereotype": "database", "parent_key": None}]
        body = {**BODY, "slots": slots, "groups": [], "notes": [], "links": []}
        template = await template_service.create_template(name="S", body=body)

        with pytest.raises(ValueError, match="stereotype"):
            await template_service.instantiate(
                template.id, name="V", bindings=[Binding("app", create_name="A")]
            )
