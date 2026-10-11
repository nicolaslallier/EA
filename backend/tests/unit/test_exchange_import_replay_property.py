"""A replayed batch must land exactly where a run that never refused anything lands.

For many random files (colliding names, clashing addresses, links that close a containment
loop, rows the database refuses at write time) and for several batch sizes, the graph an
import leaves must equal the graph of a second import of the same file *without* the rows
the first one refused, applied in a single batch. If the replay lost an id, doubled a
warning or kept a stale mapping, the two graphs differ. The seed is fixed, the cases are
many, and `test_the_cases_exercise_refusals_and_replays` fails if they stop being any.
"""

from __future__ import annotations

import random

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import DuplicateElementError
from ea.domain.exchange import Category, ExchangeFormat, ImportContent, ImportOptions, Severity
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryExchange, InMemoryRepository
from tests.exchange_files import el, exchange_xml, rel

pytestmark = pytest.mark.asyncio
SEEDS = 300
BATCHES = (1, 2, 3, 5)
DEFINITIONS = (
    '<propertyDefinitions><propertyDefinition identifier="ip" type="string">'
    "<name>ip_address</name></propertyDefinition></propertyDefinitions>"
)


def a_file(
    seed: int,
) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str, str]], set[str]]:
    rng = random.Random(seed)
    count = rng.randint(3, 14)
    elements = []
    for number in range(count):
        name = f"n{rng.randint(0, count)}"  # (type, name) collisions inside the file
        ip = f"10.0.0.{rng.randint(1, max(2, count // 2))}" if rng.random() < 0.6 else None
        elements.append((f"e{number}", name, ip))
    links = [
        (f"r{k}", f"e{rng.randrange(count)}", f"e{rng.randrange(count)}")
        for k in range(rng.randint(0, 12))
    ]
    refused_names = {f"n{k}" for k in range(3, count + 1) if rng.random() < 0.2}
    return elements, links, refused_names


def content(
    elements: list[tuple[str, str, str | None]], links: list[tuple[str, str, str]], drop: set[str]
) -> ImportContent:
    def host(identifier: str, name: str, ip: str | None) -> str:
        props = (
            f'<properties><property propertyDefinitionRef="ip"><value>{ip}</value></property>'
            "</properties>"
            if ip
            else ""
        )
        return el(identifier, "Node", name, props)

    rows = "".join(host(i, n, ip) for i, n, ip in elements if i not in drop)
    chain = "".join(rel(i, "Composition", s, t) for i, s, t in links if not {i, s, t} & drop)
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, exchange_xml(rows, chain, DEFINITIONS))


async def world(
    batch: int, refused_names: set[str]
) -> tuple[InMemoryRepository, InMemoryExchange, ExchangeService]:
    repository = InMemoryRepository(InMemoryAuditLog())
    service = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    await service.create_element(element_type=E.NODE, name="n1")
    await service.create_element(element_type=E.NODE, name="n2")
    real = service.create_element

    async def create(**kwargs: object) -> object:
        if kwargs["name"] in refused_names:
            raise DuplicateElementError("created by somebody else meanwhile")
        return await real(**kwargs)  # type: ignore[arg-type]

    service.create_element = create  # type: ignore[method-assign]
    store = InMemoryExchange(repository)
    store.batch_size = batch
    return repository, store, ExchangeService(store, service, clock=lambda: FIXED_NOW)


def graph(repository: InMemoryRepository) -> tuple[object, object]:
    names = {uid: e.name for uid, e in repository.elements.items()}
    elements = sorted(
        (e.name, tuple(sorted(e.properties.items())), e.description)
        for e in repository.elements.values()
    )
    links = sorted(
        (
            r.relationship_type.value,
            names[r.source_id],
            names[r.target_id],
            tuple(sorted(r.properties.items())),
        )
        for r in repository.relationships.values()
    )
    return elements, links


async def one_case(seed: int, batch: int) -> tuple[bool, int]:
    elements, links, refused_names = a_file(seed)
    repository, store, importer = await world(batch, refused_names)
    report = await importer.import_exchange(
        content(elements, links, set()), ImportOptions(dry_run=False)
    )
    drop = {i.source_id for i in report.issues if i.severity is Severity.ERROR and i.source_id}
    oracle_repository, _, oracle = await world(10**9, set())
    expected = await oracle.import_exchange(
        content(elements, links, drop), ImportOptions(dry_run=False)
    )

    errors = [i for i in expected.issues if i.severity is Severity.ERROR]
    assert not errors, (seed, batch, errors)
    assert graph(repository) == graph(oracle_repository), (seed, batch, report.issues)
    for category in (Category.ELEMENTS, Category.RELATIONSHIPS):
        got, want = report.counts[category], expected.counts[category]
        assert (got.created, got.reused) == (want.created, want.reused), (seed, batch, category)
    return report.counts[Category.ELEMENTS].rejected > 0, store.replayed


async def test_a_replayed_batch_lands_where_a_run_without_refusals_lands() -> None:
    for seed in range(SEEDS):
        for batch in BATCHES:
            await one_case(seed, batch)


async def test_the_cases_exercise_refusals_and_replays() -> None:
    refusing = replays = 0
    for seed in range(SEEDS):
        for batch in (2, 3):
            refused, replayed = await one_case(seed, batch)
            refusing += refused
            replays += replayed > 0
    assert refusing > 100 and replays > 100
