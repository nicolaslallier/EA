"""The Cypher, against a real Neo4j.

Everything here is a claim about the database rather than about Python: that a
constraint actually rejects a duplicate, that a variable-length traversal
returns what it should, that impact analysis walks each relationship the right
way round. None of it can be proved with a double.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateElementError
from ea.domain.ports import ElementFilter
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestElementPersistence:
    async def test_an_element_survives_a_round_trip_with_its_properties(
        self, graph_service: ArchitectureService
    ) -> None:
        created = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            description="Invoices customers",
            properties={"owner": "finance", "criticality": "high"},
        )

        fetched = await graph_service.get_element(created.id)

        assert fetched.name == "Billing"
        assert fetched.element_type is E.APPLICATION_COMPONENT
        assert fetched.description == "Invoices customers"
        assert dict(fetched.properties) == {"owner": "finance", "criticality": "high"}
        assert fetched.created_at == created.created_at

    async def test_two_elements_of_one_type_cannot_share_a_name(
        self, graph_service: ArchitectureService
    ) -> None:
        """Enforced by a uniqueness constraint, not by a read-then-write race."""
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

        with pytest.raises(DuplicateElementError):
            await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

    async def test_the_same_name_is_fine_under_a_different_type(
        self, graph_service: ArchitectureService
    ) -> None:
        """A "Billing" capability and a "Billing" application are different things."""
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

        other = await graph_service.create_element(element_type=E.CAPABILITY, name="Billing")

        assert other.element_type is E.CAPABILITY

    async def test_an_update_removes_a_property_it_no_longer_carries(
        self, graph_service: ArchitectureService
    ) -> None:
        """`SET e = $properties` replaces the map, so a dropped key really goes."""
        element = await graph_service.create_element(
            element_type=E.NODE, name="db-01", properties={"rack": "A1", "owner": "ops"}
        )

        updated = await graph_service.update_element(element.id, properties={"owner": "ops"})

        assert dict(updated.properties) == {"owner": "ops"}
        assert dict((await graph_service.get_element(element.id)).properties) == {"owner": "ops"}

    async def test_deleting_an_element_takes_its_relationships_with_it(
        self, graph_service: ArchitectureService
    ) -> None:
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        link = await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        await graph_service.delete_element(api.id)

        assert await graph_service._repository.get_relationship(link.id) is None

    async def test_the_catalogue_filters_by_layer(self, graph_service: ArchitectureService) -> None:
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        await graph_service.create_element(element_type=E.NODE, name="db-01")

        found = await graph_service.list_elements(ElementFilter(layers=(E.NODE.layer,)))

        assert [element.name for element in found] == ["db-01"]

    async def test_the_catalogue_search_ignores_case(
        self, graph_service: ArchitectureService
    ) -> None:
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

        found = await graph_service.list_elements(ElementFilter(search="BILL"))

        assert [element.name for element in found] == ["Billing"]


class TestRelationshipPersistence:
    async def test_a_relationship_survives_a_round_trip(
        self, graph_service: ArchitectureService
    ) -> None:
        function = await graph_service.create_element(
            element_type=E.APPLICATION_FUNCTION, name="Invoicing"
        )
        data = await graph_service.create_element(element_type=E.DATA_OBJECT, name="Invoice")

        created = await graph_service.connect(
            relationship_type=R.ACCESS, source_id=function.id, target_id=data.id
        )
        fetched = await graph_service.get_relationship(created.id)

        assert fetched.relationship_type is R.ACCESS
        assert fetched.source_id == function.id
        assert fetched.access_type is not None

    async def test_relationships_can_be_listed_for_one_element(
        self, graph_service: ArchitectureService
    ) -> None:
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        first = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        second = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Dunning")
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=first.id
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=second.id
        )

        found = await graph_service.list_relationships(element_id=api.id)

        assert len(found) == 2


class TestContainmentCycles:
    async def test_a_loop_is_detected_across_several_hops(
        self,
        graph_repository: Neo4jArchitectureRepository,
        graph_service: ArchitectureService,
    ) -> None:
        """Platform contains Payments contains Ledger.

        Putting Platform *inside* Ledger would close the loop; the check is a
        Cypher reachability query, not a Python graph walk.
        """
        outer = await graph_service.create_element(element_type=E.GROUPING, name="Platform")
        middle = await graph_service.create_element(element_type=E.GROUPING, name="Payments")
        inner = await graph_service.create_element(element_type=E.GROUPING, name="Ledger")
        await graph_service.connect(
            relationship_type=R.COMPOSITION, source_id=outer.id, target_id=middle.id
        )
        await graph_service.connect(
            relationship_type=R.COMPOSITION, source_id=middle.id, target_id=inner.id
        )

        assert await graph_repository.would_close_a_containment_cycle(inner.id, outer.id)

    async def test_a_shortcut_down_the_same_branch_is_not_a_loop(
        self,
        graph_repository: Neo4jArchitectureRepository,
        graph_service: ArchitectureService,
    ) -> None:
        """Platform already contains Ledger through Payments.

        Adding the direct edge is redundant, but it closes nothing, so the
        cycle check must not be the one to refuse it.
        """
        outer = await graph_service.create_element(element_type=E.GROUPING, name="Platform")
        middle = await graph_service.create_element(element_type=E.GROUPING, name="Payments")
        inner = await graph_service.create_element(element_type=E.GROUPING, name="Ledger")
        await graph_service.connect(
            relationship_type=R.COMPOSITION, source_id=outer.id, target_id=middle.id
        )
        await graph_service.connect(
            relationship_type=R.COMPOSITION, source_id=middle.id, target_id=inner.id
        )

        assert not await graph_repository.would_close_a_containment_cycle(outer.id, inner.id)

    async def test_an_element_cannot_be_put_inside_itself(
        self,
        graph_repository: Neo4jArchitectureRepository,
        graph_service: ArchitectureService,
    ) -> None:
        alone = await graph_service.create_element(element_type=E.GROUPING, name="Platform")

        assert await graph_repository.would_close_a_containment_cycle(alone.id, alone.id)

    async def test_two_unrelated_elements_close_nothing(
        self,
        graph_repository: Neo4jArchitectureRepository,
        graph_service: ArchitectureService,
    ) -> None:
        first = await graph_service.create_element(element_type=E.GROUPING, name="Platform")
        second = await graph_service.create_element(element_type=E.GROUPING, name="Payments")

        assert not await graph_repository.would_close_a_containment_cycle(first.id, second.id)


class TestRelationsOfOneElement:
    async def test_the_links_of_an_element_come_with_both_endpoints(
        self, graph_service: ArchitectureService
    ) -> None:
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        view = await graph_service.relations_of(api.id)

        assert {element.name for element in view.elements} == {"Invoice API", "Order to cash"}
        assert [link.relationship_type for link in view.relationships] == [R.SERVING]

    async def test_a_link_pointing_at_the_element_is_listed_too(
        self, graph_service: ArchitectureService
    ) -> None:
        """The panel shows what an element serves *and* what serves it."""
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        view = await graph_service.relations_of(process.id)

        assert [link.source_id for link in view.relationships] == [api.id]

    async def test_only_the_direct_links_are_returned(
        self, graph_service: ArchitectureService
    ) -> None:
        """Unlike a depth-1 neighbourhood, an edge between two neighbours is not one
        of this element's relations and must not appear in its list."""
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        first = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        second = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Dunning")
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=first.id
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=second.id
        )
        await graph_service.connect(
            relationship_type=R.TRIGGERING, source_id=first.id, target_id=second.id
        )

        view = await graph_service.relations_of(api.id)

        assert [link.relationship_type for link in view.relationships] == [R.SERVING, R.SERVING]

    async def test_an_element_with_no_link_returns_itself_alone(
        self, graph_service: ArchitectureService
    ) -> None:
        """An empty record would be indistinguishable from a missing element."""
        element = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing"
        )

        view = await graph_service.relations_of(element.id)

        assert [found.name for found in view.elements] == ["Billing"]
        assert view.relationships == ()

    async def test_an_element_associated_to_itself_is_listed_once(
        self, graph_service: ArchitectureService
    ) -> None:
        """ArchiMate permits a self-association, and both ends of it are the
        same node — which must not appear twice in the view."""
        element = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing"
        )
        await graph_service.connect(
            relationship_type=R.ASSOCIATION, source_id=element.id, target_id=element.id
        )

        view = await graph_service.relations_of(element.id)

        assert [found.id for found in view.elements] == [element.id]
        assert len(view.relationships) == 1


