"""The one static drawing of a diagram, as an SVG string.

A report embeds it, `GET /diagrams/{id}/render.svg` serves it, and the reader
mode shows it. It is *the* static drawing: the interactive editor keeps its own
TypeScript geometry for dragging, and a parity test (`test_diagram_render.py`
against `frontend/tests/diagramParity.spec.ts`, one JSON fixture) keeps the two
at the same coordinates. Changing a number here without the editor's breaks one
of the two tests.

Pure and deterministic: same detail, same string. No clock, no randomness, no
`<style>` element (presentation attributes only, so a strict CSP is enough).
Every text from a person goes through `_esc`. See docs/adr/0043.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from ea.domain.archimate import Layer, RelationshipType
from ea.domain.diagrams import DiagramDetail, DiagramNode
from ea.domain.model import Relationship

#: The ArchiMate layer fills. The SPA's `LAYER_COLOURS` must equal this mapping
#: (checked through the shared parity fixture) and `/metamodel` serves it.
LAYER_FILL: Final[Mapping[Layer, str]] = MappingProxyType(
    {
        Layer.MOTIVATION: "#ccccff",
        Layer.STRATEGY: "#f5deaa",
        Layer.BUSINESS: "#ffffb5",
        Layer.APPLICATION: "#b5ffff",
        Layer.TECHNOLOGY: "#c9e7b7",
        Layer.PHYSICAL: "#afdfa0",
        Layer.IMPLEMENTATION_MIGRATION: "#ffe0e0",
        Layer.OTHER: "#e8e6ec",
    }
)
#: The ink on those fills, never the page's colour.
BOX_INK: Final = "#24202b"
EDGE_INK: Final = "#4a4455"

#: Same numbers as `lib/diagramGeometry.ts` (MIN_CANVAS, CANVAS_MARGIN, CHARACTER_WIDTH).
MIN_CANVAS_WIDTH: Final = 800.0
MIN_CANVAS_HEIGHT: Final = 600.0
CANVAS_MARGIN: Final = 160.0
CHARACTER_WIDTH: Final = 7.5

#: Same words as the SPA's `features/relationships/labels.ts`: a translation
#: table, not a rule.
RELATIONSHIP_VERBS: Final[Mapping[RelationshipType, str]] = MappingProxyType(
    {
        RelationshipType.COMPOSITION: "compose",
        RelationshipType.AGGREGATION: "agrège",
        RelationshipType.ASSIGNMENT: "est affecté à",
        RelationshipType.REALIZATION: "réalise",
        RelationshipType.SERVING: "sert",
        RelationshipType.ACCESS: "accède à",
        RelationshipType.INFLUENCE: "influence",
        RelationshipType.ASSOCIATION: "est associé à",
        RelationshipType.TRIGGERING: "déclenche",
        RelationshipType.FLOW: "alimente",
        RelationshipType.SPECIALIZATION: "spécialise",
    }
)
ACCESS_VERBS: Final[Mapping[str, str]] = MappingProxyType(
    {"access": "accède", "read": "lit", "write": "écrit", "read_write": "lit et écrit"}
)

_FONT: Final = 'font-family="DejaVu Sans, sans-serif"'
_ARROW: Final = (
    '<defs><marker id="ea-diagram-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{EDGE_INK}"/></marker></defs>'
)
#: What XML 1.0 forbids: C0 controls other than tab, newline, carriage return.
_XML_ILLEGAL: Final = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

type Box = tuple[float, float, float, float]


def xml_safe(text: str) -> str:
    """The text without the characters no XML document may hold."""
    return _XML_ILLEGAL.sub("", text)


def _esc(text: str) -> str:
    return html.escape(xml_safe(text), quote=True)


def _n(value: float) -> str:
    """A coordinate in its shortest exact form: `40`, `61.5`, never `40.0` or `-0`."""
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _finite(node: DiagramNode) -> DiagramNode:
    """The box with any non-finite number read as 0: SVG has no `inf`, and a row can hold one."""
    return DiagramNode(
        node.element_id,
        *(v if math.isfinite(v) else 0.0 for v in (node.x, node.y, node.width, node.height)),
    )


def shorten(name: str, width: float) -> str:
    """As much of `name` as a box `width` wide holds (the editor's `shorten`)."""
    limit = max(4, math.floor(width / CHARACTER_WIDTH))
    return f"{name[: limit - 1]}…" if len(name) > limit else name


def verb_of(relationship: Relationship) -> str:
    if relationship.relationship_type is RelationshipType.ACCESS and relationship.access_type:
        return ACCESS_VERBS[relationship.access_type.value]
    return RELATIONSHIP_VERBS[relationship.relationship_type]


def _centre(box: Box) -> tuple[float, float]:
    x, y, width, height = box
    return x + width / 2, y + height / 2


def _clip(origin: tuple[float, float], box: Box) -> tuple[float, float]:
    """Where the segment from `origin` to the centre of `box` crosses its border."""
    cx, cy = _centre(box)
    dx, dy = origin[0] - cx, origin[1] - cy
    scale = min(
        math.inf if dx == 0 else box[2] / 2 / abs(dx),
        math.inf if dy == 0 else box[3] / 2 / abs(dy),
    )
    if math.isinf(scale):
        return cx, cy
    return cx + dx * scale, cy + dy * scale


def _segment(source: Box, target: Box) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The stroke between two boxes, or `None` when they overlap (the editor's rule)."""
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    if sx < tx + tw and tx < sx + sw and sy < ty + th and ty < sy + sh:
        return None
    return _clip(_centre(target), source), _clip(_centre(source), target)


def render_svg(detail: DiagramDetail) -> str:
    """The diagram as a standalone SVG document (no XML declaration, UTF-8)."""
    elements = {element.id: element for element in detail.graph.elements}
    drawn = [
        (_finite(node), elements[node.element_id])
        for node in detail.nodes
        if node.element_id in elements
    ]
    boxes: dict[object, Box] = {
        node.element_id: (node.x, node.y, node.width, node.height) for node, _ in drawn
    }
    width = max([MIN_CANVAS_WIDTH, *(x + w + CANVAS_MARGIN for x, _, w, _ in boxes.values())])
    height = max([MIN_CANVAS_HEIGHT, *(y + h + CANVAS_MARGIN for _, y, _, h in boxes.values())])

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(width)} {_n(height)}" '
        f'width="{_n(width)}" height="{_n(height)}" role="img" '
        f'aria-label="{_esc(detail.diagram.name)}">',
        f"<title>{_esc(detail.diagram.name)}</title>",
        _ARROW,
    ]
    for link in detail.graph.relationships:
        source, target = boxes.get(link.source_id), boxes.get(link.target_id)
        segment = _segment(source, target) if source and target else None
        if segment is None:
            continue
        (x1, y1), (x2, y2) = segment
        lines += [
            f'<g class="edge" data-relationship-id="{link.id}">',
            f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{EDGE_INK}" stroke-width="1.5" marker-end="url(#ea-diagram-arrow)"/>',
            f'<text x="{_n((x1 + x2) / 2)}" y="{_n((y1 + y2) / 2 - 4)}" text-anchor="middle" '
            f'{_FONT} font-size="11" fill="{EDGE_INK}">{_esc(verb_of(link))}</text>',
            "</g>",
        ]
    for node, element in drawn:
        kind = element.element_type
        cx = node.x + node.width / 2
        cy = node.y + node.height / 2
        lines += [
            f'<g class="node" data-element-id="{element.id}">',
            f"<title>{_esc(element.name)} — {_esc(kind.label)}</title>",
            f'<rect x="{_n(node.x)}" y="{_n(node.y)}" width="{_n(node.width)}" '
            f'height="{_n(node.height)}" rx="6" fill="{LAYER_FILL[kind.layer]}" '
            f'stroke="{BOX_INK}" stroke-opacity="0.35"/>',
            f'<text class="name" x="{_n(cx)}" y="{_n(cy - 2)}" text-anchor="middle" {_FONT} '
            f'font-size="13" fill="{BOX_INK}">{_esc(shorten(element.name, node.width))}</text>',
            f'<text class="type" x="{_n(cx)}" y="{_n(cy + 13)}" text-anchor="middle" {_FONT} '
            f'font-size="10" fill="{BOX_INK}">{_esc(shorten(kind.label, node.width))}</text>',
            "</g>",
        ]
    lines.append("</svg>")
    return "\n".join(lines)
