"""The package repository and the element filter by package, against a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    DuplicatePackageError,
    ElementNotFoundError,
    PackageNotFoundError,
)
from ea.domain.model import Element
from ea.domain.packages import Package
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.package_store import PostgresPackageRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, engine: AsyncEngine) -> None:
        sessions = create_session_factory(engine)
        self.packages = PostgresPackageRepository(sessions)
        self.graph = PostgresArchitectureRepository(sessions)

    async def package(self, name: str, parent: Package | None = None) -> Package:
        return await self.packages.add(
            Package.create(name=name, parent_id=parent.id if parent else None, now=NOW)
        )

    async def element(self, name: str, package: Package | None = None) -> Element:
        stored = await self.graph.add_element(
            Element.create(element_type=E.NODE, name=name, now=NOW)
        )
        if package is not None:
            await self.packages.move_elements([stored.id], package.id)
        return stored


@pytest.fixture
def world(engine_at_head: AsyncEngine) -> World:
    return World(engine_at_head)


async def names(world: World, **criteria: object) -> list[str]:
    wanted = ElementFilter(**criteria)  # type: ignore[arg-type]
    found = await world.graph.list_elements(wanted)
    assert await world.graph.count_elements(wanted) == len(found)
    return [element.name for element in found]


class TestSiblingNames:
    async def test_a_taken_root_name_is_refused_whatever_its_case(self, world: World) -> None:
        await world.package("Finance")

        with pytest.raises(DuplicatePackageError):
            await world.package("FINANCE")

    async def test_a_taken_child_name_is_refused_under_the_same_parent_only(
        self, world: World
    ) -> None:
        finance, rh = await world.package("Finance"), await world.package("RH")
        await world.package("Budget", finance)
        await world.package("Budget", rh)

        with pytest.raises(DuplicatePackageError):
            await world.package("budget", finance)

    async def test_renaming_onto_a_sibling_is_refused(self, world: World) -> None:
        await world.package("A")
        b = await world.package("B")

        with pytest.raises(DuplicatePackageError):
            await world.packages.save(b.revise(name="a", now=NOW))

    async def test_a_parent_that_is_gone_is_a_not_found(self, world: World) -> None:
        ghost = Package.create(name="Fantôme", now=NOW)

        with pytest.raises(PackageNotFoundError):
            await world.packages.add(Package.create(name="X", parent_id=ghost.id, now=NOW))


class TestListingAndCounting:
    async def test_counts_are_direct_not_recursive(self, world: World) -> None:
        finance = await world.package("Finance")
        budget = await world.package("Budget", finance)
        await world.element("a", finance)
        await world.element("b", budget)
        await world.element("c", budget)

        counts = await world.packages.counts()

        assert (counts[finance.id].elements, counts[finance.id].children) == (1, 1)
        assert (counts[budget.id].elements, counts[budget.id].diagrams) == (2, 0)

    async def test_list_all_is_sorted_ignoring_case(self, world: World) -> None:
        await world.package("beta")
        await world.package("Alpha")

        assert [p.name for p in await world.packages.list_all()] == ["Alpha", "beta"]


class TestDeleting:
    async def test_the_contents_move_up_in_the_same_transaction(self, world: World) -> None:
        finance = await world.package("Finance")
        budget = await world.package("Budget", finance)
        await world.element("a", finance)
        await world.element("b", budget)

        assert await world.packages.delete(finance.id, into=None) is True

        assert await world.packages.get(finance.id) is None
        moved = await world.packages.get(budget.id)
        assert moved is not None
        assert moved.parent_id is None
        assert await names(world, root_only=True) == ["a"]

    async def test_a_sibling_name_taken_at_the_destination_moves_nothing(
        self, world: World
    ) -> None:
        finance = await world.package("Finance")
        await world.package("Budget", finance)
        await world.package("Budget")  # a root "Budget" already
        await world.element("a", finance)

        with pytest.raises(DuplicatePackageError):
            await world.packages.delete(finance.id, into=None)

        assert await world.packages.get(finance.id) is not None
        assert await names(world, package_id=finance.id, include_subpackages=False) == ["a"]

    async def test_an_unknown_package_is_not_deleted(self, world: World) -> None:
        assert await world.packages.delete(uuid4(), into=None) is False


class TestMovingElements:
    async def test_all_or_none(self, world: World) -> None:
        finance = await world.package("Finance")
        kept = await world.element("a")

        with pytest.raises(ElementNotFoundError):
            await world.packages.move_elements([kept.id, uuid4()], finance.id)

        stored = await world.graph.get_element(kept.id)
        assert stored is not None
        assert stored.package_id is None

    async def test_to_a_package_that_is_gone_is_a_not_found(self, world: World) -> None:
        element = await world.element("a")

        with pytest.raises(PackageNotFoundError):
            await world.packages.move_elements([element.id], uuid4())

    async def test_back_to_the_root(self, world: World) -> None:
        finance = await world.package("Finance")
        element = await world.element("a", finance)

        await world.packages.move_elements([element.id], None)

        stored = await world.graph.get_element(element.id)
        assert stored is not None
        assert stored.package_id is None

    async def test_saving_an_element_read_before_a_move_does_not_undo_the_move(
        self, world: World
    ) -> None:
        finance = await world.package("Finance")
        stale = await world.element("a")
        await world.packages.move_elements([stale.id], finance.id)

        await world.graph.save_element(stale.rename("a2", now=NOW))

        stored = await world.graph.get_element(stale.id)
        assert stored is not None
        assert stored.package_id == finance.id


class TestTheFilter:
    async def seed(self, world: World) -> Package:
        a = await world.package("A")
        b = await world.package("B", a)
        c = await world.package("C", b)
        await world.element("in-a", a)
        await world.element("in-b", b)
        await world.element("in-c", c)
        await world.element("at-root")
        return a

    async def test_a_package_includes_its_subtree_by_default(self, world: World) -> None:
        a = await self.seed(world)

        assert sorted(await names(world, package_id=a.id)) == ["in-a", "in-b", "in-c"]

    async def test_without_subpackages_only_the_package_itself(self, world: World) -> None:
        a = await self.seed(world)

        assert await names(world, package_id=a.id, include_subpackages=False) == ["in-a"]

    async def test_a_middle_package_takes_what_is_below_it(self, world: World) -> None:
        await self.seed(world)
        b = next(p for p in await world.packages.list_all() if p.name == "B")

        assert sorted(await names(world, package_id=b.id)) == ["in-b", "in-c"]

    async def test_root_only_keeps_the_unfiled(self, world: World) -> None:
        await self.seed(world)

        assert await names(world, root_only=True) == ["at-root"]

    async def test_an_unknown_package_matches_nothing(self, world: World) -> None:
        await self.seed(world)

        assert await names(world, package_id=uuid4()) == []
