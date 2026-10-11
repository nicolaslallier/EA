"""Diagram templates: a typed skeleton with no element in it — pure rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagram_objects import DiagramEdge, DiagramObject, DiagramScene, ObjectKind
from ea.domain.diagram_templates import (
    Binding,
    body_to_json,
    parse_body,
    plan_instantiation,
    template_from_scene,
)
from ea.domain.diagrams import DiagramNode
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def raw(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
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
                "text": "Toujours un hôte dédié",
                "x": 320,
                "y": 10,
                "width": 180,
                "height": 60,
                "colour": "#fff4b0",
                "parent_key": None,
                "anchor_key": "host",
            }
        ],
        "links": [{"source_key": "host", "target_key": "app", "relationship_type": "realization"}],
    }
    return {**body, **overrides}


def an_element(element_type: ElementType, name: str = "X") -> Element:
    return Element.create(element_type=element_type, name=name, now=NOW)


class TestTheBody:
    def test_a_well_formed_body_round_trips(self) -> None:
        body = parse_body(raw())

        assert [s.key for s in body.slots] == ["app", "host"]
        assert body_to_json(body) == raw()

    def test_it_holds_no_element_only_typed_slots(self) -> None:
        document = body_to_json(parse_body(raw()))

        assert "element_id" not in str(document)

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"version": 2}, "version"),
            (
                {
                    "slots": [
                        {
                            "key": "a",
                            "element_type": "spaceship",
                            "label": "x",
                            "x": 0,
                            "y": 0,
                            "width": 100,
                            "height": 40,
                            "stereotype": None,
                            "parent_key": None,
                        }
                    ]
                },
                "spaceship",
            ),
        ],
    )
    def test_an_unknown_version_or_type_is_refused(
        self, change: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            parse_body(raw(**change))

    def test_a_link_the_metamodel_does_not_allow_between_the_two_types_is_refused(self) -> None:
        links = [{"source_key": "host", "target_key": "app", "relationship_type": "specialization"}]

        with pytest.raises(ValueError, match="not allowed"):
            parse_body(raw(links=links))

    def test_a_link_to_a_key_that_is_not_a_slot_is_refused(self) -> None:
        links = [{"source_key": "host", "target_key": "zone", "relationship_type": "association"}]

        with pytest.raises(ValueError, match="slot"):
            parse_body(raw(links=links))

    def test_keys_are_unique_across_slots_groups_and_notes(self) -> None:
        notes = [
            {
                "key": "app",
                "text": "x",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 50,
                "colour": None,
                "parent_key": None,
                "anchor_key": None,
            }
        ]

        with pytest.raises(ValueError, match="twice"):
            parse_body(raw(notes=notes))

    def test_a_parent_must_be_a_group_without_a_loop_and_within_the_depth(self) -> None:
        slots = raw()["slots"]
        slots[0]["parent_key"] = "host"
        with pytest.raises(ValueError, match="group"):
            parse_body(raw(slots=slots))

        loop = [
            {
                "key": "g1",
                "text": "1",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "colour": None,
                "parent_key": "g2",
            },
            {
                "key": "g2",
                "text": "2",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "colour": None,
                "parent_key": "g1",
            },
        ]
        with pytest.raises(ValueError, match="loop"):
            parse_body(raw(groups=loop, slots=[], notes=[], links=[]))

        deep = [
            {
                "key": f"g{i}",
                "text": str(i),
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 100,
                "colour": None,
                "parent_key": f"g{i - 1}" if i else None,
            }
            for i in range(6)
        ]
        with pytest.raises(ValueError, match="deep"):
            parse_body(raw(groups=deep, slots=[], notes=[], links=[]))

    def test_a_note_is_anchored_on_a_slot_or_nothing(self) -> None:
        notes = raw()["notes"]
        notes[0]["anchor_key"] = "zone"

        with pytest.raises(ValueError, match="anchored"):
            parse_body(raw(notes=notes))

    def test_an_empty_group_is_valid(self) -> None:
        assert parse_body(raw(slots=[], notes=[], links=[])).groups[0].key == "zone"


class TestFromADiagram:
    def test_each_element_becomes_a_slot_of_the_same_type_and_the_rest_is_kept_by_kind(
        self,
    ) -> None:
        app, host = (
            an_element(ElementType.APPLICATION_COMPONENT, "Facturation"),
            an_element(ElementType.NODE, "srv-01"),
        )
        link = Relationship.between(RelationshipType.REALIZATION, host, app, now=NOW)
        group = DiagramObject(uuid4(), ObjectKind.GROUP, 0, 0, 300, 120, text="Zone")
        note = DiagramObject(
            uuid4(), ObjectKind.NOTE, 320, 10, 180, 60, text="Voir", anchor_element_id=host.id
        )
        scene = DiagramScene(
            nodes=(
                DiagramNode(app.id, 10, 10, parent_object_id=group.id),
                DiagramNode(host.id, 10, 200),
            ),
            objects=(group, note),
            edges=(DiagramEdge(link.id, ()),),
        )

        body = template_from_scene(
            scene, {app.id: app, host.id: host}, [link], {app.id: "database"}
        )

        assert sorted((s.element_type, s.stereotype) for s in body.slots) == sorted(
            [(ElementType.APPLICATION_COMPONENT, "database"), (ElementType.NODE, None)]
        )
        slot_of = {s.label: s for s in body.slots}
        assert slot_of["Facturation"].parent_key == body.groups[0].key
        assert body.notes[0].anchor_key == slot_of["srv-01"].key
        assert [(link.relationship_type) for link in body.links] == [RelationshipType.REALIZATION]
        assert str(app.id) not in str(body_to_json(body))  # no element, only types

    def test_a_link_whose_end_is_not_on_the_diagram_is_not_kept(self) -> None:
        app, host = an_element(ElementType.APPLICATION_COMPONENT), an_element(ElementType.NODE)
        link = Relationship.between(RelationshipType.REALIZATION, host, app, now=NOW)

        body = template_from_scene(
            DiagramScene(nodes=(DiagramNode(app.id, 0, 0),)), {app.id: app}, [link], {}
        )

        assert body.links == ()


class TestPlanningAnInstantiation:
    def setup_method(self) -> None:
        self.body = parse_body(raw())

    def test_slots_are_bound_created_or_left_out(self) -> None:
        app = an_element(ElementType.APPLICATION_COMPONENT)

        plan = plan_instantiation(
            self.body, [Binding("app", element_id=app.id)], {app.id: app.element_type}, {}
        )
        creating = plan_instantiation(self.body, [Binding("host", create_name="srv-02")], {}, {})

        assert [(s.key, i) for s, i in plan.bound] == [("app", app.id)]
        assert plan.omitted == ("host",)
        assert [(s.key, n) for s, n in creating.to_create] == [("host", "srv-02")]

    def test_an_element_of_another_type_is_refused_naming_both(self) -> None:
        node = an_element(ElementType.NODE)

        with pytest.raises(ValueError, match="application_component.*node"):
            plan_instantiation(
                self.body, [Binding("app", element_id=node.id)], {node.id: node.element_type}, {}
            )

    def test_a_stereotype_the_slot_requires_must_be_carried(self) -> None:
        slots = raw()["slots"]
        slots[0]["stereotype"] = "database"
        body = parse_body(raw(slots=slots))
        app = an_element(ElementType.APPLICATION_COMPONENT)

        with pytest.raises(ValueError, match="database"):
            plan_instantiation(
                body,
                [Binding("app", element_id=app.id)],
                {app.id: app.element_type},
                {app.id: frozenset()},
            )
        plan_instantiation(
            body,
            [Binding("app", element_id=app.id)],
            {app.id: app.element_type},
            {app.id: frozenset({"database"})},
        )

    @pytest.mark.parametrize(
        "bindings",
        [
            [Binding("ghost", create_name="x")],
            [Binding("app", create_name="a"), Binding("app", create_name="b")],
            [Binding("app", element_id=uuid4(), create_name="both")],
            [Binding("app", create_name="   ")],
        ],
    )
    def test_malformed_bindings_are_refused(self, bindings: list[Binding]) -> None:
        with pytest.raises(ValueError):
            plan_instantiation(self.body, bindings, {}, {})

    def test_one_element_cannot_fill_two_slots(self) -> None:
        body = parse_body(
            raw(
                slots=[
                    {**raw()["slots"][0], "key": "a1", "parent_key": None},
                    {**raw()["slots"][0], "key": "a2", "parent_key": None},
                ],
                links=[],
                notes=[],
                groups=[],
            )
        )
        app = an_element(ElementType.APPLICATION_COMPONENT)

        with pytest.raises(ValueError, match="twice"):
            plan_instantiation(
                body,
                [Binding("a1", element_id=app.id), Binding("a2", element_id=app.id)],
                {app.id: app.element_type},
                {},
            )


class TestHostileBodies:
    def test_a_non_finite_or_absurd_geometry_is_refused(self) -> None:
        slots = raw()["slots"]
        slots[0]["x"] = float("inf")
        with pytest.raises(ValueError, match="finite"):
            parse_body(raw(slots=slots))
        slots = raw()["slots"]
        slots[0]["width"] = float("nan")
        with pytest.raises(ValueError, match="finite"):
            parse_body(raw(slots=slots))

    def test_text_that_would_be_markup_stays_text_and_a_bad_colour_is_refused(self) -> None:
        notes = raw()["notes"]
        notes[0]["text"] = "<script>alert(1)</script>"
        assert parse_body(raw(notes=notes)).notes[0].text == "<script>alert(1)</script>"
        notes[0]["colour"] = 'red" onload="x'
        with pytest.raises(ValueError, match="colour"):
            parse_body(raw(notes=notes))

    def test_a_body_that_is_not_a_document_is_a_value_error_not_a_crash(self) -> None:
        for hostile in (
            {},
            {"version": 1},
            {"version": 1, "slots": 3, "groups": [], "notes": [], "links": []},
        ):
            with pytest.raises(ValueError):
                parse_body(hostile)
