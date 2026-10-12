"""The rules of a scene: notes, groups and bends — pure, no store."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from ea.domain.diagram_objects import (
    MAX_GROUP_DEPTH,
    MAX_WAYPOINTS,
    DiagramEdge,
    DiagramObject,
    ObjectKind,
    Point,
    check_scene,
)
from ea.domain.diagrams import DiagramNode


def a_group(parent: UUID | None = None, **overrides: object) -> DiagramObject:
    fields: dict[str, object] = {
        "id": uuid4(),
        "kind": ObjectKind.GROUP,
        "x": 0.0,
        "y": 0.0,
        "width": 300.0,
        "height": 200.0,
        "text": "Domaine Paiement",
        "parent_id": parent,
    }
    return DiagramObject(**{**fields, **overrides})  # type: ignore[arg-type]


def a_note(parent: UUID | None = None, **overrides: object) -> DiagramObject:
    return a_group(parent, **{"kind": ObjectKind.NOTE, "text": "À revoir", **overrides})


def a_chain(length: int) -> list[DiagramObject]:
    """`length` groups, each inside the previous one."""
    chain: list[DiagramObject] = []
    for _ in range(length):
        chain.append(a_group(chain[-1].id if chain else None))
    return chain


def test_an_empty_scene_is_valid() -> None:
    scene = check_scene([], [], [])

    assert (scene.nodes, scene.objects, scene.edges) == ((), (), ())


def test_an_empty_group_is_valid() -> None:
    group = a_group()

    assert check_scene([], [group], []).objects == (group,)


def test_a_box_can_sit_in_a_group_and_a_group_in_a_group() -> None:
    outer = a_group()
    inner = a_group(outer.id)
    element = uuid4()

    scene = check_scene([DiagramNode(element, 5, 5, parent_object_id=inner.id)], [outer, inner], [])

    assert scene.nodes[0].parent_object_id == inner.id


def test_an_element_is_still_placed_once() -> None:
    element = uuid4()

    with pytest.raises(ValueError, match="placed twice"):
        check_scene([DiagramNode(element, 0, 0), DiagramNode(element, 9, 9)], [], [])


def test_two_objects_cannot_share_an_id() -> None:
    note = a_note()

    with pytest.raises(ValueError, match="twice"):
        check_scene([], [note, note], [])


def test_a_group_cannot_be_anchored_to_an_element() -> None:
    with pytest.raises(ValueError, match="group"):
        check_scene([], [a_group(anchor_element_id=uuid4())], [])


def test_a_note_is_anchored_to_an_element_that_is_on_the_diagram() -> None:
    element = uuid4()
    note = a_note(anchor_element_id=element)

    assert check_scene([DiagramNode(element, 0, 0)], [note], []).objects == (note,)
    with pytest.raises(ValueError, match="anchored"):
        check_scene([], [note], [])


def test_a_parent_must_exist_and_be_a_group() -> None:
    with pytest.raises(ValueError, match="no group"):
        check_scene([], [a_note(parent=uuid4())], [])
    note = a_note()
    with pytest.raises(ValueError, match="only be placed in a group"):
        check_scene([DiagramNode(uuid4(), 0, 0, parent_object_id=note.id)], [note], [])


def test_a_group_cannot_be_its_own_parent() -> None:
    group = a_group()
    looping = a_group(group.id, id=group.id)

    with pytest.raises(ValueError, match="loop"):
        check_scene([], [looping], [])


def test_two_groups_cannot_contain_each_other() -> None:
    first, second = a_group(), a_group()
    first = a_group(second.id, id=first.id)
    second = a_group(first.id, id=second.id)

    with pytest.raises(ValueError, match="loop"):
        check_scene([], [first, second], [])


def test_groups_nest_five_deep_and_no_deeper() -> None:
    chain = a_chain(MAX_GROUP_DEPTH)
    assert check_scene([], chain, []).objects == tuple(chain)

    with pytest.raises(ValueError, match="deep"):
        check_scene([], [*chain, a_group(chain[-1].id)], [])


def test_a_note_may_sit_in_the_deepest_group() -> None:
    chain = a_chain(MAX_GROUP_DEPTH)

    assert len(check_scene([], [*chain, a_note(chain[-1].id)], []).objects) == MAX_GROUP_DEPTH + 1


def test_a_link_is_routed_once_with_a_bounded_number_of_bends() -> None:
    relationship = uuid4()
    bends = tuple(Point(float(i), 0.0) for i in range(MAX_WAYPOINTS))

    assert check_scene([], [], [DiagramEdge(relationship, bends)]).edges[0].waypoints == bends
    with pytest.raises(ValueError, match="bends"):
        check_scene([], [], [DiagramEdge(relationship, (*bends, Point(0, 0)))])
    with pytest.raises(ValueError, match="routed twice"):
        check_scene([], [], [DiagramEdge(relationship), DiagramEdge(relationship)])


def test_a_note_longer_than_the_bound_is_refused() -> None:
    with pytest.raises(ValueError, match="characters"):
        check_scene([], [a_note(text="x" * 2001)], [])


def test_non_finite_numbers_are_refused() -> None:
    with pytest.raises(ValueError, match="finite"):
        check_scene([], [a_note(x=float("inf"))], [])
    with pytest.raises(ValueError, match="finite"):
        check_scene([], [], [DiagramEdge(uuid4(), (Point(float("nan"), 0.0),))])
