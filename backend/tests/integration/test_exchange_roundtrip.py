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
import csv
import io
import re
import zipfile
from typing import Any

import pytest
from defusedxml import ElementTree as SafeET
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import AccessType
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import DiagramNode
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
from ea.services.diagrams import DiagramService
from ea.services.exchanges import ExchangeService
from ea.services.packages import PackageService
from tests.conftest import an_editor
from tests.exchange_files import DATA, el, exchange_xml, rel
from tests.integration.exchange_support import empty_packages

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


# --- CSV: the same round trip, the same honesty ---------------------------------------------

CSV_EXPORT = ExportRequest(format=ExchangeFormat.CSV)
RAW_CSV_EXPORT = ExportRequest(format=ExchangeFormat.CSV, safe_cells=False)


async def csv_parts(
    exchange: ExchangeService, request: ExportRequest = CSV_EXPORT
) -> dict[str, bytes]:
    export = await exchange.export(request)
    archive = zipfile.ZipFile(io.BytesIO(b"".join([chunk async for chunk in export.chunks])))
    return {name: archive.read(name) for name in archive.namelist()}


def csv_content(parts: dict[str, bytes]) -> ImportContent:
    return ImportContent(
        ExchangeFormat.CSV, parts["elements.csv"], parts["relations.csv"], parts["properties.csv"]
    )


