"""The Neo4j schema is declared by hand, so its coverage is checked by machine."""

from ea.db.schema import ANY_RELATIONSHIP, SCHEMA_STATEMENTS
from ea.domain.archimate import RelationshipType


def test_every_relationship_type_has_an_id_index() -> None:
    """A missing index turns a relationship lookup into a full graph scan."""
    for relationship_type in RelationshipType:
        pattern = f"[r:{relationship_type.label}]"
        assert any(pattern in statement for statement in SCHEMA_STATEMENTS), relationship_type


def test_the_traversal_pattern_lists_every_relationship_type() -> None:
    """A type missing here would be invisible to every traversal endpoint."""
    listed = set(ANY_RELATIONSHIP.split("|"))

    assert listed == {relationship_type.label for relationship_type in RelationshipType}


def test_every_statement_is_idempotent() -> None:
    """Boot applies the whole list every time; none of it may fail on a re-run."""
    for statement in SCHEMA_STATEMENTS:
        assert "IF NOT EXISTS" in statement