class TestTraversals:
    async def test_the_neighbourhood_stops_at_the_requested_depth(
        self, graph_service: ArchitectureService
    ) -> None:
        node = await graph_service.create_element(element_type=E.NODE, name="db-01")
        artifact = await graph_service.create_element(element_type=E.ARTIFACT, name="billing.jar")
        component = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing"
        )
        await graph_service.connect(
            relationship_type=R.ASSIGNMENT, source_id=node.id, target_id=artifact.id
        )
        await graph_service.connect(
            relationship_type=R.REALIZATION,
            source_id=artifact.id,
            target_id=component.id,
        )

        one_hop = await graph_service.neighbourhood(node.id, depth=1)
        two_hops = await graph_service.neighbourhood(node.id, depth=2)

        assert {element.name for element in one_hop.elements} == {"db-01", "billing.jar"}
        assert {element.name for element in two_hops.elements} == {
            "db-01",
            "billing.jar",
            "Billing",
        }

    async def test_the_neighbourhood_returns_the_edges_between_what_it_found(
        self, graph_service: ArchitectureService
    ) -> None:
        """A view with nodes and no edges cannot be drawn."""
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        view = await graph_service.neighbourhood(api.id, depth=1)

        assert len(view.relationships) == 1
        assert view.relationships[0].relationship_type is R.SERVING

    async def test_a_lone_element_is_its_own_neighbourhood(
        self, graph_service: ArchitectureService
    ) -> None:
        lonely = await graph_service.create_element(element_type=E.NODE, name="unused-01")

        view = await graph_service.neighbourhood(lonely.id, depth=3)

        assert [element.id for element in view.elements] == [lonely.id]
        assert view.relationships == ()

    async def test_the_neighbourhood_can_be_narrowed_to_one_relationship_type(
        self, graph_service: ArchitectureService
    ) -> None:
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        goal = await graph_service.create_element(element_type=E.GOAL, name="Get paid faster")
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )
        await graph_service.connect(
            relationship_type=R.REALIZATION, source_id=api.id, target_id=goal.id
        )

        view = await graph_service.neighbourhood(api.id, depth=1, relationship_types=(R.SERVING,))

        assert {element.name for element in view.elements} == {
            "Invoice API",
            "Order to cash",
        }


