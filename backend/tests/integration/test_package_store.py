"""The package repository and the element filter by package, against a real PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    CyclicPackageError,
    DuplicatePackageError,
    ElementNotFoundError,
    PackageNotEmptyError,
    PackageNotFoundError,
    PackageTooDeepError,
)
from ea.domain.model import Element
from ea.domain.packages import MAX_PACKAGE_DEPTH, Package
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


class TestTheTreeUnderConcurrency:
    """The tree's invariants are checked and written under one lock (docs/adr/0042)."""

    async def test_two_moves_that_would_each_close_a_cycle_let_only_one_through(
        self, world: World
    ) -> None:
        for _ in range(5):  # a race is not proven by one lucky ordering
            a = await world.package(f"A{_}")
            b = await world.package(f"B{_}")

            outcomes = await asyncio.gather(
                world.packages.save(a.moved_to(b.id, now=NOW)),
                world.packages.save(b.moved_to(a.id, now=NOW)),
                return_exceptions=True,
            )

            assert sum(isinstance(o, CyclicPackageError) for o in outcomes) == 1
            assert sum(isinstance(o, Package) for o in outcomes) == 1

    async def test_a_move_and_a_creation_that_together_exceed_the_depth_let_only_one_through(
        self, world: World
    ) -> None:
        chain = [await world.package("p0")]
        for level in range(1, MAX_PACKAGE_DEPTH - 2):
            chain.append(await world.package(f"p{level}", chain[-1]))
        s = await world.package("s")
        s1 = await world.package("s1", s)
        # Moved alone, s lands at depth 11 and s1 at 12; a child of s1 alone is fine too.
        outcomes = await asyncio.gather(
            world.packages.save(s.moved_to(chain[-1].id, now=NOW)),
            world.packages.add(Package.create(name="s2", parent_id=s1.id, now=NOW)),
            return_exceptions=True,
        )

        assert sum(isinstance(o, PackageTooDeepError) for o in outcomes) == 1
        assert sum(isinstance(o, Package) for o in outcomes) == 1

    async def test_the_repository_refuses_a_cycle_even_without_the_service(
        self, world: World
    ) -> None:
        a = await world.package("A")
        b = await world.package("B", a)

        with pytest.raises(CyclicPackageError):
            await world.packages.save(a.moved_to(b.id, now=NOW))


class TestDeletingAnEmptyPackageOnly:
    """`require_empty` is the service's "no move_contents_to": never a silent re-rooting."""

    async def test_a_package_that_now_holds_an_element_is_refused_and_the_element_stays(
        self, world: World
    ) -> None:
        finance = await world.package("Finance")
        element = await world.element("a", finance)

        with pytest.raises(PackageNotEmptyError):
            await world.packages.delete(finance.id, into=None, require_empty=True)

        stored = await world.graph.get_element(element.id)
        assert stored is not None
        assert stored.package_id == finance.id

    async def test_an_empty_package_is_deleted(self, world: World) -> None:
        finance = await world.package("Finance")

        assert await world.packages.delete(finance.id, into=None, require_empty=True) is True

    async def test_a_delete_racing_a_filing_never_re_roots_the_element(self, world: World) -> None:
        for round_ in range(8):
            finance = await world.package(f"F{round_}")
            element = await world.element(f"e{round_}")

            outcomes = await asyncio.gather(
                world.packages.delete(finance.id, into=None, require_empty=True),
                world.packages.move_elements([element.id], finance.id),
                return_exceptions=True,
            )

            stored = await world.graph.get_element(element.id)
            assert stored is not None
            deleted = await world.packages.get(finance.id) is None
            if deleted:
                assert outcomes[0] is True
                assert isinstance(outcomes[1], PackageNotFoundError)
                assert stored.package_id is None
            else:
                assert isinstance(outcomes[0], PackageNotEmptyError)
                assert outcomes[1] is None
                assert stored.package_id == finance.id
