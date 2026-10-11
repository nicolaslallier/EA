"""The export, validated against the Open Group's XSD when the operator provides it.

The schema is not in this repository: its licence has not been read, so it is
neither downloaded nor versioned (docs/adr/0049). To run this test, point
`EA_EXCHANGE_XSD` at a local `archimate3_Model.xsd` (its sibling `xml.xsd`,
`archimate3_View.xsd`, `archimate3_Diagram.xsd` beside it), obtained from
https://www.opengroup.org/xsd/archimate/ . Without it the test is skipped with
a message; `test_exchange_structure.py` always runs the hand-written checks.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import pytest

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.exchange import ExchangeElement, ExchangeRelationship
from ea.formats.archimate_xml import render_model

lxml = pytest.importorskip("lxml.etree")
_PROVIDED = os.environ.get("EA_EXCHANGE_XSD", "")
XSD = Path(_PROVIDED) if _PROVIDED else None

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        XSD is None or not XSD.is_file(),
        reason="EA_EXCHANGE_XSD does not name an archimate3_Model.xsd — see the module docstring",
    ),
]


async def aiter_of[T](items: Iterable[T]) -> AsyncIterator[T]:
    for item in items:
        yield item


async def test_an_export_with_every_kind_of_content_validates() -> None:
    assert XSD is not None
    elements = [
        ExchangeElement("a", ElementType.BUSINESS_ACTOR, "Alice", "d", "doc", {"owner": "x"}),
        ExchangeElement("b", ElementType.APPLICATION_COMPONENT, "CRM"),
    ]
    links = [
        ExchangeRelationship("r1", RelationshipType.ACCESS, "a", "b", access_type=AccessType.READ),
        ExchangeRelationship("r2", RelationshipType.ASSOCIATION, "a", "b", directed=True),
        ExchangeRelationship(
            "r3", RelationshipType.INFLUENCE, "a", "b", properties={"modifier": "+"}
        ),
    ]
    text = "".join([p async for p in render_model("EA", aiter_of(elements), aiter_of(links))])

    schema = lxml.XMLSchema(lxml.parse(str(XSD)))
    document = lxml.fromstring(text.encode("utf-8"))

    assert schema.validate(document), schema.error_log
