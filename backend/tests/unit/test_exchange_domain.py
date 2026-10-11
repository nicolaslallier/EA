"""The vocabulary of an exchange: names, identifiers, and what an element becomes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.exchange import (
    ACCESS_BY_NAME,
    ACCESS_NAMES,
    DESCRIPTION_KEY,
    JUNCTION_NAMES,
    SOURCE_ID,
    ExportScope,
    element_type_of,
    exchange_element_of,
    exchange_name,
    exchange_relationship_of,
    identifier_of,
    pascal_case,
    relationship_type_of,
    xml_identifier,
)
from ea.domain.model import Element, Relationship, is_property_key

NOW = datetime(2026, 10, 10, tzinfo=UTC)
MODELLED = [t for t in ElementType if not t.is_connector]


def test_names_are_the_pascal_case_of_the_value() -> None:
    assert exchange_name(ElementType.BUSINESS_ACTOR) == "BusinessActor"
    assert exchange_name(ElementType.COURSE_OF_ACTION) == "CourseOfAction"
    assert exchange_name(RelationshipType.SPECIALIZATION) == "Specialization"
    assert pascal_case("read_write") == "ReadWrite"


@pytest.mark.parametrize("element_type", MODELLED, ids=lambda t: t.value)
def test_every_modelled_element_type_survives_the_round_trip(element_type: ElementType) -> None:
    assert element_type_of(exchange_name(element_type)) is element_type


@pytest.mark.parametrize("relationship", list(RelationshipType), ids=lambda t: t.value)
def test_every_relationship_type_survives_the_round_trip(relationship: RelationshipType) -> None:
    assert relationship_type_of(exchange_name(relationship)) is relationship


def test_two_types_never_share_an_exchange_name() -> None:
    names = [exchange_name(t) for t in MODELLED]

    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r"[A-Z][A-Za-z]+", name) for name in names)


def test_the_only_connector_is_the_junction_and_it_is_not_a_modelled_type() -> None:
    connectors = [t for t in ElementType if t.is_connector]

    assert connectors == [ElementType.JUNCTION]
    assert element_type_of("AndJunction") is None
    assert frozenset({"AndJunction", "OrJunction"}) == JUNCTION_NAMES


def test_unknown_names_are_none() -> None:
    assert element_type_of("Spaceship") is None
    assert relationship_type_of("Telepathy") is None


def test_access_names_cover_every_access_type_both_ways() -> None:
    assert ACCESS_NAMES[AccessType.READ_WRITE] == "ReadWrite"
    assert {ACCESS_BY_NAME[name] for name in ACCESS_NAMES.values()} == set(AccessType)


def test_a_valid_identifier_is_kept_and_an_invalid_one_is_normalised() -> None:
    assert xml_identifier("id-123") == "id-123"
    assert xml_identifier("1") == "id-1"
    assert xml_identifier("a b/c") == "id-a_b_c"
    assert xml_identifier(xml_identifier("1")) == "id-1"  # idempotent


def test_an_element_without_a_source_id_is_named_after_its_uuid() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")

    assert identifier_of(None, uid) == f"id-{uid}"
    assert identifier_of("src-9", uid) == "src-9"


def test_the_exchange_form_of_an_element_drops_source_id_and_keeps_the_rest() -> None:
    element = Element.create(
        element_type=ElementType.APPLICATION_COMPONENT,
        name="Billing",
        description="Invoices",
        documentation="Long text",
        properties={SOURCE_ID: "app-1", "owner": "finance"},
        now=NOW,
    )

    item = exchange_element_of(element)

    assert item.identifier == "app-1"
    assert dict(item.properties) == {"owner": "finance"}
    assert (item.description, item.documentation) == ("Invoices", "Long text")
    assert DESCRIPTION_KEY == "ea.description"


def test_the_exchange_form_of_a_relationship_uses_the_ends_identifiers() -> None:
    a = Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW)
    b = Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="B", now=NOW)
    link = Relationship.between(
        RelationshipType.FLOW, a, b, now=NOW, name="sends", properties={SOURCE_ID: "r-1"}
    )

    item = exchange_relationship_of(link, source="a-1", target="b-1")

    assert (item.identifier, item.source, item.target) == ("r-1", "a-1", "b-1")
    assert dict(item.properties) == {}
    assert item.relationship_type is RelationshipType.FLOW


def test_the_scope_label_names_the_kind() -> None:
    assert ExportScope().label == "model"


def test_a_property_key_is_a_plain_identifier() -> None:
    assert is_property_key("owner_name")
    assert not is_property_key("ea.description")
    assert not is_property_key("Owner name")
    assert not is_property_key("")
