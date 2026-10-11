"""export -> import -> export on a real PostgreSQL: what survives, and what is said not to.

The comparison is structural, not byte for byte, and on purpose. An element with no
`source_id` leaves as `id-<its uuid>`; imported into another database it is given a new
uuid, and an identifier shaped like a uuid that is not the element's own is replaced on the
way out (`IdentifierRegistry`: no `source_id` may pass for another object's generated
name). So the second file differs from the first in those identifiers and in nothing else,
and `canonical` names them by order of appearance. Every other difference fails the test.
In the *same* database the identifiers are the elements' own, and there the files are equal
byte for byte.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
from typing import Any

import pytest
from defusedxml import ElementTree as SafeET
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import AccessType
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import ImportInProgressError
from ea.domain.exchange import (
    NAMESPACE,
    SOURCE_ID,
    Category,
    ExchangeFormat,
    ExportRequest,
    ImportContent,
    ImportOptions,
    Severity,
)
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.exchanges import ExchangeService
from tests.conftest import an_editor
from tests.exchange_files import DATA, el, exchange_xml, rel

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
REAL = ImportOptions(dry_run=False)
NS = {"x": NAMESPACE}
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"
GENERATED = re.compile(r"\Aid-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


@pytest.fixture
def stack(
    engine_at_head: AsyncEngine,
) -> tuple[
    PostgresArchitectureRepository, ArchitectureService, PostgresExchangeRepository, ExchangeService
]:
    sessions = create_session_factory(engine_at_head)
    repository = PostgresArchitectureRepository(sessions)
    architecture = ArchitectureService(repository)
    store = PostgresExchangeRepository(sessions)
    return repository, architecture, store, ExchangeService(store, architecture)


def content(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


async def exported(exchange: ExchangeService) -> bytes:
    export = await exchange.export(ExportRequest())
    return b"".join([chunk async for chunk in export.chunks])


def canonical(data: bytes) -> dict[str, Any]:
    """Everything a file says, with the identifiers that mean nothing renamed by position."""
    root = SafeET.fromstring(data, forbid_dtd=True)
    names = {
        d.get("identifier"): d.find("x:name", NS).text  # type: ignore[union-attr]
        for d in root.findall("x:propertyDefinitions/x:propertyDefinition", NS)
    }
    labels: dict[str, str] = {}

    def label(identifier: str) -> str:
        if not GENERATED.match(identifier):
            return identifier
        return labels.setdefault(identifier, f"~{len(labels)}")

    def properties(node: Any) -> list[tuple[str, str | None]]:
        return sorted(
            (names[p.get("propertyDefinitionRef")], p.find("x:value", NS).text)  # type: ignore[union-attr]
            for p in node.findall("x:properties/x:property", NS)
        )

    def text_of(node: Any, tag: str) -> str | None:
        found = node.find(f"x:{tag}", NS)
        return None if found is None else found.text

    elements = [
        (
            n.get(XSI),
            label(n.get("identifier")),
            text_of(n, "name"),
            text_of(n, "documentation"),
            properties(n),
        )
        for n in root.findall("x:elements/x:element", NS)
    ]
    links = [
        (
            n.get(XSI),
            label(n.get("identifier")),
            label(n.get("source")),
            label(n.get("target")),
            text_of(n, "name"),
            n.get("accessType"),
            n.get("isDirected"),
            n.get("modifier"),
            properties(n),
        )
        for n in root.findall("x:relationships/x:relationship", NS)
    ]
    return {"elements": elements, "links": links}


async def reference_model(architecture: ArchitectureService) -> dict[str, Any]:
    """Every kind of content a round trip must carry, with and without a `source_id`."""
    actor = await architecture.create_element(
        element_type=E.BUSINESS_ACTOR,
        name="Alice & \"Bob\" <team> ]]> 'quoted'",
        description="Pays invoices\nsecond line\r\nthird line, é à ü, 😀",
        documentation="Long\ntext with é and 😀 and ]]> and <b>tags</b>",
        properties={SOURCE_ID: "actor-1", "owner": "finance", "empty": ""},
    )
    app = await architecture.create_element(
        element_type=E.APPLICATION_COMPONENT, name="Billing", properties={"tier": "gold"}
    )
    run = await architecture.create_element(element_type=E.APPLICATION_FUNCTION, name="Invoice run")
    data = await architecture.create_element(element_type=E.DATA_OBJECT, name="Invoice")
    goal = await architecture.create_element(element_type=E.GOAL, name="Get paid")
    await architecture.create_element(element_type=E.NODE, name="Alone, no relation at all")
    # A `source_id` that looks like another element's generated name, and one that is not a
    # valid XML name: neither can be written as is (see the module docstring).
    mimic = await architecture.create_element(
        element_type=E.NODE, name="Mimic", properties={SOURCE_ID: f"id-{run.id}"}
    )
    spaced = await architecture.create_element(
        element_type=E.NODE, name="Spaced", properties={SOURCE_ID: "has spaces & <marks>"}
    )
    await architecture.connect(
        relationship_type=R.SERVING,
        source_id=app.id,
        target_id=actor.id,
        name='serves "fast" & <well>',
    )
    await architecture.connect(
        relationship_type=R.ACCESS,
        source_id=run.id,
        target_id=data.id,
        access_type=AccessType.READ_WRITE,
    )
    await architecture.connect(
        relationship_type=R.ASSOCIATION, source_id=actor.id, target_id=app.id, directed=True
    )
    await architecture.connect(
        relationship_type=R.INFLUENCE,
        source_id=app.id,
        target_id=goal.id,
        properties={"modifier": "++", "why": "revenue", "blank": ""},
    )
    await architecture.connect(
        relationship_type=R.ASSOCIATION, source_id=mimic.id, target_id=spaced.id
    )
    return {"mimic": mimic, "spaced": spaced, "run": run}


async def empty_the_graph(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM elements"))  # links follow by foreign key


async def test_export_import_export_keeps_everything_but_the_identifiers_that_meant_nothing(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    repository, architecture, _, exchange = stack
    await reference_model(architecture)
    first = await exported(exchange)

    await empty_the_graph(engine_at_head)
    report = await exchange.import_exchange(content(first), REAL)

    assert report.counts[Category.ELEMENTS].created == 8
    assert report.counts[Category.RELATIONSHIPS].created == 5
    assert not [i for i in report.issues if i.severity is Severity.ERROR], report.issues
    second = await exported(exchange)
    assert canonical(second) == canonical(first)
    # and a second turn is as stable as the first
    await empty_the_graph(engine_at_head)
    await exchange.import_exchange(content(second), REAL)
    assert canonical(await exported(exchange)) == canonical(first)
    assert await repository.count_elements(ElementFilter()) == 8


async def test_the_identifiers_that_cannot_be_written_are_replaced_and_the_import_says_nothing_lost(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    """The documented limits: not an identity, and shown rather than hidden by the comparison."""
    repository, architecture, _, exchange = stack
    made = await reference_model(architecture)
    first = await exported(exchange)

    # In the file, the two odd `source_id`s were replaced by the elements' generated names.
    assert f'identifier="id-{made["mimic"].id}"'.encode() in first
    assert f'identifier="id-{made["spaced"].id}"'.encode() in first
    assert b"has spaces" not in first
    await empty_the_graph(engine_at_head)
    await exchange.import_exchange(content(first), REAL)

    elements = {e.name: e for e in await repository.list_elements(ElementFilter(limit=100))}
    assert elements["Mimic"].properties[SOURCE_ID] == f"id-{made['mimic'].id}"  # not "id-<run>"
    assert elements["Spaced"].properties[SOURCE_ID] == f"id-{made['spaced'].id}"  # not the original


async def test_importing_an_export_into_the_same_database_creates_nothing_and_changes_no_byte(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService],
) -> None:
    _, architecture, _, exchange = stack
    await reference_model(architecture)
    first = await exported(exchange)

    report = await exchange.import_exchange(content(first), REAL)

    for category in (Category.ELEMENTS, Category.RELATIONSHIPS):
        assert report.counts[category].created == 0
        assert report.counts[category].rejected == 0
    assert report.counts[Category.ELEMENTS].reused == 8
    assert report.counts[Category.RELATIONSHIPS].reused == 5
    assert await exported(exchange) == first


async def test_an_export_imported_where_some_of_it_already_exists_links_to_what_is_there(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    repository, architecture, _, exchange = stack
    await reference_model(architecture)
    first = await exported(exchange)
    await empty_the_graph(engine_at_head)
    present = await architecture.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",  # already there, linked to nothing
    )

    report = await exchange.import_exchange(content(first), REAL)

    assert report.counts[Category.ELEMENTS].reused == 1
    assert report.counts[Category.ELEMENTS].created == 7
    assert report.counts[Category.RELATIONSHIPS].created == 5
    assert (await repository.get_element(present.id)) is not None
    # The element that was there is reused, not rewritten: its (empty) properties win over the
    # file's `tier: gold`. That is the collision rule, and the one other difference there is.
    expected = canonical(first)
    expected["elements"] = [
        (*row[:4], []) if row[2] == "Billing" else row for row in expected["elements"]
    ]
    assert canonical(await exported(exchange)) == expected


async def test_ten_thousand_elements_make_the_same_round_trip(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    _, _, _, exchange = stack
    rows = "".join(el(f"e{i}", "Node", f"node {i} &amp; &lt;{i}&gt; é😀") for i in range(10_000))
    links = "".join(rel(f"r{i}", "Association", f"e{i}", f"e{i + 1}") for i in range(0, 9_999, 3))
    built = await exchange.import_exchange(content(exchange_xml(rows, links)), REAL)
    assert built.counts[Category.ELEMENTS].created == 10_000
    first = await exported(exchange)

    await empty_the_graph(engine_at_head)
    report = await exchange.import_exchange(content(first), REAL)

    assert report.counts[Category.ELEMENTS].created == 10_000
    assert (
        report.counts[Category.RELATIONSHIPS].created
        == built.counts[Category.RELATIONSHIPS].created
    )
    again = await exchange.import_exchange(content(first), ImportOptions())  # nothing left to do
    assert (
        again.counts[Category.ELEMENTS].created == 0
        and again.counts[Category.ELEMENTS].reused == 10_000
    )
    assert canonical(await exported(exchange)) == canonical(first)


async def test_a_dry_run_reports_what_the_real_import_does_and_changes_nothing(
    stack: tuple[PostgresArchitectureRepository, Any, Any, ExchangeService],
) -> None:
    repository, _, _, exchange = stack
    data = content((DATA / "minimal-model.xml").read_bytes())

    simulated = await exchange.import_exchange(data, ImportOptions())
    assert await repository.count_elements(ElementFilter()) == 0
    real = await exchange.import_exchange(data, REAL)

    assert (real.counts, real.issues) == (simulated.counts, simulated.issues)
    assert await repository.count_elements(ElementFilter()) == 3


async def test_a_refused_relation_leaves_the_rest_and_strict_leaves_nothing(
    stack: tuple[PostgresArchitectureRepository, Any, Any, ExchangeService],
) -> None:
    repository, _, _, exchange = stack
    data = content((DATA / "refused-relations.xml").read_bytes())

    strict = await exchange.import_exchange(data, ImportOptions(dry_run=False, strict=True))
    assert not strict.applied and await repository.count_elements(ElementFilter()) == 0

    report = await exchange.import_exchange(data, REAL)
    assert report.applied and await repository.count_elements(ElementFilter()) == 3
    assert [i.code for i in report.issues] == ["relationship_not_allowed"]


async def test_two_imports_at_once_one_gets_a_409(
    stack: tuple[Any, Any, PostgresExchangeRepository, ExchangeService],
) -> None:
    _, _, store, exchange = stack
    data = content((DATA / "minimal-model.xml").read_bytes())

    async def second() -> None:
        with acting_as(an_editor()):  # a fresh context holds no caller: this is its own request
            await exchange.import_exchange(data, ImportOptions())

    async with store.transaction(commit=False):  # the first import holds the lock
        task = asyncio.create_task(second(), context=contextvars.Context())
        with pytest.raises(ImportInProgressError):
            await task