def table_of(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


def canonical_csv(parts: dict[str, bytes]) -> dict[str, Any]:
    """Everything the three files say, the identifiers that mean nothing named by position."""
    labels: dict[str, str] = {}

    def label(identifier: str) -> str:
        if not GENERATED.match(identifier):
            return identifier
        return labels.setdefault(identifier, f"~{len(labels)}")

    elements = [
        (kind, label(identifier), name, documentation, specialization)
        for identifier, kind, name, documentation, specialization in table_of(
            parts["elements.csv"]
        )[1:]
    ]
    links = [
        (kind, label(identifier), name, doc, label(source), label(target), specialization)
        for identifier, kind, name, doc, source, target, specialization in table_of(
            parts["relations.csv"]
        )[1:]
    ]
    properties = [
        (label(identifier), key, value)
        for identifier, key, value in table_of(parts["properties.csv"])[1:]
    ]
    return {"elements": elements, "links": links, "properties": properties}


#: Names and values a spreadsheet could run, and legitimate ones that merely look alike.
FORMULAS = [
    "=1+1",
    "@SUM(A1)",
    "+cmd|' /C calc'!A0",
    "-2+3",
    "-Legacy",  # a real name: it must come back as it went
    "'=already quoted",
    "''-twice",
    "\N{ZERO WIDTH SPACE}=1",
    "\N{ZERO WIDTH NO-BREAK SPACE}=1",
    '=HYPERLINK("http://x")',
]


async def formula_model(architecture: ArchitectureService) -> None:
    for number, name in enumerate(FORMULAS):
        await architecture.create_element(
            element_type=E.NODE,
            name=name,
            description=f"=described {number}",
            documentation=f"+doc {number}\n-second line",
            properties={
                "tab": "\t=1",
                "space": " =1",
                "at": "@home",
                "carriage": "\r=1",
                "legacy": "-Legacy",
            },
        )


def no_cell_runs_as_a_formula(parts: dict[str, bytes]) -> None:
    for name, data in parts.items():
        for row in table_of(data):
            for cell in row:
                assert cell[:1] not in ("=", "+", "-", "@", "\t", "\r"), (name, cell)


async def test_csv_export_import_export_keeps_everything_but_the_identifiers_that_meant_nothing(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    repository, architecture, _, exchange = stack
    await reference_model(architecture)
    await formula_model(architecture)
    first = await csv_parts(exchange)
    assert first["elements.csv"].count(b"'=1+1") == 1  # neutralised in the file...
    no_cell_runs_as_a_formula(first)  # ...and in every other cell of every file

    await empty_the_graph(engine_at_head)
    report = await exchange.import_exchange(csv_content(first), REAL)

    assert not [i for i in report.issues if i.severity is Severity.ERROR], report.issues
    assert report.counts[Category.ELEMENTS].created == 8 + len(FORMULAS)
    assert report.counts[Category.RELATIONSHIPS].created == 5
    names = {e.name for e in await repository.list_elements(ElementFilter(limit=100))}
    assert set(FORMULAS) <= names  # given back as they went: no apostrophe gained or lost
    second = await csv_parts(exchange)
    assert canonical_csv(second) == canonical_csv(first)
    # a second turn is as stable as the first: nothing accumulates, apostrophes included
    await empty_the_graph(engine_at_head)
    await exchange.import_exchange(csv_content(second), REAL)
    assert canonical_csv(await csv_parts(exchange)) == canonical_csv(first)
    assert await repository.count_elements(ElementFilter()) == 8 + len(FORMULAS)


async def test_the_values_of_the_properties_survive_the_guard(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    repository, architecture, _, exchange = stack
    await formula_model(architecture)
    first = await csv_parts(exchange)

    await empty_the_graph(engine_at_head)
    await exchange.import_exchange(csv_content(first), REAL)

    for element in await repository.list_elements(ElementFilter(limit=100)):
        assert dict(element.properties) == {
            "tab": "\t=1",
            "space": " =1",
            "at": "@home",
            "carriage": "\r=1",
            "legacy": "-Legacy",
            "source_id": element.properties["source_id"],
        }
        assert element.description.startswith("=described")
        assert element.documentation.startswith("+doc")


async def test_csv_reimport_into_the_same_database_creates_nothing_and_changes_no_byte(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService],
) -> None:
    _, architecture, _, exchange = stack
    await reference_model(architecture)
    await formula_model(architecture)
    export = await exchange.export(CSV_EXPORT)
    first_zip = b"".join([chunk async for chunk in export.chunks])
    first = await csv_parts(exchange)

    report = await exchange.import_exchange(csv_content(first), REAL)

    assert report.counts[Category.ELEMENTS].created == 0
    assert report.counts[Category.ELEMENTS].rejected == 0
    assert report.counts[Category.ELEMENTS].reused == 8 + len(FORMULAS)
    assert report.counts[Category.RELATIONSHIPS].created == 0
    assert report.counts[Category.RELATIONSHIPS].rejected == 0
    assert report.counts[Category.RELATIONSHIPS].reused == 5
    again = await exchange.export(CSV_EXPORT)
    assert b"".join([chunk async for chunk in again.chunks]) == first_zip


async def test_a_property_called_directed_or_access_type_is_not_taken_for_the_attribute(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    repository, architecture, _, exchange = stack
    a = await architecture.create_element(element_type=E.NODE, name="A")
    b = await architecture.create_element(element_type=E.NODE, name="B")
    await architecture.connect(
        relationship_type=R.ASSOCIATION,
        source_id=a.id,
        target_id=b.id,
        properties={"directed": "true", "access_type": "read"},
    )
    first = await csv_parts(exchange)

    await empty_the_graph(engine_at_head)
    await exchange.import_exchange(csv_content(first), REAL)

    (link,) = await repository.list_relationships(limit=10)
    assert link.directed is False
    assert dict(link.properties)["directed"] == "true"
    assert dict(link.properties)["access_type"] == "read"


async def test_ten_thousand_elements_make_the_same_csv_round_trip(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    _, _, _, exchange = stack
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(["ID", "Type", "Name", "Documentation"])
    for i in range(10_000):
        writer.writerow([f"e{i}", "Node", f'node {i}, "q" é😀 ]]> {i}', f"line1\r\nline{i}"])
    links = io.StringIO()
    link_writer = csv.writer(links, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    link_writer.writerow(["ID", "Type", "Name", "Documentation", "Source", "Target"])
    for i in range(0, 9_999, 3):
        link_writer.writerow([f"r{i}", "Association", "", "", f"e{i}", f"e{i + 1}"])
    built = await exchange.import_exchange(
        ImportContent(ExchangeFormat.CSV, buffer.getvalue().encode(), links.getvalue().encode()),
        REAL,
    )
    assert built.counts[Category.ELEMENTS].created == 10_000
    first = await csv_parts(exchange)

    await empty_the_graph(engine_at_head)
    report = await exchange.import_exchange(csv_content(first), REAL)

    assert report.counts[Category.ELEMENTS].created == 10_000
    assert (
        report.counts[Category.RELATIONSHIPS].created
        == built.counts[Category.RELATIONSHIPS].created
    )
    again = await exchange.import_exchange(csv_content(first), ImportOptions())
    assert (
        again.counts[Category.ELEMENTS].created == 0
        and again.counts[Category.ELEMENTS].reused == 10_000
    )
    assert canonical_csv(await csv_parts(exchange)) == canonical_csv(first)


async def test_where_the_csv_round_trip_is_not_an_identity_it_says_so(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    """The documented limits, shown rather than hidden by the comparison."""
    repository, architecture, _, exchange = stack
    made = await reference_model(architecture)
    await architecture.create_element(element_type=E.NODE, name="two\nlines")
    first = await csv_parts(exchange)

    # An identifier that cannot be used as it is stays replaced, as in the XML.
    assert f'"id-{made["mimic"].id}"'.encode() in first["elements.csv"]
    assert b"has spaces" not in first["elements.csv"]
    await empty_the_graph(engine_at_head)
    report = await exchange.import_exchange(csv_content(first), REAL)

    # A name holding a line break is exported, and refused on the way back: names may not
    # hold control characters (it is one row, rejected with its own code, not a failure).
    assert report.counts[Category.ELEMENTS].rejected == 1
    assert "control_character" in {i.code for i in report.issues}
    elements = {e.name: e for e in await repository.list_elements(ElementFilter(limit=100))}
    assert "two\nlines" not in elements
    assert elements["Spaced"].properties[SOURCE_ID] == f"id-{made['spaced'].id}"


async def test_a_name_quoted_by_the_export_comes_back_changed_if_the_guard_was_off_on_export_only(
    stack: tuple[Any, ArchitectureService, Any, ExchangeService], engine_at_head: AsyncEngine
) -> None:
    """`safe_cells` must be the same on both sides: that is what makes the guard reversible."""
    repository, architecture, _, exchange = stack
    await architecture.create_element(element_type=E.NODE, name="'=literal")
    raw = await csv_parts(exchange, RAW_CSV_EXPORT)
    await empty_the_graph(engine_at_head)

    await exchange.import_exchange(csv_content(raw), REAL)  # the guard is read back by default

    (element,) = await repository.list_elements(ElementFilter(limit=100))
    assert element.name == "=literal"  # not an identity: the apostrophe was taken for a guard


# --- Views and packages ----------------------------------------------------------------------


@pytest.fixture
def whole(
    engine_at_head: AsyncEngine,
) -> tuple[ArchitectureService, DiagramService, PackageService, ExchangeService, Any]:
    from ea.repositories.diagram_store import PostgresDiagramRepository
    from ea.repositories.package_store import PostgresPackageRepository
    from ea.repositories.saved_query_store import PostgresSavedQueryRepository
    from ea.services.saved_queries import SavedQueryService

    sessions = create_session_factory(engine_at_head)
    architecture = ArchitectureService(PostgresArchitectureRepository(sessions))
    packages_store = PostgresPackageRepository(sessions)
    diagrams = DiagramService(
        PostgresDiagramRepository(sessions), architecture, packages=packages_store
    )
    packages = PackageService(packages_store)
    queries = SavedQueryService(
        PostgresSavedQueryRepository(sessions), packages_store, architecture
    )
    exchange = ExchangeService(
        PostgresExchangeRepository(sessions),
        architecture,
        diagrams=diagrams,
        packages=packages,
        queries=queries,
    )
    return architecture, diagrams, packages, exchange, (sessions, queries)


async def clear_everything(sessions: Any, engine: AsyncEngine) -> None:
    await empty_the_graph(engine)
    await empty_packages(sessions)


def structure_of(data: bytes) -> dict[str, Any]:
    """Folders and views of a file, the identifiers that mean nothing named by position."""
    root = SafeET.fromstring(data, forbid_dtd=True)
    labels: dict[str, str] = {}

    def label(identifier: str) -> str:
        return (
            identifier
            if not GENERATED.match(identifier)
            else labels.setdefault(identifier, f"~{len(labels)}")
        )

    for node in root.findall("x:elements/x:element", NS):
        label(node.get("identifier"))

    def folder(node: Any) -> Any:
        return (
            node.find("x:label", NS).text,
            sorted(
                label(i.get("identifierRef")) for i in node.findall("x:item[@identifierRef]", NS)
            ),
            [folder(c) for c in node.findall("x:item[x:label]", NS)],
        )

    folders = [folder(n) for n in root.findall("x:organizations/x:item[x:label]", NS)]
    views = [
        (
            v.find("x:name", NS).text,
            (
                v.find("x:documentation", NS).text
                if v.find("x:documentation", NS) is not None
                else None
            ),
            [
                (label(b.get("elementRef")), b.get("x"), b.get("y"), b.get("w"), b.get("h"))
                for b in v.findall("x:node", NS)
            ],
        )
        for v in root.findall("x:views/x:diagrams/x:view", NS)
    ]
    return {"folders": folders, "views": views}


async def a_filed_catalogue(whole: Any) -> dict[str, Any]:
    architecture, diagrams, packages, _, _ = whole
    await reference_model(architecture)
    elements = {e.name: e for e in await architecture.list_elements(ElementFilter(limit=100))}
    top = (await packages.create(name="Infra")).package.id
    sub = (await packages.create(name="Réseau", parent_id=top)).package.id
    await packages.create(name="Vide")
    await packages.move_elements([elements["Billing"].id, elements["Invoice run"].id], top)
    await packages.move_elements([elements["Invoice"].id], sub)
    view = await diagrams.create(name="Vue d'ensemble", description="La vue\nen deux lignes")
    await diagrams.replace_layout(
        view.id,
        [
            DiagramNode(elements["Billing"].id, 10.4, 20, 120, 55),
            DiagramNode(elements["Invoice"].id, -30, 400, 200, 80),
        ],
    )
    await diagrams.create(name="Vue vide")
    return elements


async def test_views_and_packages_survive_export_import_export(
    whole: Any, engine_at_head: AsyncEngine
) -> None:
    architecture, diagrams, _, exchange, (sessions, _) = whole
    await a_filed_catalogue(whole)
    first = await exported(exchange)

    await clear_everything(sessions, engine_at_head)
    for gone in await diagrams.list_diagrams():
        await diagrams.delete(gone.id)
    report = await exchange.import_exchange(content(first), REAL)

    assert report.counts[Category.PACKAGES].created == 3
    assert report.counts[Category.VIEWS].created == 2
    assert not [i for i in report.issues if i.severity is Severity.ERROR], report.issues
    second = await exported(exchange)
    assert structure_of(second) == structure_of(first)
    assert canonical(second) == canonical(first)
    filed = {
        e.name: e.package_id for e in await architecture.list_elements(ElementFilter(limit=100))
    }
    assert filed["Alone, no relation at all"] is None  # unfiled stays unfiled
    assert filed["Billing"] is not None and filed["Invoice"] != filed["Billing"]


async def test_importing_an_export_into_the_same_database_creates_and_moves_nothing(
    whole: Any,
) -> None:
    architecture, _, _, exchange, _ = whole
    await a_filed_catalogue(whole)
    first = await exported(exchange)

    async def state() -> dict[str, Any]:
        found = await architecture.list_elements(ElementFilter(limit=100))
        return {e.name: (e.package_id, e.revision) for e in found}

    before = await state()
    report = await exchange.import_exchange(content(first), REAL)

    for category in (
        Category.ELEMENTS,
        Category.RELATIONSHIPS,
        Category.PACKAGES,
        Category.VIEWS,
    ):
        assert report.counts[category].created == 0, category
        assert report.counts[category].rejected == 0, category
    assert report.counts[Category.PACKAGES].reused == 3
    assert report.counts[Category.VIEWS].reused == 2
    after = await state()
    # No element changed package. The first import of a file does complete an element that had
    # no `source_id` with the file's identifier (a revision, as in the XML round trip); the
    # second one is the identity.
    assert {n: p for n, (p, _) in after.items()} == {n: p for n, (p, _) in before.items()}
    assert await exported(exchange) == first
    await exchange.import_exchange(content(first), REAL)
    assert await state() == after


async def test_a_scope_round_trips_through_an_empty_database(
    whole: Any, engine_at_head: AsyncEngine
) -> None:
    from ea.domain.exchange import ExportScope, ScopeKind

    _, diagrams, packages, exchange, (sessions, _) = whole
    elements = await a_filed_catalogue(whole)
    top = next(s.package.id for s in await packages.list_packages() if s.package.name == "Infra")
    scoped = ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=top))
    export = await exchange.export(scoped)
    first = b"".join([chunk async for chunk in export.chunks])

    assert export.skipped_views == 0  # the view's two boxes are both in Infra's subtree
    assert b"Alice" not in first and b"Billing" in first  # nothing from outside the scope
    await clear_everything(sessions, engine_at_head)
    for gone in await diagrams.list_diagrams():
        await diagrams.delete(gone.id)
    await exchange.import_exchange(content(first), REAL)
    export_again = await exchange.export(
        ExportRequest(
            scope=ExportScope(
                ScopeKind.PACKAGE,
                package_id=next(
                    s.package.id
                    for s in await packages.list_packages()
                    if s.package.name == "Infra"
                ),
            )
        )
    )
    second = b"".join([chunk async for chunk in export_again.chunks])

    assert canonical(second) == canonical(first)
    assert structure_of(second) == structure_of(first)
    assert elements  # (the reference catalogue had more than the scope)


async def test_an_element_already_filed_elsewhere_is_not_moved_by_a_folder(
    whole: Any, engine_at_head: AsyncEngine
) -> None:
    architecture, _, packages, exchange, _ = whole
    elsewhere = (await packages.create(name="Elsewhere")).package.id
    element = await architecture.create_element(element_type=E.NODE, name="srv")
    await packages.move_elements([element.id], elsewhere)
    revision = (await architecture.get_element(element.id)).revision
    data = exchange_xml(
        el("n", "Node", "srv"),
        extra="<organizations><item><label>Infra</label>"
        '<item identifierRef="n"/></item></organizations>',
    )

    report = await exchange.import_exchange(content(data), REAL)

    kept = await architecture.get_element(element.id)
    assert kept.package_id == elsewhere
    # The only change is the source_id completion, which is a revision of its own kind.
    assert kept.revision <= revision + 1
    assert "package_not_applied" in {i.code for i in report.issues}


async def test_an_import_takes_the_tree_before_any_element_so_a_package_write_cannot_interlock(
    whole: Any, engine_at_head: AsyncEngine
) -> None:
    from sqlalchemy import func, select, text

    from ea.repositories.package_store import TREE_LOCK

    architecture, _, _, exchange, _ = whole
    element = await architecture.create_element(
        element_type=E.NODE, name="srv", properties={SOURCE_ID: "n"}
    )
    data = exchange_xml(
        el("n", "Node", "srv"),
        extra="<organizations><item><label>Infra</label>"
        '<item identifierRef="n"/></item></organizations>',
    )

    async def importing() -> Any:
        with acting_as(an_editor()):
            return await exchange.import_exchange(content(data), REAL)

    async with engine_at_head.connect() as writer, writer.begin():
        # A package write holds the tree...
        await writer.execute(select(func.pg_advisory_xact_lock(TREE_LOCK)))
        task = asyncio.create_task(importing(), context=contextvars.Context())
        for _ in range(200):  # ...and the import queues behind it
            waiting = await writer.scalar(
                text("select count(*) from pg_locks where locktype='advisory' and not granted")
            )
            if waiting:
                break
            await asyncio.sleep(0.02)
        assert waiting, "the import should wait for the tree"
        # ...so it holds no element yet: the writer takes the row without waiting for it.
        await writer.execute(
            text("select id from elements where id = :id for update nowait"), {"id": element.id}
        )

    report = await asyncio.wait_for(task, 20)
    assert report.applied
