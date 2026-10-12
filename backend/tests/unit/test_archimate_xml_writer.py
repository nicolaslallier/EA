"""The Exchange XML writer: deterministic, well-formed, and safe for any name."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

import pytest
from defusedxml import ElementTree as DefusedET

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.exchange import (
    NAMESPACE,
    ExchangeBox,
    ExchangeElement,
    ExchangeFolder,
    ExchangeRelationship,
    ExchangeView,
)
from ea.formats.archimate_xml import buffered, render_model

pytestmark = pytest.mark.asyncio
NS = {"x": NAMESPACE}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


async def aiter_of[T](items: Iterable[T]) -> AsyncIterator[T]:
    for item in items:
        yield item


async def render(
    elements: Iterable[ExchangeElement] = (),
    relationships: Iterable[ExchangeRelationship] = (),
    name: str = "Modèle",
) -> str:
    parts = render_model(name, aiter_of(elements), aiter_of(relationships))
    return "".join([part async for part in parts])


def actor(identifier: str = "a-1", name: str = "Alice", **fields: object) -> ExchangeElement:
    return ExchangeElement(identifier, ElementType.BUSINESS_ACTOR, name, **fields)  # type: ignore[arg-type]


async def test_the_output_is_well_formed_and_in_the_exchange_namespace() -> None:
    xml = await render([actor()], name="EA")

    root = DefusedET.fromstring(xml)
    assert root.tag == f"{{{NAMESPACE}}}model"
    assert root.find("x:name", NS).text == "EA"  # type: ignore[union-attr]


async def test_an_element_carries_its_identifier_and_its_pascal_case_type() -> None:
    root = DefusedET.fromstring(await render([actor()]))

    element = root.find("x:elements/x:element", NS)
    assert element is not None
    assert element.get("identifier") == "a-1"
    assert element.get(XSI_TYPE) == "BusinessActor"
    assert element.find("x:name", NS).text == "Alice"  # type: ignore[union-attr]


async def test_the_description_travels_as_a_declared_property() -> None:
    root = DefusedET.fromstring(await render([actor(description="Who pays")]))

    ref = root.find("x:elements/x:element/x:properties/x:property", NS)
    assert ref is not None and ref.get("propertyDefinitionRef") == "propid-ea.description"
    definition = root.find("x:propertyDefinitions/x:propertyDefinition", NS)
    assert definition is not None
    assert definition.get("identifier") == "propid-ea.description"
    assert definition.get("type") == "string"
    assert definition.find("x:name", NS).text == "ea.description"  # type: ignore[union-attr]


async def test_properties_are_sorted_by_key_so_the_output_is_deterministic() -> None:
    first = await render([actor(properties={"zeta": "1", "alpha": "2"})])
    second = await render([actor(properties={"alpha": "2", "zeta": "1"})])

    assert first == second
    assert first.index("propid-alpha") < first.index("propid-zeta")


async def test_special_characters_are_escaped_and_control_characters_dropped() -> None:
    xml = await render([actor(name='A & B <"c"> \x01\x0b ok')])

    root = DefusedET.fromstring(xml)  # would raise on an illegal character
    assert root.find("x:elements/x:element/x:name", NS).text == 'A & B <"c">  ok'  # type: ignore[union-attr]


async def test_relationship_attributes_follow_the_type() -> None:
    a, b = actor("a"), actor("b", "Bob")
    links = [
        ExchangeRelationship("r1", RelationshipType.ACCESS, "a", "b", access_type=AccessType.READ),
        ExchangeRelationship("r2", RelationshipType.ASSOCIATION, "a", "b", directed=True),
        ExchangeRelationship(
            "r3", RelationshipType.INFLUENCE, "a", "b", properties={"modifier": "++", "x": "y"}
        ),
        ExchangeRelationship("r4", RelationshipType.SERVING, "a", "b", name="serves"),
    ]
    root = DefusedET.fromstring(await render([a, b], links))

    by_id = {r.get("identifier"): r for r in root.findall("x:relationships/x:relationship", NS)}
    assert by_id["r1"].get("accessType") == "Read"
    assert by_id["r2"].get("isDirected") == "true"
    assert by_id["r3"].get("modifier") == "++"
    assert by_id["r3"].find("x:properties/x:property", NS) is not None  # `x` only
    assert by_id["r4"].get(XSI_TYPE) == "Serving"
    assert by_id["r4"].find("x:name", NS).text == "serves"  # type: ignore[union-attr]
    assert by_id["r4"].get("accessType") is None and by_id["r4"].get("isDirected") is None


async def test_an_empty_model_has_no_empty_sections() -> None:
    root = DefusedET.fromstring(await render())

    for section in ("elements", "relationships", "propertyDefinitions"):
        assert root.find(f"x:{section}", NS) is None


async def test_the_sections_come_in_the_order_the_schema_wants() -> None:
    root = DefusedET.fromstring(
        await render(
            [actor(properties={"k": "v"}), actor("b", "Bob")],
            [ExchangeRelationship("r", RelationshipType.ASSOCIATION, "a-1", "b")],
        )
    )

    assert [child.tag.split("}")[1] for child in root] == [
        "name",
        "elements",
        "relationships",
        "propertyDefinitions",
    ]


async def test_buffering_never_splits_a_character_and_loses_nothing() -> None:
    text = "é€😀" * 50

    async def parts() -> AsyncIterator[str]:
        for char in text:
            yield char

    chunks = [chunk async for chunk in buffered(parts(), limit=16)]

    assert b"".join(chunks).decode("utf-8") == text
    assert len(chunks) > 1


async def test_a_name_full_of_markup_survives_the_round_trip() -> None:
    nasty = ']]> <b>&amp; it\'s "q" \t tab \r\n crlf'
    root = DefusedET.fromstring(
        await render([actor(name=nasty, description=nasty, documentation=nasty)])
    )

    element = root.find("x:elements/x:element", NS)
    assert element is not None
    assert element.find("x:name", NS).text == nasty  # type: ignore[union-attr]
    assert element.find("x:documentation", NS).text == nasty  # type: ignore[union-attr]
    value = element.find("x:properties/x:property/x:value", NS)
    assert value is not None and value.text == nasty


async def test_an_attribute_value_keeps_its_line_breaks() -> None:
    link = ExchangeRelationship(
        "r", RelationshipType.INFLUENCE, "a-1", "b", properties={"modifier": 'a\nb"c'}
    )
    root = DefusedET.fromstring(await render([actor(), actor("b", "Bob")], [link]))

    assert root.find("x:relationships/x:relationship", NS).get("modifier") == 'a\nb"c'  # type: ignore[union-attr]


async def test_every_character_xml_forbids_is_dropped_and_reported() -> None:
    reported: list[tuple[str, int]] = []
    bad = "a\x00b\x1fc￾d￿e\ud800f"  # NUL, a control, two non-characters, a lone surrogate
    parts = render_model(
        "M",
        aiter_of([actor(name=bad)]),
        aiter_of([]),
        on_dropped=lambda identifier, count: reported.append((identifier, count)),
    )
    root = DefusedET.fromstring("".join([part async for part in parts]))

    assert root.find("x:elements/x:element/x:name", NS).text == "abcdef"  # type: ignore[union-attr]
    assert reported == [("a-1", 5)]


async def test_clean_text_is_not_reported() -> None:
    reported: list[tuple[str, int]] = []
    parts = render_model(
        "M", aiter_of([actor()]), aiter_of([]), on_dropped=lambda i, c: reported.append((i, c))
    )
    [part async for part in parts]

    assert reported == []


async def render_with_views(views: Iterable[ExchangeView]) -> str:
    parts = render_model("M", aiter_of([actor("a-1")]), aiter_of([]), views=aiter_of(views))
    return "".join([part async for part in parts])


async def test_a_view_lists_its_boxes_with_integer_geometry() -> None:
    view = ExchangeView(
        "view-Overview", "Overview", (ExchangeBox("a-1", 10.4, 20.6, 120.0, 55.0),), "Main picture"
    )

    root = DefusedET.fromstring(await render_with_views([view]))

    node_view = root.find("x:views/x:diagrams/x:view", NS)
    assert node_view is not None and node_view.get("identifier") == "view-Overview"
    assert node_view.get(XSI_TYPE) == "Diagram"
    assert node_view.find("x:name", NS).text == "Overview"  # type: ignore[union-attr]
    assert node_view.find("x:documentation", NS).text == "Main picture"  # type: ignore[union-attr]
    box = node_view.find("x:node", NS)
    assert box is not None
    assert (box.get("identifier"), box.get("elementRef"), box.get(XSI_TYPE)) == (
        "view-Overview-a-1",
        "a-1",
        "Element",
    )
    assert (box.get("x"), box.get("y"), box.get("w"), box.get("h")) == ("10", "21", "120", "55")
    assert node_view.find("x:connection", NS) is None


async def test_views_come_last_and_a_model_without_views_has_no_views_section() -> None:
    with_views = DefusedET.fromstring(await render_with_views([ExchangeView("view-V", "V", ())]))
    without = DefusedET.fromstring(await render([actor()]))

    assert [c.tag.split("}")[1] for c in with_views][-1] == "views"
    assert without.find("x:views", NS) is None


async def test_the_text_of_a_view_is_escaped_and_cleaned_like_any_other() -> None:
    view = ExchangeView("view-V", 'a <b> & "c"\x01', (), "d]]>e")

    root = DefusedET.fromstring(await render_with_views([view]))

    found = root.find("x:views/x:diagrams/x:view", NS)
    assert found is not None
    assert found.find("x:name", NS).text == 'a <b> & "c"'  # type: ignore[union-attr]


async def test_organizations_nest_folders_and_reference_elements() -> None:
    inner = ExchangeFolder("Sub", ("a-2",), ())
    root = ExchangeFolder("", ("a-1",), (ExchangeFolder("Pkg", ("a-3",), (inner,)),))
    parts = render_model(
        "M",
        aiter_of([actor("a-1"), actor("a-2", "B"), actor("a-3", "C")]),
        aiter_of([]),
        organizations=lambda: root,
    )

    xml = DefusedET.fromstring("".join([p async for p in parts]))

    assert [c.tag.split("}")[1] for c in xml] == ["name", "elements", "organizations"]
    folder = xml.find("x:organizations/x:item[x:label]", NS)
    assert folder is not None and folder.find("x:label", NS).text == "Pkg"  # type: ignore[union-attr]
    assert folder.find("x:item[@identifierRef='a-3']", NS) is not None
    assert folder.find("x:item/x:label", NS).text == "Sub"  # type: ignore[union-attr]
    assert xml.find("x:organizations/x:item[@identifierRef='a-1']", NS) is not None


async def test_an_empty_organization_writes_no_section() -> None:
    parts = render_model(
        "M", aiter_of([actor()]), aiter_of([]), organizations=lambda: ExchangeFolder("", (), ())
    )

    xml = DefusedET.fromstring("".join([p async for p in parts]))

    assert xml.find("x:organizations", NS) is None


async def test_an_empty_package_is_still_a_folder() -> None:
    parts = render_model(
        "M",
        aiter_of([actor()]),
        aiter_of([]),
        organizations=lambda: ExchangeFolder("", (), (ExchangeFolder("Empty", (), ()),)),
    )

    xml = DefusedET.fromstring("".join([p async for p in parts]))

    assert xml.find("x:organizations/x:item/x:label", NS).text == "Empty"  # type: ignore[union-attr]


async def test_sections_keep_the_order_of_the_schema() -> None:
    parts = render_model(
        "M",
        aiter_of([actor(properties={"k": "v"})]),
        aiter_of([]),
        views=aiter_of([ExchangeView("view-V", "V", ())]),
        organizations=lambda: ExchangeFolder("", ("a-1",), (ExchangeFolder("F", (), ()),)),
    )

    xml = DefusedET.fromstring("".join([p async for p in parts]))

    assert [c.tag.split("}")[1] for c in xml] == [
        "name",
        "elements",
        "organizations",
        "propertyDefinitions",
        "views",
    ]


def test_build_organization_roots_the_packages_whose_parent_is_not_listed() -> None:
    from uuid import uuid4

    from ea.domain.exchange import PackageNode, build_organization

    top, sub, orphan = uuid4(), uuid4(), uuid4()
    packages = [
        PackageNode(sub, top, "Sub"),
        PackageNode(top, None, "Top"),
        PackageNode(orphan, uuid4(), "Orphan"),  # its parent is outside the scope
    ]

    root = build_organization(packages, {None: ["u"], top: ["b", "a"], sub: ["c"]})

    assert root.items == ("u",)
    assert [f.label for f in root.children] == ["Orphan", "Top"]
    top_folder = root.children[1]
    assert top_folder.items == ("a", "b")
    assert [(f.label, f.items) for f in top_folder.children] == [("Sub", ("c",))]


async def test_an_or_junction_is_written_as_one_and_read_back_with_its_kind() -> None:
    from ea.formats.archimate_xml import parse_model

    junction = ExchangeElement(
        "j-1", ElementType.JUNCTION, "Gate", properties={"junction_type": "or"}
    )
    link = ExchangeRelationship("r", RelationshipType.FLOW, "a-1", "j-1")
    text = await render([actor(), junction], [link])

    assert 'type="OrJunction"' in text
    model = parse_model(text.encode())
    back = next(e for e in model.elements if e.identifier == "j-1")
    assert (back.element_type, back.name, back.properties["junction_type"]) == (
        ElementType.JUNCTION,
        "Gate",
        "or",
    )
    assert [r.target for r in model.relationships] == ["j-1"]
