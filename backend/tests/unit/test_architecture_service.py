"""Use-case rules, checked against an in-memory graph.

The double below implements `ArchitectureRepository` structurally, so these stay
unit tests: no container, no Bolt, milliseconds. The Cypher that backs the real
implementation is covered in `tests/integration`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    CyclicContainmentError,
    ElementNotFoundError,
    IllegalRelationshipError,
)
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository


class TestElements:
    @pytest.mark.asyncio
    async def test_creating_an_element_stores_it(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

        assert repository.elements[element.id].name == "Billing"

    @pytest.mark.asyncio
    async def test_fetching_an_unknown_element_says_which_one(
        self, service: ArchitectureService
    ) -> None:
        missing = uuid4()

        with pytest.raises(ElementNotFoundError, match=str(missing)):
            await service.get_element(missing)

    @pytest.mark.asyncio
    async def test_an_update_leaves_the_fields_it_was_not_given(
        self, service: ArchitectureService
    ) -> None:
        element = await service.create_element(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            description="Invoices customers",
            properties={"owner": "finance"},
        )

        updated = await service.update_element(element.id, name="Invoicing")

        assert updated.name == "Invoicing"
        assert updated.description == "Invoices customers"
        assert updated.properties["owner"] == "finance"

    @pytest.mark.asyncio
    async def test_deleting_an_unknown_element_is_an_error_not_a_silent_success(
        self, service: ArchitectureService
    ) -> None:
        with pytest.raises(ElementNotFoundError):
            await service.delete_element(uuid4())


class TestConnecting:
    @pytest.mark.asyncio
    async def test_two_elements_are_linked(self, service: ArchitectureService) -> None:
        api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
        process = await service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order to cash"
        )

        link = await service.connect(
            relationship_type=R.SERVING, source_id=api.id, target_id=process.id
        )

        assert (link.source_id, link.target_id) == (api.id, process.id)

    @pytest.mark.asyncio
    async def test_linking_to_a_missing_element_fails_before_anything_is_written(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")

        with pytest.raises(ElementNotFoundError):
            await service.connect(relationship_type=R.SERVING, source_id=api.id, target_id=uuid4())

        assert repository.relationships == {}

    @pytest.mark.asyncio
    async def test_a_link_the_metamodel_forbids_is_refused(
        self, service: ArchitectureService
    ) -> None:
        data = await service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
        function = await service.create_element(
            element_type=E.APPLICATION_FUNCTION, name="Invoicing"
        )

        with pytest.raises(IllegalRelationshipError):
            await service.connect(
                relationship_type=R.ACCESS, source_id=data.id, target_id=function.id
            )

    @pytest.mark.asyncio
    async def test_containment_cannot_be_made_cyclic(self, service: ArchitectureService) -> None:
        """A composes B, B composes C — C must not be allowed to compose A."""
        a = await service.create_element(element_type=E.GROUPING, name="Platform")
        b = await service.create_element(element_type=E.GROUPING, name="Payments")
        c = await service.create_element(element_type=E.GROUPING, name="Ledger")
        await service.connect(relationship_type=R.COMPOSITION, source_id=a.id, target_id=b.id)
        await service.connect(relationship_type=R.COMPOSITION, source_id=b.id, target_id=c.id)

        with pytest.raises(CyclicContainmentError, match="cyclic"):
            await service.connect(relationship_type=R.COMPOSITION, source_id=c.id, target_id=a.id)

    @pytest.mark.asyncio
    async def test_a_serving_loop_is_allowed_because_it_is_not_containment(
        self, service: ArchitectureService
    ) -> None:
        """Two services calling each other is a design smell, not a broken model."""
        first = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
        second = await service.create_element(
            element_type=E.APPLICATION_SERVICE, name="Payment API"
        )
        await service.connect(relationship_type=R.SERVING, source_id=first.id, target_id=second.id)

        link = await service.connect(
            relationship_type=R.SERVING, source_id=second.id, target_id=first.id
        )

        assert link.source_id == second.id


class TestAnalysis:
    @pytest.mark.asyncio
    async def test_analysing_an_unknown_element_fails_rather_than_returning_nothing(
        self, service: ArchitectureService
    ) -> None:
        """An empty graph and a wrong id must not look the same to a caller."""
        with pytest.raises(ElementNotFoundError):
            await service.impact_of(uuid4())

    @pytest.mark.asyncio
    async def test_listing_the_relations_of_an_unknown_element_fails(
        self, service: ArchitectureService
    ) -> None:
        with pytest.raises(ElementNotFoundError):
            await service.relations_of(uuid4())

    @pytest.mark.asyncio
    async def test_relations_of_an_element_carry_both_endpoints(
        self, service: ArchitectureService
    ) -> None:
        """A link is unreadable without its ends: the view carries them along."""
        api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
        process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")
        await service.connect(relationship_type=R.SERVING, source_id=api.id, target_id=process.id)

        view = await service.relations_of(api.id)

        assert {element.name for element in view.elements} == {"Invoice API", "Order"}
        assert [link.relationship_type for link in view.relationships] == [R.SERVING]
