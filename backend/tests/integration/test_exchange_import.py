"""The import against a real PostgreSQL: the replay of rows, the budget, dry run, review."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.exchange import (
    SOURCE_ID,
    Category,
    ExchangeFormat,
    ImportContent,
    ImportOptions,
)
from ea.domain.model import Element
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.exchange_files import el, exchange_xml, rel
from tests.integration.test_element_review import put_in_state, state_of

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
REAL = ImportOptions(dry_run=False)


def content(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


@pytest.fixture
def importer(engine_at_head: AsyncEngine, graph_service: ArchitectureService) -> ExchangeService:
    repository = PostgresExchangeRepository(create_session_factory(engine_at_head))
    return ExchangeService(repository, graph_service)


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


RIVAL_D = (
    "INSERT INTO elements (id, element_type, layer, aspect, name, properties, created_at, updated_at) VALUES "
    "(gen_random_uuid(), 'node', 'technology', 'active_structure', 'D', '{}', now(), now())"
)


async def test_a_refusal_in_the_middle_of_a_batch_leaves_links_between_the_rows_replayed(
    graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    # "D" is created by somebody else after the catalogue was read: the unique index refuses it
    importer = with_a_rival(engine_at_head, graph_service, RIVAL_D)
    data = exchange_xml(
        el("a", "Node", "A")
        + el("a-again", "Node", "A")
        + el("b", "Node", "B")
        + el("c", "Node", "C")
        + el("d", "Node", "D"),  # refused by the database, after A, B and C were created
        rel("r1", "Association", "a", "b")
        + rel("r2", "Association", "c", "a-again")
        + rel("r3", "Association", "d", "a"),
    )

    report = await importer.import_exchange(content(data), REAL)

    assert report.applied
    assert report.counts[Category.ELEMENTS].created == 3
    assert report.counts[Category.ELEMENTS].rejected == 1
    assert report.counts[Category.RELATIONSHIPS].created == 2
    assert [i.code for i in report.issues].count("duplicate_in_file") == 1
    async with engine_at_head.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT s.name, t.name FROM relationships r "
                "JOIN elements s ON s.id = r.source_id JOIN elements t ON t.id = r.target_id"
            )
        )
        assert {tuple(row) for row in rows} == {("A", "B"), ("C", "A")}
    assert await count(engine_at_head, "elements") == 4  # A, B, C and the rival's D
    # one creation event per element and link kept (the rival wrote none): nothing orphaned
    assert await count(engine_at_head, "audit_events") == 3 + 2


async def test_a_dry_run_leaves_neither_the_model_nor_the_trail(
    importer: ExchangeService, engine_at_head: AsyncEngine
) -> None:
    data = exchange_xml(
        el("a", "Node", "A") + el("c", "Node", "C"), rel("r", "Association", "a", "c")
    )

    report = await importer.import_exchange(content(data), ImportOptions())

    assert report.counts[Category.ELEMENTS].created == 2 and not report.applied
    assert await count(engine_at_head, "elements") == 0
    assert await count(engine_at_head, "relationships") == 0
    assert await count(engine_at_head, "audit_events") == 0


async def test_five_hundred_links_the_matrix_forbids_do_not_reject_the_file(
    importer: ExchangeService, engine_at_head: AsyncEngine
) -> None:
    actors = "".join(el(f"a{i}", "BusinessActor", f"actor{i}") for i in range(25))
    nodes = "".join(el(f"n{i}", "Node", f"node{i}") for i in range(20))
    links = "".join(
        rel(f"r{a}-{n}", "Specialization", f"a{a}", f"n{n}") for a in range(25) for n in range(20)
    )

    report = await importer.import_exchange(content(exchange_xml(actors + nodes, links)), REAL)

    assert report.applied and report.counts[Category.RELATIONSHIPS].rejected == 500
    assert await count(engine_at_head, "elements") == 45


async def test_linking_an_approved_element_sends_it_back_to_review_and_says_so(
    importer: ExchangeService, graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    approved = await graph_service.create_element(
        element_type=E.NODE, name="srv", properties={SOURCE_ID: "srv-1"}
    )
    await put_in_state(engine_at_head, approved.id, "approved")
    data = exchange_xml(el("n", "Node", "new"), rel("r", "Association", "n", "srv-1"))

    simulated = await importer.import_exchange(content(data), ImportOptions())
    assert (await state_of(engine_at_head, approved.id))[0] == "approved"
    real = await importer.import_exchange(content(data), REAL)

    assert [i.code for i in simulated.issues] == [i.code for i in real.issues]
    assert [i.code for i in real.issues] == ["approved_element_reopened"]
    assert (await state_of(engine_at_head, approved.id))[0] == "in_review"


async def test_a_link_to_a_locked_element_is_a_refused_row(
    importer: ExchangeService, graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    locked = await graph_service.create_element(
        element_type=E.NODE, name="srv", properties={SOURCE_ID: "srv-1"}
    )
    await put_in_state(engine_at_head, locked.id, "approved", locked=True)
    data = exchange_xml(el("n", "Node", "new"), rel("r", "Association", "n", "srv-1"))

    report = await importer.import_exchange(content(data), REAL)

    assert [i.code for i in report.issues] == ["element_locked"]
    assert report.counts[Category.ELEMENTS].created == 1
    assert await count(engine_at_head, "relationships") == 0


class Barrier(PostgresExchangeRepository):
    """Lets another transaction commit right after the catalogue is read, as a rival would."""

    def __init__(self, engine: AsyncEngine, rival: str) -> None:
        super().__init__(create_session_factory(engine))
        self._engine = engine
        self._rival = rival

    async def known_elements(self) -> tuple[Element, ...]:
        loaded = await super().known_elements()
        async with self._engine.begin() as other:  # another connection, committed at once
            await other.execute(text(self._rival))
        return loaded


def with_a_rival(engine: AsyncEngine, graph: ArchitectureService, rival: str) -> ExchangeService:
    return ExchangeService(Barrier(engine, rival), graph)


DESCRIBED = (
    '<properties><property propertyDefinitionRef="d"><value>from the file</value></property>'
    "</properties>"
)
DESCRIPTION_KEY = (
    '<propertyDefinitions><propertyDefinition identifier="d" type="string">'
    "<name>ea.description</name></propertyDefinition></propertyDefinitions>"
)


async def test_a_description_written_by_another_transaction_meanwhile_is_kept(
    graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    existing = await graph_service.create_element(element_type=E.NODE, name="srv")
    importer = with_a_rival(
        engine_at_head,
        graph_service,
        f"UPDATE elements SET description = 'theirs' WHERE id = '{existing.id}'",
    )
    data = exchange_xml(el("s", "Node", "srv", DESCRIBED), extra=DESCRIPTION_KEY)

    report = await importer.import_exchange(content(data), REAL)

    assert report.applied and report.counts[Category.ELEMENTS].reused == 1
    stored = await graph_service.get_element(existing.id)
    assert stored.description == "theirs" and stored.properties[SOURCE_ID] == "s"


async def test_an_element_approved_meanwhile_refuses_the_completion_and_the_import_goes_on(
    graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    existing = await graph_service.create_element(element_type=E.NODE, name="srv")
    importer = with_a_rival(
        engine_at_head,
        graph_service,
        f"UPDATE elements SET approval_state = 'approved', approved_by_sub = 'a', "
        f"approved_at = now() WHERE id = '{existing.id}'",
    )
    data = exchange_xml(el("s", "Node", "srv") + el("n", "Node", "new"))

    report = await importer.import_exchange(content(data), REAL)

    assert [i.code for i in report.issues] == ["completion_not_applied"]
    assert report.counts[Category.ELEMENTS].created == 1
    assert SOURCE_ID not in (await graph_service.get_element(existing.id)).properties


async def test_an_element_deleted_meanwhile_is_a_refused_row(
    graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    existing = await graph_service.create_element(element_type=E.NODE, name="srv")
    importer = with_a_rival(
        engine_at_head, graph_service, f"DELETE FROM elements WHERE id = '{existing.id}'"
    )
    data = exchange_xml(el("s", "Node", "srv") + el("n", "Node", "new"))

    report = await importer.import_exchange(content(data), REAL)

    assert [i.code for i in report.issues] == ["element_gone"]
    assert report.counts[Category.ELEMENTS].created == 1


async def test_a_hostile_file_gets_a_code_per_row_never_a_500(
    importer: ExchangeService, graph_service: ArchitectureService, engine_at_head: AsyncEngine
) -> None:
    await graph_service.create_element(
        element_type=E.NODE, name="taken", properties={"ip_address": "10.9.9.9"}
    )

    def prop(ref: str, value: str) -> str:
        return (
            f'<properties><property propertyDefinitionRef="{ref}"><value>{value}</value>'
            "</property></properties>"
        )

    data = exchange_xml(
        el("long", "Node", "n" * 10_000)
        + el("cidr", "Node", "badcidr", prop("c", "10.0.0.0/99"))
        + el("dup", "Node", "dup", prop("i", "10.9.9.9"))
        + el("good", "Node", "good"),
        extra='<propertyDefinitions><propertyDefinition identifier="c" type="string">'
        '<name>cidr</name></propertyDefinition><propertyDefinition identifier="i" type="string">'
        "<name>ip_address</name></propertyDefinition></propertyDefinitions>",
    )

    report = await importer.import_exchange(content(data), REAL)

    assert {i.source_id: i.code for i in report.issues} == {
        "long": "name_too_long",
        "cidr": "invalid_ipam_property",
        "dup": "address_taken",
    }
    assert await count(engine_at_head, "elements") == 2  # "taken" and "good"
