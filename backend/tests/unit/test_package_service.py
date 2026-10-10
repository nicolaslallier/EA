"""The package use cases against in-memory stores: the rules that need the whole tree."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.diagrams import Diagram
from ea.domain.errors import (
    CyclicPackageError,
    DuplicatePackageError,
    ElementNotFoundError,
    NotAuthorisedError,
    PackageNotEmptyError,
    PackageNotFoundError,
    PackageTooDeepError,
    TooManyPackagesError,
)
from ea.domain.packages import MAX_MOVE_BATCH, MAX_PACKAGE_DEPTH
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.packages import PackageService
from tests.conftest import FIXED_NOW, InMemoryDiagrams, a_reader

pytestmark = pytest.mark.asyncio


async def an_element(service: ArchitectureService, name: str = "Billing"):  # type: ignore[no-untyped-def]
    return await service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)


class TestCreating:
    async def test_a_root_package_has_a_path_and_no_content(
        self, package_service: PackageService
    ) -> None:
        created = await package_service.create(name=" Finance ", description="Les comptes")

        assert created.package.name == "Finance"
        assert created.path == "Finance"
        assert (created.counts.elements, created.counts.diagrams, created.counts.children) == (
            0,
            0,
            0,
        )

    async def test_a_child_reads_its_whole_path(self, package_service: PackageService) -> None:
        finance = await package_service.create(name="Finance")

        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        assert budget.path == "Finance / Budget"

    async def test_a_parent_that_does_not_exist_is_a_not_found(
        self, package_service: PackageService
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.create(name="Budget", parent_id=uuid4())

    async def test_a_taken_sibling_name_is_refused_ignoring_case(
        self, package_service: PackageService
    ) -> None:
        await package_service.create(name="Finance")

        with pytest.raises(DuplicatePackageError):
            await package_service.create(name="FINANCE")

    async def test_a_name_with_a_slash_is_a_value_error(
        self, package_service: PackageService
    ) -> None:
        with pytest.raises(ValueError, match="/"):
            await package_service.create(name="a/b")

    async def test_the_tree_stops_at_the_maximum_depth(
        self, package_service: PackageService
    ) -> None:
        parent = None
        for level in range(MAX_PACKAGE_DEPTH):
            parent = (await package_service.create(name=f"p{level}", parent_id=parent)).package.id

        with pytest.raises(PackageTooDeepError):
            await package_service.create(name="trop", parent_id=parent)

    async def test_the_tree_is_capped(
        self, package_service: PackageService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.services.packages.MAX_PACKAGES", 1)
        await package_service.create(name="Un")

        with pytest.raises(TooManyPackagesError):
            await package_service.create(name="Deux")


class TestUpdating:
    async def test_renaming_keeps_the_parent(self, package_service: PackageService) -> None:
        finance = await package_service.create(name="Finance")
        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        renamed = await package_service.update(budget.package.id, name="Prévisions")

        assert renamed.path == "Finance / Prévisions"

    async def test_an_omitted_parent_is_kept_and_an_explicit_null_is_the_root(
        self, package_service: PackageService
    ) -> None:
        finance = await package_service.create(name="Finance")
        budget = await package_service.create(name="Budget", parent_id=finance.package.id)

        kept = await package_service.update(budget.package.id, description="x")
        at_root = await package_service.update(budget.package.id, parent_id=None)

        assert kept.package.parent_id == finance.package.id
        assert at_root.package.parent_id is None
        assert at_root.path == "Budget"

    async def test_a_package_cannot_go_under_its_own_descendant(
        self, package_service: PackageService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        c = await package_service.create(name="C", parent_id=b.package.id)

        with pytest.raises(CyclicPackageError):
            await package_service.update(a.package.id, parent_id=c.package.id)

    async def test_a_package_cannot_be_its_own_parent(
        self, package_service: PackageService
    ) -> None:
        a = await package_service.create(name="A")

        with pytest.raises(CyclicPackageError):
            await package_service.update(a.package.id, parent_id=a.package.id)

    async def test_moving_a_subtree_too_deep_is_refused(
        self, package_service: PackageService
    ) -> None:
        deep = None
        for level in range(MAX_PACKAGE_DEPTH - 1):
            deep = (await package_service.create(name=f"d{level}", parent_id=deep)).package.id
        top = await package_service.create(name="top")
        await package_service.create(name="child", parent_id=top.package.id)

        with pytest.raises(PackageTooDeepError):
            await package_service.update(top.package.id, parent_id=deep)

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.update(uuid4(), name="x")


class TestDeleting:
    async def test_an_empty_package_is_deleted(self, package_service: PackageService) -> None:
        a = await package_service.create(name="A")

        await package_service.delete(a.package.id)

        assert await package_service.list_packages() == ()

    async def test_a_package_holding_an_element_is_refused_without_a_target(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        with pytest.raises(PackageNotEmptyError, match="move_contents_to"):
            await package_service.delete(a.package.id)

    async def test_a_package_holding_only_a_diagram_is_refused_too(
        self,
        package_service: PackageService,
        diagrams: InMemoryDiagrams,
    ) -> None:
        a = await package_service.create(name="A")
        diagram = Diagram.create(name="Vente", now=FIXED_NOW, package_id=a.package.id)
        await diagrams.add(diagram)

        with pytest.raises(PackageNotEmptyError):
            await package_service.delete(a.package.id)

    async def test_with_a_target_the_contents_move_and_the_package_goes(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        other = await package_service.create(name="Autre")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        await package_service.delete(a.package.id, move_contents_to=other.package.id)

        assert (await service.get_element(element.id)).package_id == other.package.id
        assert (await package_service.get(b.package.id)).package.parent_id == other.package.id

    async def test_to_the_root_with_none(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        await package_service.delete(a.package.id, move_contents_to=None)

        assert (await service.get_element(element.id)).package_id is None

    async def test_the_target_cannot_be_a_descendant_of_the_deleted_package(
        self, package_service: PackageService
    ) -> None:
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)

        with pytest.raises(CyclicPackageError):
            await package_service.delete(a.package.id, move_contents_to=b.package.id)

        assert await package_service.get(a.package.id)

    async def test_a_name_taken_at_the_destination_moves_nothing(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        await package_service.create(name="Budget", parent_id=a.package.id)
        await package_service.create(name="Budget")
        element = await an_element(service)
        await package_service.move_elements([element.id], a.package.id)

        with pytest.raises(DuplicatePackageError):
            await package_service.delete(a.package.id, move_contents_to=None)

        assert (await service.get_element(element.id)).package_id == a.package.id

    async def test_moving_the_children_up_too_deep_is_refused(
        self, package_service: PackageService
    ) -> None:
        deep = None
        for level in range(MAX_PACKAGE_DEPTH - 1):
            deep = (await package_service.create(name=f"d{level}", parent_id=deep)).package.id
        a = await package_service.create(name="A")
        b = await package_service.create(name="B", parent_id=a.package.id)
        await package_service.create(name="C", parent_id=b.package.id)

        with pytest.raises(PackageTooDeepError):
            await package_service.delete(a.package.id, move_contents_to=deep)

    async def test_an_unknown_target_is_a_not_found(self, package_service: PackageService) -> None:
        a = await package_service.create(name="A")

        with pytest.raises(PackageNotFoundError):
            await package_service.delete(a.package.id, move_contents_to=uuid4())

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.delete(uuid4())


class TestMovingElements:
    async def test_elements_go_to_a_package_and_back_to_the_root(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        one, two = await an_element(service, "one"), await an_element(service, "two")

        assert await package_service.move_elements([one.id, two.id, one.id], a.package.id) == 2
        assert (await package_service.get(a.package.id)).counts.elements == 2

        await package_service.move_elements([one.id], None)
        assert (await package_service.get(a.package.id)).counts.elements == 1

    async def test_an_absent_element_moves_nothing(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        a = await package_service.create(name="A")
        kept = await an_element(service)

        with pytest.raises(ElementNotFoundError):
            await package_service.move_elements([kept.id, uuid4()], a.package.id)

        assert (await service.get_element(kept.id)).package_id is None

    async def test_an_absent_package_is_a_not_found(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(PackageNotFoundError):
            await package_service.move_elements([element.id], uuid4())

    async def test_more_than_the_batch_is_refused(self, package_service: PackageService) -> None:
        with pytest.raises(ValueError, match=str(MAX_MOVE_BATCH)):
            await package_service.move_elements([uuid4() for _ in range(MAX_MOVE_BATCH + 1)], None)

    async def test_an_empty_batch_is_a_no_op(self, package_service: PackageService) -> None:
        assert await package_service.move_elements([], None) == 0


class TestReading:
    async def test_the_listing_is_sorted_by_path_with_direct_counts(
        self, package_service: PackageService, service: ArchitectureService
    ) -> None:
        finance = await package_service.create(name="finance")
        await package_service.create(name="Budget", parent_id=finance.package.id)
        await package_service.create(name="Achats")
        element = await an_element(service)
        await package_service.move_elements([element.id], finance.package.id)

        listed = await package_service.list_packages()

        assert [(s.path, s.counts.elements, s.counts.children) for s in listed] == [
            ("Achats", 0, 0),
            ("finance", 1, 1),
            ("finance / Budget", 0, 0),
        ]

    async def test_a_reader_reads_but_cannot_write(self, package_service: PackageService) -> None:
        created = await package_service.create(name="A")

        with acting_as(a_reader()):
            assert [s.path for s in await package_service.list_packages()] == ["A"]
            assert (await package_service.get(created.package.id)).path == "A"
            with pytest.raises(NotAuthorisedError):
                await package_service.create(name="B")
            with pytest.raises(NotAuthorisedError):
                await package_service.update(created.package.id, name="C")
            with pytest.raises(NotAuthorisedError):
                await package_service.delete(created.package.id)
            with pytest.raises(NotAuthorisedError):
                await package_service.move_elements([], None)

    async def test_an_unknown_package_is_a_not_found(self, package_service: PackageService) -> None:
        with pytest.raises(PackageNotFoundError):
            await package_service.get(uuid4())


class TestTheAuditTrail:
    async def test_each_write_leaves_one_line_naming_what_changed(
        self,
        package_service: PackageService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="ea.services"):
            a = await package_service.create(name="A")
            await package_service.update(a.package.id, name="A2")
            element = await an_element(service)
            await package_service.move_elements([element.id], a.package.id)
            await package_service.delete(a.package.id, move_contents_to=None)

        actions = [
            getattr(r, "action", None) for r in caplog.records if r.name.startswith("ea.services")
        ]
        assert [x for x in actions if x and x.startswith(("package", "elements"))] == [
            "package_created",
            "package_updated",
            "elements_moved",
            "package_deleted",
        ]
        moved = next(r for r in caplog.records if getattr(r, "action", "") == "elements_moved")
        assert moved.count == 1  # type: ignore[attr-defined]
