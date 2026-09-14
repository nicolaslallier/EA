"""Reading a Neo4j record into the domain, without Neo4j — docs/adr/0033."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.graph_import import element_from_neo4j, relationship_from_neo4j

WHEN = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)


class Neo4jDateTime:
    """The driver's own temporal type: it only becomes a `datetime` when asked."""

    def __init__(self, value: datetime) -> None:
        self._value = value

    def to_native(self) -> datetime:
        return self._value


def test_an_element_loses_the_property_prefix_and_keeps_its_id() -> None:
    element_id = uuid4()

    element = element_from_neo4j(
        {
            "id": str(element_id),
            "element_type": "node",
            "layer": "technology",
            "aspect": "active_structure",
            "name": "db-01",
            "description": "Base",
            "documentation": "",
            "created_at": Neo4jDateTime(WHEN),
            "updated_at": Neo4jDateTime(WHEN),
            "p_ip_address": "10.0.1.12",
            "p_vrf": "default",
            "p_rack": 12,
        }
    )

    assert element.id == element_id
    assert element.element_type is ElementType.NODE
    assert element.created_at == WHEN
    assert dict(element.properties) == {"ip_address": "10.0.1.12", "vrf": "default", "rack": "12"}


def test_a_missing_text_field_is_empty_rather_than_absent() -> None:
    element = element_from_neo4j(
        {
            "id": str(uuid4()),
            "element_type": "capability",
            "name": "Facturer",
            "created_at": WHEN,
            "updated_at": WHEN,
        }
    )

    assert element.description == ""
    assert element.documentation == ""
    assert dict(element.properties) == {}


def test_a_relationship_keeps_its_ends_types_and_access() -> None:
    source, target = uuid4(), uuid4()

    relationship = relationship_from_neo4j(
        {
            "id": str(uuid4()),
            "relationship_type": "access",
            "source_id": str(source),
            "target_id": str(target),
            "source_type": "application_component",
            "target_type": "data_object",
            "created_at": Neo4jDateTime(WHEN),
            "name": "lit",
            "access_type": "read",
            "directed": True,
            "p_since": "2024",
        }
    )

    assert relationship.relationship_type is RelationshipType.ACCESS
    assert (relationship.source_id, relationship.target_id) == (source, target)
    assert relationship.source_type is ElementType.APPLICATION_COMPONENT
    assert relationship.access_type is AccessType("read")
    assert relationship.directed is True
    assert dict(relationship.properties) == {"since": "2024"}
