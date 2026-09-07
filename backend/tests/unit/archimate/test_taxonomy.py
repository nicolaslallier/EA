"""The ArchiMate 3.2 taxonomy is data, so it is checked as data.

These tests guard the two properties everything else relies on: every element
type is classified exactly once, and the classification matches the layer
structure of the specification.
"""

from collections import Counter

import pytest

from ea.domain.archimate import Aspect, ElementType, Layer, RelationshipType


def test_every_element_type_is_classified() -> None:
    """A type without metadata would silently escape every rule below."""
    for element_type in ElementType:
        assert element_type.layer in Layer
        assert element_type.aspect in Aspect


def test_element_type_values_are_stable_snake_case_identifiers() -> None:
    """The value is persisted as a Neo4j label suffix, so it is part of the schema."""
    for element_type in ElementType:
        assert element_type.value.islower()
        assert element_type.value.replace("_", "").isalnum()


def test_no_duplicate_element_type_values() -> None:
    counts = Counter(element_type.value for element_type in ElementType)
    assert [value for value, count in counts.items() if count > 1] == []


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        (Layer.MOTIVATION, 10),
        (Layer.STRATEGY, 4),
        (Layer.BUSINESS, 13),
        (Layer.APPLICATION, 9),
        (Layer.TECHNOLOGY, 13),
        (Layer.PHYSICAL, 4),
        (Layer.IMPLEMENTATION_MIGRATION, 5),
        (Layer.OTHER, 3),
    ],
)
def test_layer_holds_the_number_of_elements_the_specification_defines(
    layer: Layer, expected: int
) -> None:
    assert len(ElementType.in_layer(layer)) == expected


def test_the_catalogue_is_complete() -> None:
    """ArchiMate 3.2 defines 61 element types (Location, Grouping and Junction included)."""
    assert len(ElementType) == 61


def test_archimate_defines_eleven_relationship_types() -> None:
    assert len(RelationshipType) == 11


def test_junction_is_a_connector_not_a_modelled_concept() -> None:
    assert ElementType.JUNCTION.is_connector
    assert not ElementType.APPLICATION_COMPONENT.is_connector


def test_element_types_can_be_looked_up_by_value() -> None:
    assert ElementType("application_component") is ElementType.APPLICATION_COMPONENT
