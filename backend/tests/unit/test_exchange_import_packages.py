"""Importing `organizations`: folders become packages, new elements are filed, nothing is moved."""

from __future__ import annotations

import dataclasses
from uuid import UUID, uuid4

import pytest

from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction
from ea.domain.errors import DuplicatePackageError, NotAuthorisedError, PackageNotFoundError
from ea.domain.exchange import (
    Category,
    ExchangeFormat,
    ImportContent,
    ImportOptions,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.exchanges import ExchangeService
from ea.services.packages import PackageService
from tests.conftest import FIXED_NOW, InMemoryExchange, InMemoryRepository, a_reader
from tests.exchange_files import el, exchange_xml

pytestmark = pytest.mark.asyncio
REAL = ImportOptions(dry_run=False)


@pytest.fixture
def importer(
    exchange_store: InMemoryExchange,
    service: ArchitectureService,
    package_service: PackageService,
) -> ExchangeService:
    return ExchangeService(
        exchange_store, service, packages=package_service, clock=lambda: FIXED_NOW
    )


def xml(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


def folder(label: str, *inner: str, refs: tuple[str, ...] = ()) -> str:
    items = "".join(f'<item identifierRef="{r}"/>' for r in refs)
    return f"<item><label>{label}</label>{items}{''.join(inner)}</item>"


def organized(*items: str, elements: str = "") -> ImportContent:
    return xml(
        exchange_xml(
            elements or el("a", "Node", "A") + el("b", "Node", "B") + el("c", "Node", "C"),
            extra=f"<organizations>{''.join(items)}</organizations>",
        )
    )


ORGANIZED = organized(
    folder("Infra", folder("Net", refs=("b",)), refs=("a",)),
)


async def names(package_service: PackageService) -> dict[str, UUID]:
    return {s.package.name: s.package.id for s in await package_service.list_packages()}


def packaged(repository: InMemoryRepository) -> dict[str, UUID | None]:
    return {e.name: e.package_id for e in repository.elements.values()}


async def test_folders_become_packages_and_created_elements_are_filed(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    report = await importer.import_exchange(ORGANIZED, REAL)

    assert report.counts[Category.PACKAGES].created == 2
    where = await names(package_service)
    summary = {s.package.name: s.package for s in await package_service.list_packages()}
    assert summary["Net"].parent_id == where["Infra"]
    assert packaged(repository) == {"A": where["Infra"], "B": where["Net"], "C": None}


async def test_an_existing_package_of_another_case_is_reused(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    existing = (await package_service.create(name="infra")).package.id

    report = await importer.import_exchange(ORGANIZED, REAL)

    assert (report.counts[Category.PACKAGES].reused, report.counts[Category.PACKAGES].created) == (
        1,
        1,
    )
    assert packaged(repository)["A"] == existing


async def test_the_target_receives_the_new_folders_and_the_new_elements_no_folder_lists(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    target = (await package_service.create(name="Landing")).package.id

    await importer.import_exchange(
        ORGANIZED, ImportOptions(dry_run=False, target_package_id=target)
    )

    summary = {s.package.name: s.package for s in await package_service.list_packages()}
    assert summary["Infra"].parent_id == target
    assert packaged(repository)["C"] == target
    assert packaged(repository)["A"] == summary["Infra"].id


async def test_an_unknown_target_is_a_404_and_nothing_is_written(
    importer: ExchangeService, repository: InMemoryRepository
) -> None:
    with pytest.raises(PackageNotFoundError):
        await importer.import_exchange(
            ORGANIZED, ImportOptions(dry_run=False, target_package_id=uuid4())
        )

    assert repository.elements == {}


async def test_an_element_already_there_is_not_moved_and_it_is_said(
    importer: ExchangeService,
    service: ArchitectureService,
    package_service: PackageService,
    repository: InMemoryRepository,
) -> None:
    elsewhere = (await package_service.create(name="Elsewhere")).package.id
    existing = await service.create_element(element_type=E.NODE, name="A")
    repository.elements[existing.id] = dataclasses.replace(existing, package_id=elsewhere)

    report = await importer.import_exchange(ORGANIZED, REAL)

    assert repository.elements[existing.id].package_id == elsewhere  # filed where it was
    assert "package_not_applied" in {i.code for i in report.issues}


async def test_an_approved_element_in_a_folder_is_not_moved_and_nothing_is_refused(
    importer: ExchangeService,
    service: ArchitectureService,
    package_service: PackageService,
    repository: InMemoryRepository,
) -> None:
    existing = await service.create_element(element_type=E.NODE, name="A")
    repository.elements[existing.id] = dataclasses.replace(
        existing, approval_state=ApprovalState.APPROVED
    )

    report = await importer.import_exchange(ORGANIZED, REAL)

    assert repository.elements[existing.id].package_id is None
    assert report.counts[Category.ELEMENTS].rejected == 0
    assert report.counts[Category.PACKAGES].rejected == 0


async def test_a_label_a_package_cannot_carry_is_rejected_with_its_subtree(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    content = organized(folder("A/B", folder("Inner", refs=("a",))), folder("Fine", refs=("b",)))

    report = await importer.import_exchange(content, REAL)

    assert report.counts[Category.PACKAGES].rejected == 2  # the folder and the one below it
    assert report.counts[Category.PACKAGES].created == 1
    assert sorted(await names(package_service)) == ["Fine"]
    assert packaged(repository)["A"] is None  # unfiled: its folder never existed
    codes = {i.code for i in report.issues}
    assert {"invalid_package_name", "parent_rejected"} <= codes


async def test_two_siblings_differing_by_case_are_a_duplicate_row(
    importer: ExchangeService, package_service: PackageService
) -> None:
    report = await importer.import_exchange(organized(folder("Net"), folder("net")), REAL)

    assert report.counts[Category.PACKAGES].created == 1
    assert report.counts[Category.PACKAGES].rejected == 1
    assert "duplicate_package" in {i.code for i in report.issues}


async def test_a_chain_far_deeper_than_the_tree_allows_costs_no_refused_row(
    importer: ExchangeService, package_service: PackageService
) -> None:
    chain = folder("leaf")
    for level in range(199, 0, -1):
        chain = folder(f"l{level}", chain)

    report = await importer.import_exchange(organized(chain), REAL)

    # 12 levels fit; the 188 below are rejected in memory: the database refused nothing,
    # so the budget of 100 refusals was not touched.
    assert report.counts[Category.PACKAGES].created == 12
    assert report.counts[Category.PACKAGES].rejected == 188
    assert "package_too_deep" in {i.code for i in report.issues}


async def test_more_packages_than_the_tree_holds_are_rejected_in_memory(
    importer: ExchangeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ea.domain.exchange.MAX_PACKAGES", 2)

    report = await importer.import_exchange(
        organized(folder("A"), folder("B"), folder("C"), folder("D")), REAL
    )

    assert report.counts[Category.PACKAGES].created == 2
    assert report.counts[Category.PACKAGES].rejected == 2
    assert "too_many_packages" in {i.code for i in report.issues}


async def test_the_same_file_twice_creates_and_moves_nothing(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    await importer.import_exchange(ORGANIZED, REAL)
    packaged_first = packaged(repository)

    again = await importer.import_exchange(ORGANIZED, REAL)

    assert again.counts[Category.PACKAGES].created == 0
    assert again.counts[Category.PACKAGES].reused == 2
    assert packaged(repository) == packaged_first
    assert "package_not_applied" not in {i.code for i in again.issues}


async def test_an_element_listed_in_two_folders_stays_in_the_first(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    report = await importer.import_exchange(
        organized(folder("One", refs=("a",)), folder("Two", refs=("a", "b"))), REAL
    )

    where = await names(package_service)
    assert packaged(repository)["A"] == where["One"]
    assert packaged(repository)["B"] == where["Two"]
    assert "package_duplicate_item" in {i.code for i in report.issues}


async def test_a_dry_run_writes_no_package(
    importer: ExchangeService, package_service: PackageService, repository: InMemoryRepository
) -> None:
    report = await importer.import_exchange(ORGANIZED, ImportOptions())

    assert report.counts[Category.PACKAGES].created == 2
    assert await names(package_service) == {}
    assert repository.elements == {}


async def test_a_reader_cannot_import_folders(
    importer: ExchangeService, package_service: PackageService
) -> None:
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await importer.import_exchange(ORGANIZED, REAL)


async def test_the_work_is_audited_by_the_package_service(
    importer: ExchangeService, repository: InMemoryRepository
) -> None:
    await importer.import_exchange(ORGANIZED, REAL)

    actions = {event.action for event in repository.audit_log.events}
    assert {AuditAction.PACKAGE_CREATED, AuditAction.PACKAGE_ELEMENTS_MOVED} <= actions


async def test_a_package_the_database_refuses_is_a_rejected_row_and_the_others_stay(
    importer: ExchangeService, package_service: PackageService, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = package_service.create

    async def refusing(**kwargs: object):  # type: ignore[no-untyped-def]
        if kwargs["name"] == "Bad":
            raise DuplicatePackageError("a concurrent creation took it")
        return await real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(package_service, "create", refusing)

    report = await importer.import_exchange(
        organized(folder("Good1", refs=("a",)), folder("Bad", refs=("b",)), folder("Good2")), REAL
    )

    assert sorted(await names(package_service)) == ["Good1", "Good2"]
    assert report.counts[Category.PACKAGES].rejected == 1
    assert "duplicate_package" in {i.code for i in report.issues}


async def test_new_elements_are_filed_a_hundred_at_a_time(
    importer: ExchangeService,
    package_service: PackageService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = package_service.move_elements

    async def spy(element_ids, package_id):  # type: ignore[no-untyped-def]
        calls.append(len(element_ids))
        return await real(element_ids, package_id)

    monkeypatch.setattr(package_service, "move_elements", spy)
    elements = "".join(el(f"e{i}", "Node", f"n{i}") for i in range(250))

    await importer.import_exchange(
        organized(folder("Big", refs=tuple(f"e{i}" for i in range(250))), elements=elements), REAL
    )

    assert calls == [100, 100, 50]


async def test_the_tree_is_locked_before_the_elements(
    importer: ExchangeService, exchange_store: InMemoryExchange
) -> None:
    await importer.import_exchange(ORGANIZED, REAL)

    assert exchange_store.calls == ["tree", "elements"]


async def test_a_file_without_folders_never_touches_the_tree_lock(
    importer: ExchangeService, exchange_store: InMemoryExchange
) -> None:
    await importer.import_exchange(xml(exchange_xml(el("a", "Node", "A"))), REAL)

    assert exchange_store.calls == ["elements"]


async def test_folders_are_ignored_and_said_when_packages_are_not_available(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    bare = ExchangeService(exchange_store, service, clock=lambda: FIXED_NOW)

    report = await bare.import_exchange(ORGANIZED, REAL)

    assert "packages_not_available" in {i.code for i in report.issues}
    assert report.counts[Category.ELEMENTS].created == 3


async def test_a_package_the_database_refuses_takes_its_subtree_with_it_and_files_nothing_wrongly(
    importer: ExchangeService,
    package_service: PackageService,
    repository: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = package_service.create

    async def refusing(**kwargs: object):  # type: ignore[no-untyped-def]
        if kwargs["name"] == "Bad":
            raise DuplicatePackageError("a concurrent creation took it")
        return await real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(package_service, "create", refusing)
    # One refusal is all the budget allows: the children of "Bad" are deduced from it, in
    # memory, and must not be counted as refusals of the database.
    monkeypatch.setattr("ea.services.exchange_import.MAX_REFUSED_ROWS", 1)
    content = organized(
        folder("Bad", folder("Child", folder("Grandchild", refs=("c",)), refs=("b",)), refs=("a",)),
        folder("Good"),
    )

    report = await importer.import_exchange(content, REAL)

    assert sorted(await names(package_service)) == ["Good"]  # nothing created at the root
    assert packaged(repository) == {"A": None, "B": None, "C": None}
    assert report.counts[Category.PACKAGES].rejected == 3  # Bad, Child, Grandchild
    assert report.counts[Category.PACKAGES].created == 1
    errors = [i.code for i in report.issues if i.severity.value == "error"]
    assert errors == ["duplicate_package"]  # one line for the folder that was refused
    assert "parent_rejected" not in errors


async def test_a_refused_package_leaves_no_trace_in_the_paths_of_the_import(
    importer: ExchangeService,
    package_service: PackageService,
    repository: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first row of a batch has no replay to rewind its entry: the entry must go anyway."""
    real = package_service.move_elements

    async def refusing(element_ids, package_id):  # type: ignore[no-untyped-def]
        raise PackageNotFoundError("the package vanished")

    monkeypatch.setattr(package_service, "move_elements", refusing)
    content = organized(folder("First", folder("Below", refs=("b",)), refs=("a",)))

    report = await importer.import_exchange(content, REAL)

    assert await names(package_service) == {}
    assert packaged(repository) == {"A": None, "B": None, "C": None}
    assert report.counts[Category.PACKAGES].rejected == 2
    assert real  # (the real one is not called: the first filing fails)


async def test_an_import_creates_at_most_so_many_packages_the_rest_is_rejected_in_memory(
    exchange_store: InMemoryExchange,
    service: ArchitectureService,
    package_service: PackageService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = ExchangeService(
        exchange_store,
        service,
        packages=package_service,
        max_new_folders=2,
        clock=lambda: FIXED_NOW,
    )
    # No refusal of the database is tolerated: the excess must be told in memory.
    monkeypatch.setattr("ea.services.exchange_import.MAX_REFUSED_ROWS", 0)

    report = await importer.import_exchange(organized(*[folder(f"F{i}") for i in range(10)]), REAL)

    assert report.counts[Category.PACKAGES].created == 2
    assert report.counts[Category.PACKAGES].rejected == 8
    assert {i.code for i in report.issues} == {"too_many_folders"}


async def test_a_folder_that_exists_does_not_count_towards_the_creations(
    exchange_store: InMemoryExchange,
    service: ArchitectureService,
    package_service: PackageService,
) -> None:
    importer = ExchangeService(
        exchange_store,
        service,
        packages=package_service,
        max_new_folders=1,
        clock=lambda: FIXED_NOW,
    )
    for name in ("Old1", "Old2", "Old3"):
        await package_service.create(name=name)

    report = await importer.import_exchange(
        organized(*[folder(n) for n in ("Old1", "Old2", "Old3", "New")]), REAL
    )

    assert report.counts[Category.PACKAGES].reused == 3
    assert report.counts[Category.PACKAGES].created == 1
    assert report.counts[Category.PACKAGES].rejected == 0
