"""Structural checks on an export that always run, whatever the XSD (docs/adr/0051).

They do not replace validation against the Open Group schema, which is not in
this repository: they pin what the schema would also demand and that a reader
(Archi, Sparx EA) trips over first — the namespace, the sections and their
order, unique identifiers, and references that resolve.
"""

from __future__ import annotations

import pytest
from defusedxml import ElementTree as DefusedET

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.exchange import NAMESPACE, SOURCE_ID, ExportRequest
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService

pytestmark = pytest.mark.asyncio
NS = {"x": NAMESPACE}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
SECTION_ORDER = ["name", "elements", "relationships", "propertyDefinitions"]


async def exported_root(service: ExchangeService):  # type: ignore[no-untyped-def]
    export = await service.export(ExportRequest())
    return DefusedET.fromstring(b"".join([c async for c in export.chunks]))


async def a_busy_model(graph: ArchitectureService) -> None:
    a = await graph.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="A & <B>",
        description="d",
        properties={SOURCE_ID: "1-starts-with-a-digit", "owner": "x"},
    )
    b = await graph.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="B",
        properties={SOURCE_ID: "1-starts-with-a-digit"},
    )
    c = await graph.create_element(
        element_type=E.DATA_OBJECT, name="C", properties={SOURCE_ID: "id-" + str(a.id)}
    )
    await graph.connect(relationship_type=R.FLOW, source_id=a.id, target_id=b.id)
    function = await graph.create_element(element_type=E.APPLICATION_FUNCTION, name="F")
    await graph.connect(
        relationship_type=R.ACCESS,
        source_id=function.id,
        target_id=c.id,
        properties={SOURCE_ID: "x y"},
    )


async def test_the_document_has_the_exchange_shape(
    service: ArchitectureService, exchange_service: ExchangeService
) -> None:
    await a_busy_model(service)

    root = await exported_root(exchange_service)

    assert root.tag == f"{{{NAMESPACE}}}model"
    assert root.get("identifier")
    assert [child.tag.split("}")[1] for child in root] == SECTION_ORDER
    for element in root.findall("x:elements/x:element", NS):
        assert element.get(XSI_TYPE) and element.get("identifier")
        assert element.find("x:name", NS).get(XML_LANG)  # type: ignore[union-attr]
    for link in root.findall("x:relationships/x:relationship", NS):
        assert link.get(XSI_TYPE) and link.get("source") and link.get("target")


async def test_identifiers_are_unique_valid_names_and_every_reference_resolves(
    service: ArchitectureService, exchange_service: ExchangeService
) -> None:
    await a_busy_model(service)

    root = await exported_root(exchange_service)

    everything = [n for n in root.iter() if n.get("identifier") is not None]
    identifiers = [n.get("identifier") for n in everything]
    assert len(identifiers) == len(set(identifiers))
    assert all(i and not i[0].isdigit() and ":" not in i and " " not in i for i in identifiers)
    elements = {e.get("identifier") for e in root.findall("x:elements/x:element", NS)}
    for link in root.findall("x:relationships/x:relationship", NS):
        assert link.get("source") in elements and link.get("target") in elements
    definitions = {
        d.get("identifier") for d in root.findall("x:propertyDefinitions/x:propertyDefinition", NS)
    }
    for ref in root.iter(f"{{{NAMESPACE}}}property"):
        assert ref.get("propertyDefinitionRef") in definitions


async def test_no_internal_column_leaks_into_the_properties(
    service: ArchitectureService, exchange_service: ExchangeService
) -> None:
    await a_busy_model(service)

    root = await exported_root(exchange_service)

    names = {n.text for n in root.findall("x:propertyDefinitions/x:propertyDefinition/x:name", NS)}
    assert names == {"owner", "ea.description"}
