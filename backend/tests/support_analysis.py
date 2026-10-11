"""Small builders shared by the analysis suites — defaults plus overrides."""

from __future__ import annotations

from uuid import uuid4

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.model import Element, Relationship
from tests.conftest import FIXED_NOW


def an_element(element_type: ElementType, name: str, **overrides: object) -> Element:
    return Element.create(
        element_type=element_type,
        name=name,
        now=FIXED_NOW,
        **overrides,  # type: ignore[arg-type]
    )


def a_link(relationship_type: RelationshipType, source: Element, target: Element) -> Relationship:
    """A legal link, built through the metamodel like the service does."""
    return Relationship.between(relationship_type, source, target, now=FIXED_NOW)


def a_stored_link(
    relationship_type: RelationshipType, source: Element, target: Element
) -> Relationship:
    """A link that skips the metamodel: what an import, or an older metamodel, left behind."""
    return Relationship(
        id=uuid4(),
        relationship_type=relationship_type,
        source_id=source.id,
        target_id=target.id,
        source_type=source.element_type,
        target_type=target.element_type,
        created_at=FIXED_NOW,
    )
