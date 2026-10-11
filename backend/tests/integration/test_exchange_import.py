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
from ea.repositories.exchange_store import PostgresExchangeRepository
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.exchange_files import el, exchange_xml, rel
from tests.integration.test_element_review import put_in_state, state_of

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]
REAL = ImportOptions(dry_run=False)
IP = (
    '<propertyDefinitions><propertyDefinition identifier="ip" type="string">'
    "<name>ip_address</name></propertyDefinition></propertyDefinitions>"
)


def content(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


def host(identifier: str, name: str) -> str:
    return el(
        identifier,
        "Node",
        name,
        '<properties><property propertyDefinitionRef="ip"><value>10.0.0.5</value>'
        "</property></properties>",
    )


@pytest.fixture
def importer(engine_at_head: AsyncEngine, graph_service: ArchitectureService) -> ExchangeService:
    repository = PostgresExchangeRepository(create_session_factory(engine_at_head))
    return ExchangeService(repository, graph_service)


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


async def test_a_refusal_in_the_middle_of_a_batch_leaves_links_between_the_rows_replayed(
    importer: ExchangeService, engine_at_head: AsyncEngine
) -> None:
    data = exchange_xml(
        el("a", "Node", "A")
        + el("a-again", "Node", "A")
        + el("b", "Node", "B")
        + host("c", "C")
        + host("d", "D"),  # refused by the unique index, after A, B and C were created
        rel("r1", "Association", "a", "b")
        + rel("r2", "Association", "c", "a-again")
        + rel("r3", "Association", "d", "a"),
        extra=IP,
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
    assert await count(engine_at_head, "elements") == 3
    # one creation event per element kept and per link: nothing orphaned by the rollback
    assert await count(engine_at_head, "audit_events") == 3 + 2


async def test_a_dry_run_leaves_neither_the_model_nor_the_trail(
    importer: ExchangeService, engine_at_head: AsyncEngine
) -> None:
    data = exchange_xml(
        el("a", "Node", "A") + host("c", "C") + host("d", "D"),
        rel("r", "Association", "a", "c"),
        extra=IP,
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
