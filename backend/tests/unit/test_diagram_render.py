"""The one static drawing of a diagram: exact, deterministic, escaped."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import UTC, datetime

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.diagram_render import (
    LAYER_FILL,
    RELATIONSHIP_VERBS,
    render_svg,
    shorten,
    xml_safe,
)
from ea.domain.diagrams import Diagram, DiagramDetail, DiagramNode
from ea.domain.model import Element, Relationship
from ea.domain.ports import GraphView

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
FONT = 'font-family="DejaVu Sans, sans-serif"'
ARROW = (
    '<defs><marker id="ea-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M 0 0 L 10 5 L 0 10 z" fill="#4a4455"/></marker></defs>'
)


def an_element(name: str = "Facturation", kind: ElementType = ElementType.APPLICATION_COMPONENT):
    return Element.create(element_type=kind, name=name, now=NOW)


def a_link(source: Element, target: Element) -> Relationship:
    return Relationship.between(RelationshipType.ASSOCIATION, source, target, now=NOW)


def a_detail(
    placed: Sequence[tuple[Element, float, float, float, float]],
    links: Sequence[Relationship] = (),
    name: str = "Vente",
) -> DiagramDetail:
    return DiagramDetail(
        diagram=Diagram.create(name=name, now=NOW),
        nodes=tuple(DiagramNode(e.id, x, y, w, h) for e, x, y, w, h in placed),
        graph=GraphView(elements=tuple(e for e, *_ in placed), relationships=tuple(links)),
    )


def test_every_layer_has_a_fill_and_every_relationship_a_verb() -> None:
    assert set(LAYER_FILL) == set(Layer)
    assert set(RELATIONSHIP_VERBS) == set(RelationshipType)


def test_a_name_is_shortened_by_the_editors_rule() -> None:
    assert shorten("court", 132) == "court"
    assert shorten("x" * 40, 132) == "x" * 16 + "…"
    assert shorten("abcdef", 20) == "abc…"


def test_one_box_is_drawn_exactly_where_it_is_stored() -> None:
    facturation = an_element()

    svg = render_svg(a_detail([(facturation, 40, 40, 132, 46)]))

    assert svg == "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="800" '
            'height="600" role="img" aria-label="Vente">',
            "<title>Vente</title>",
            ARROW,
            f'<g class="node" data-element-id="{facturation.id}">',
            "<title>Facturation — Application Component</title>",
            '<rect x="40" y="40" width="132" height="46" rx="6" fill="#b5ffff" '
            'stroke="#24202b" stroke-opacity="0.35"/>',
            f'<text class="name" x="106" y="61" text-anchor="middle" {FONT} '
            'font-size="13" fill="#24202b">Facturation</text>',
            f'<text class="type" x="106" y="76" text-anchor="middle" {FONT} '
            'font-size="10" fill="#24202b">Application Comp…</text>',
            "</g>",
            "</svg>",
        ]
    )


def test_two_calls_give_the_same_string() -> None:
    detail = a_detail([(an_element(), 10, 10, 132, 46)])

    assert render_svg(detail) == render_svg(detail)


def test_a_link_is_trimmed_to_the_borders_and_labelled() -> None:
    left, right = an_element("A"), an_element("B")

    svg = render_svg(
        a_detail([(left, 0, 0, 100, 40), (right, 400, 0, 100, 40)], [a_link(left, right)])
    )

    assert '<line x1="100" y1="20" x2="400" y2="20"' in svg
    assert 'x="250" y="16"' in svg
    assert ">est associé à</text>" in svg


def test_overlapping_boxes_have_no_stroke() -> None:
    left, right = an_element("A"), an_element("B")

    svg = render_svg(
        a_detail([(left, 0, 0, 100, 40), (right, 50, 10, 100, 40)], [a_link(left, right)])
    )

    assert "<line" not in svg


def test_a_link_to_an_element_that_is_not_drawn_is_skipped() -> None:
    drawn, absent = an_element("A"), an_element("B")

    svg = render_svg(a_detail([(drawn, 0, 0, 100, 40)], [a_link(drawn, absent)]))

    assert "<line" not in svg
    ET.fromstring(svg)


def test_an_empty_diagram_is_a_valid_blank_canvas() -> None:
    root = ET.fromstring(render_svg(a_detail([])))

    assert root.get("viewBox") == "0 0 800 600"
    assert root.findall(".//{http://www.w3.org/2000/svg}g") == []


def test_the_canvas_grows_to_hold_a_far_box() -> None:
    root = ET.fromstring(render_svg(a_detail([(an_element(), 2000, 100, 200, 50)])))

    assert root.get("viewBox") == "0 0 2360 600"


def test_markup_in_a_name_is_text_and_never_markup() -> None:
    nasty = an_element('<script>alert(1)</script> & "x" ]]>')

    svg = render_svg(a_detail([(nasty, 10, 10, 400, 46)], name='<img src=x onerror=1> "d"'))

    assert "<script" not in svg
    assert "<img" not in svg
    assert "&lt;script&gt;" in svg
    assert "&amp;" in svg
    assert "&quot;" in svg
    root = ET.fromstring(svg)  # still well-formed XML
    titles = [t.text for t in root.iter("{http://www.w3.org/2000/svg}title")]
    assert '<script>alert(1)</script> & "x" ]]> — Application Component' in titles


def test_characters_xml_forbids_are_dropped() -> None:
    assert xml_safe("A\x00B\x0bC\x1fD") == "ABCD"
    svg = render_svg(a_detail([(an_element("A\x0bB\x0cC"), 0, 0, 132, 46)]))

    ET.fromstring(svg)
    assert "ABC" in svg


def test_a_non_finite_coordinate_is_never_written_as_inf_or_nan() -> None:
    svg = render_svg(a_detail([(an_element(), float("inf"), float("nan"), 132, 46)]))

    ET.fromstring(svg)
    assert not re.search(r'(=|\s)"?-?(inf|nan)\b', svg)
    assert 'x="0" y="0"' in svg