class TestImpactAnalysis:
    async def test_impact_follows_serving_forwards(
        self, graph_service: ArchitectureService
    ) -> None:
        """The service serves the process, so losing the service breaks it."""
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        impacted = await graph_service.impact_of(api.id)

        assert {element.name for element in impacted.elements} == {
            "Invoice API",
            "Order to cash",
        }

    async def test_impact_does_not_run_backwards_along_serving(
        self, graph_service: ArchitectureService
    ) -> None:
        """Losing the business process does not break the service it consumed."""
        api = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        impacted = await graph_service.impact_of(process.id)

        assert {element.name for element in impacted.elements} == {"Order to cash"}

    async def test_impact_runs_against_the_arrow_for_composition(
        self, graph_service: ArchitectureService
    ) -> None:
        """Composition points whole -> part, but it is the whole that suffers."""
        whole = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing"
        )
        part = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Invoice generator"
        )
        await graph_service.connect(
            relationship_type=R.COMPOSITION, source_id=whole.id, target_id=part.id
        )

        impacted = await graph_service.impact_of(part.id)

        assert {element.name for element in impacted.elements} == {
            "Invoice generator",
            "Billing",
        }

    async def test_impact_crosses_layers_in_one_traversal(
        self, graph_service: ArchitectureService
    ) -> None:
        """The question a graph exists to answer: a disk dies, who notices?

        node -> artifact -> component -> service -> process, five hops through
        three layers, mixing relationship types that point different ways.
        """
        node = await graph_service.create_element(element_type=E.NODE, name="db-01")
        artifact = await graph_service.create_element(element_type=E.ARTIFACT, name="billing.jar")
        component = await graph_service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing"
        )
        service = await graph_service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Invoice API"
        )
        process = await graph_service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )
        await graph_service.connect(
            relationship_type=R.ASSIGNMENT, source_id=node.id, target_id=artifact.id
        )
        await graph_service.connect(
            relationship_type=R.REALIZATION,
            source_id=artifact.id,
            target_id=component.id,
        )
        await graph_service.connect(
            relationship_type=R.REALIZATION,
            source_id=component.id,
            target_id=service.id,
        )
        await graph_service.connect(
            relationship_type=R.SERVING, source_id=service.id, target_id=process.id
        )

        impacted = await graph_service.impact_of(node.id, depth=5)

        assert {element.name for element in impacted.elements} == {
            "db-01",
            "billing.jar",
            "Billing",
            "Invoice API",
            "Order to cash",
        }


async def test_a_missing_element_is_absent_rather_than_empty(
    graph_repository: Neo4jArchitectureRepository,
) -> None:
    assert await graph_repository.get_element(uuid4()) is None
