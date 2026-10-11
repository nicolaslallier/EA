"""The structured search against a real PostgreSQL: the SQL is what is under test."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.domain.packages import Package
from ea.domain.ports import ElementFilter
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.package_store import PostgresPackageRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, engine: AsyncEngine) -> None:
        sessions = create_session_factory(engine)
        self.graph = PostgresArchitectureRepository(sessions)
        self.packages = PostgresPackageRepository(sessions)

    async def element(
        self,
        name: str,
        element_type: E = E.APPLICATION_COMPONENT,
        properties: dict[str, str] | None = None,
        at: datetime = NOW,
    ) -> Element:
        return await self.graph.add_element(
            Element.create(element_type=element_type, name=name, now=at, properties=properties),
            audit=(),
        )

    async def link(self, kind: R, source: Element, target: Element) -> None:
        await self.graph.add_relationship(
            Relationship.between(kind, source, target, now=NOW), audit=()
        )

    async def names(self, **query: Any) -> list[str]:
        criteria = ElementFilter.from_query(ElementQuery(**query))
        found = await self.graph.list_elements(criteria)
        return [element.name for element in found]

    async def total(self, **query: Any) -> int:
        return await self.graph.count_elements(ElementFilter.from_query(ElementQuery(**query)))


@pytest.fixture
def world(engine_at_head: AsyncEngine) -> World:
    return World(engine_at_head)


def only(*tests: PropertyTest) -> dict[str, Any]:
    return {"properties": tests}


class TestPropertyTests:
    async def seed(self, world: World) -> None:
        await world.element("billing", properties={"owner": "Finance", "note": "100% urgent_fix"})
        await world.element("ledger", properties={"owner": "Compta"})
        await world.element("crm")

    async def test_eq_is_exact(self, world: World) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "Finance"))) == [
            "billing"
        ]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "finance"))) == []

    async def test_contains_ignores_case_and_treats_wildcards_as_characters(
        self, world: World
    ) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.CONTAINS, "FINAN"))) == [
            "billing"
        ]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "100%"))) == [
            "billing"
        ]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "_fix"))) == [
            "billing"
        ]
        assert await world.names(**only(PropertyTest("note", PropertyOp.CONTAINS, "%"))) == [
            "billing"
        ]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.CONTAINS, "_"))) == []

    async def test_exists_and_absent_split_the_catalogue(self, world: World) -> None:
        await self.seed(world)

        assert sorted(await world.names(**only(PropertyTest("owner", PropertyOp.EXISTS)))) == [
            "billing",
            "ledger",
        ]
        assert await world.names(**only(PropertyTest("owner", PropertyOp.ABSENT))) == ["crm"]

    async def test_two_tests_are_combined_with_and(self, world: World) -> None:
        await self.seed(world)

        assert await world.names(
            properties=(
                PropertyTest("owner", PropertyOp.EXISTS),
                PropertyTest("note", PropertyOp.ABSENT),
            )
        ) == ["ledger"]

    async def test_a_value_carrying_sql_matches_nothing_and_breaks_nothing(
        self, world: World
    ) -> None:
        await self.seed(world)

        assert await world.names(**only(PropertyTest("owner", PropertyOp.EQ, "x' OR '1'='1"))) == []
        assert await world.total() == 3


class TestRelationTests:
    async def seed(self, world: World) -> None:
        self.service = await world.element("api", E.APPLICATION_SERVICE)
        self.process = await world.element("order to cash", E.BUSINESS_PROCESS)
        self.component = await world.element("billing", E.APPLICATION_COMPONENT)
        self.node = await world.element("db-01", E.NODE)
        self.lonely = await world.element("lonely", E.APPLICATION_COMPONENT)
        await world.link(R.SERVING, self.service, self.process)
        await world.link(R.ASSOCIATION, self.component, self.node)

    async def test_out_in_and_any(self, world: World) -> None:
        await self.seed(world)

        def serving(direction: Direction, **rest: Any) -> dict[str, Any]:
            return {"relations": (RelationTest(R.SERVING, direction, **rest),)}

        assert await world.names(**serving(Direction.OUT)) == ["api"]
        assert await world.names(**serving(Direction.IN)) == ["order to cash"]
        assert sorted(await world.names(**serving(Direction.ANY))) == ["api", "order to cash"]

    async def test_other_type_narrows_the_far_end(self, world: World) -> None:
        await self.seed(world)

        def to(other: E) -> dict[str, Any]:
            return {"relations": (RelationTest(R.SERVING, Direction.OUT, True, other),)}

        assert await world.names(**to(E.BUSINESS_PROCESS)) == ["api"]
        assert await world.names(**to(E.NODE)) == []

    async def test_absent_finds_what_has_no_such_link(self, world: World) -> None:
        await self.seed(world)

        no_association = RelationTest(R.ASSOCIATION, Direction.ANY, present=False)
        assert sorted(await world.names(relations=(no_association,))) == [
            "api",
            "lonely",
            "order to cash",
        ]

    async def test_absent_with_a_type_ignores_links_to_other_types(self, world: World) -> None:
        await self.seed(world)

        # `billing` is associated with a node, so it has no association with a process.
        no_association_to_a_process = RelationTest(
            R.ASSOCIATION, Direction.OUT, present=False, other_type=E.BUSINESS_PROCESS
        )
        found = await world.names(
            element_types=(E.APPLICATION_COMPONENT,), relations=(no_association_to_a_process,)
        )

        assert sorted(found) == ["billing", "lonely"]

    async def test_a_relation_no_type_can_have_is_an_empty_answer_not_an_error(
        self, world: World
    ) -> None:
        await self.seed(world)

        impossible = RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_ACTOR)
        assert await world.names(element_types=(E.DATA_OBJECT,), relations=(impossible,)) == []

    async def test_relations_combine_with_types_and_properties(self, world: World) -> None:
        await self.seed(world)
        await world.element("tagged service", E.APPLICATION_SERVICE, {"owner": "x"})

        found = await world.names(
            element_types=(E.APPLICATION_SERVICE,),
            properties=(PropertyTest("owner", PropertyOp.ABSENT),),
            relations=(RelationTest(R.SERVING, Direction.OUT),),
        )

        assert found == ["api"]


class TestCombinations:
    async def test_a_package_subtree_a_type_and_a_property_together(self, world: World) -> None:
        finance = await world.packages.add(Package.create(name="Finance", now=NOW), audit=())
        budget = await world.packages.add(
            Package.create(name="Budget", parent_id=finance.id, now=NOW),
            audit=(),
        )
        inside = await world.element("in", properties={"owner": "x"})
        also_inside = await world.element("also in", E.NODE, {"owner": "x"})
        outside = await world.element("out", properties={"owner": "x"})
        await world.packages.move_elements([inside.id, also_inside.id], budget.id, audit=())
        assert outside.package_id is None

        found = await world.names(
            package_id=finance.id,
            element_types=(E.APPLICATION_COMPONENT,),
            properties=(PropertyTest("owner", PropertyOp.EQ, "x"),),
        )

        assert found == ["in"]

    async def test_the_text_still_matches_a_name_fragment_literally(self, world: World) -> None:
        await world.element("db_01")
        await world.element("dbx01")

        assert await world.names(text="db_") == ["db_01"]

    async def test_the_text_is_never_a_pattern_nor_sql(self, world: World) -> None:
        await world.element("100% sure")
        await world.element("back\\slash")
        await world.element("plain")

        assert await world.names(text="%") == ["100% sure"]
        assert await world.names(text="\\") == ["back\\slash"]
        assert await world.names(text="x'; DROP TABLE elements; --") == []
        assert await world.total() == 3


class TestPagingAndOrder:
    async def test_a_page_and_the_total_of_the_whole(self, world: World) -> None:
        for name in ("a", "b", "c", "d", "e"):
            await world.element(name)

        assert await world.names(limit=2, offset=2) == ["c", "d"]
        assert await world.total(limit=2, offset=2) == 5

    async def test_by_date_the_newest_comes_first_and_by_name_the_alphabet(
        self, world: World
    ) -> None:
        await world.element("zulu", at=NOW)
        await world.element("alpha", at=NOW + timedelta(days=1))

        assert await world.names(order=QueryOrder.NAME) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.CREATED_AT) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.UPDATED_AT) == ["alpha", "zulu"]

    async def test_created_at_is_not_the_name_order(self, world: World) -> None:
        await world.element("alpha", at=NOW)
        await world.element("zulu", at=NOW + timedelta(days=1))

        assert await world.names(order=QueryOrder.NAME) == ["alpha", "zulu"]
        assert await world.names(order=QueryOrder.CREATED_AT) == ["zulu", "alpha"]

    async def test_ids_break_ties_so_a_page_never_repeats(self, world: World) -> None:
        for index in range(4):
            await world.element(f"n{index}", at=NOW)

        first = await world.names(order=QueryOrder.CREATED_AT, limit=2, offset=0)
        second = await world.names(order=QueryOrder.CREATED_AT, limit=2, offset=2)

        assert len({*first, *second}) == 4
