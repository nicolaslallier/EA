"""The entities that carry an architecture: elements and the links between them."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ea.domain.archimate import AccessType
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import IllegalRelationshipError
from ea.domain.model import Element, Relationship

FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def an_element(element_type: E = E.APPLICATION_COMPONENT, name: str = "Billing") -> Element:
    return Element.create(element_type=element_type, name=name, now=FIXED_NOW)


class TestElement:
    def test_a_new_element_gets_an_identity_and_a_timestamp(self) -> None:
        element = an_element()

        assert isinstance(element.id, UUID)
        assert element.created_at == FIXED_NOW
        assert element.updated_at == FIXED_NOW

    def test_the_name_is_trimmed(self) -> None:
        assert an_element(name="  Billing  ").name == "Billing"

    def test_an_element_needs_a_name(self) -> None:
        with pytest.raises(ValueError, match="name"):
            an_element(name="   ")

    def test_a_junction_is_not_a_modelled_element(self) -> None:
        """Junctions exist to fan relationships in and out, not to be catalogued."""
        with pytest.raises(ValueError, match="junction"):
            an_element(element_type=E.JUNCTION)

    def test_renaming_moves_the_updated_timestamp_only(self) -> None:
        element = an_element()
        later = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

        renamed = element.rename("Invoicing", now=later)

        assert renamed.name == "Invoicing"
        assert renamed.id == element.id
        assert renamed.created_at == FIXED_NOW
        assert renamed.updated_at == later

    def test_an_element_is_immutable(self) -> None:
        """Mutating in place would let a service change a repository's copy."""
        with pytest.raises(AttributeError):
            an_element().name = "Invoicing"  # type: ignore[misc]

    def test_properties_carry_the_free_form_attributes(self) -> None:
        element = Element.create(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            properties={"owner": "finance", "criticality": "high"},
            now=FIXED_NOW,
        )

        assert element.properties["owner"] == "finance"


class TestRelationship:
    def test_a_legal_relationship_is_built_between_two_elements(self) -> None:
        service = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        link = Relationship.between(R.SERVING, service, process, now=FIXED_NOW)

        assert link.source_id == service.id
        assert link.target_id == process.id
        assert link.source_type is E.APPLICATION_SERVICE

    def test_an_illegal_relationship_is_refused_at_construction(self) -> None:
        data = an_element(E.DATA_OBJECT, "Invoice")
        function = an_element(E.APPLICATION_FUNCTION, "Invoicing")

        with pytest.raises(IllegalRelationshipError):
            Relationship.between(R.ACCESS, data, function, now=FIXED_NOW)

    def test_an_element_cannot_compose_itself(self) -> None:
        component = an_element()

        with pytest.raises(IllegalRelationshipError, match="itself"):
            Relationship.between(R.COMPOSITION, component, component, now=FIXED_NOW)

    def test_access_defaults_to_plain_access(self) -> None:
        function = an_element(E.APPLICATION_FUNCTION, "Invoicing")
        data = an_element(E.DATA_OBJECT, "Invoice")

        link = Relationship.between(R.ACCESS, function, data, now=FIXED_NOW)

        assert link.access_type is AccessType.ACCESS

    def test_access_type_is_only_meaningful_on_an_access_relationship(self) -> None:
        service = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        with pytest.raises(ValueError, match="access_type"):
            Relationship.between(
                R.SERVING, service, process, access_type=AccessType.WRITE, now=FIXED_NOW
            )

    def test_a_relationship_carries_its_endpoint_types_for_query_free_validation(
        self,
    ) -> None:
        """Storing the endpoint types lets a rule be re-checked without a round trip."""
        service = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        link = Relationship.between(R.SERVING, service, process, now=FIXED_NOW)

        assert (link.source_type, link.target_type) == (E.APPLICATION_SERVICE, E.BUSINESS_PROCESS)

    def test_relationship_ids_are_unique(self) -> None:
        service = an_element(E.APPLICATION_SERVICE, "Invoice API")
        process = an_element(E.BUSINESS_PROCESS, "Order to cash")

        first = Relationship.between(R.SERVING, service, process, now=FIXED_NOW)
        second = Relationship.between(R.SERVING, service, process, now=FIXED_NOW)

        assert first.id != second.id
        assert first.id != uuid4()


class TestPropertyNames:
    def test_a_property_name_must_be_a_plain_identifier(self) -> None:
        """Names become graph properties, so `owner name` or `owner-name` cannot pass."""
        with pytest.raises(ValueError, match="plain identifier"):
            Element.create(
                element_type=E.APPLICATION_COMPONENT,
                name="Billing",
                properties={"owner name": "finance"},
                now=FIXED_NOW,
            )

    def test_underscores_and_digits_are_fine(self) -> None:
        element = Element.create(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            properties={"owner_team_2": "finance"},
            now=FIXED_NOW,
        )

        assert element.properties["owner_team_2"] == "finance"
