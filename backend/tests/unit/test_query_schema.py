"""The strict schema of a query: the one door every adapter and every stored query uses."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ea.api.schemas import ElementQuerySchema
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
    to_json,
)


def a_query() -> ElementQuery:
    return ElementQuery(
        element_types=(E.NODE,),
        text="db",
        package_id=uuid4(),
        include_subpackages=False,
        properties=(
            PropertyTest("owner", PropertyOp.EQ, "x"),
            PropertyTest("cidr", PropertyOp.EXISTS),
        ),
        relations=(RelationTest(R.SERVING, Direction.IN, False, E.BUSINESS_PROCESS),),
        order=QueryOrder.CREATED_AT,
        limit=10,
        offset=20,
    )


def payload(**changes: Any) -> dict[str, Any]:
    return {**to_json(ElementQuery()), **changes}


class TestTheRoundTrip:
    def test_domain_schema_domain(self) -> None:
        query = a_query()

        assert ElementQuerySchema.of(query).to_domain() == query

    def test_the_schema_dumps_exactly_the_stored_form(self) -> None:
        query = a_query()

        assert ElementQuerySchema.of(query).model_dump(mode="json") == to_json(query)

    def test_the_stored_form_is_accepted_back_by_the_schema(self) -> None:
        query = a_query()

        assert ElementQuerySchema.model_validate(to_json(query)).to_domain() == query


class TestWhatItRefuses:
    def test_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ElementQuerySchema.model_validate(payload(colour="red"))

    def test_a_missing_field_there_are_no_defaults(self) -> None:
        body = payload()
        del body["order"]

        with pytest.raises(ValidationError, match="order"):
            ElementQuerySchema.model_validate(body)

    def test_a_version_that_is_not_1(self) -> None:
        with pytest.raises(ValidationError, match="version"):
            ElementQuerySchema.model_validate(payload(version=2))

    def test_an_operator_outside_the_four(self) -> None:
        with pytest.raises(ValidationError, match="op"):
            ElementQuerySchema.model_validate(
                payload(properties=[{"key": "a", "op": "gt", "value": "1"}])
            )

    def test_a_property_name_that_is_not_an_identifier(self) -> None:
        with pytest.raises(ValidationError, match="key"):
            ElementQuerySchema.model_validate(
                payload(properties=[{"key": "a-b", "op": "exists", "value": None}])
            )

    def test_more_than_ten_property_tests_or_five_relation_tests(self) -> None:
        many = [{"key": f"k{i}", "op": "exists", "value": None} for i in range(11)]
        with pytest.raises(ValidationError, match="properties"):
            ElementQuerySchema.model_validate(payload(properties=many))
        relations = [
            {
                "relationship_type": "serving",
                "direction": "any",
                "present": True,
                "other_type": None,
            }
        ] * 6
        with pytest.raises(ValidationError, match="relations"):
            ElementQuerySchema.model_validate(payload(relations=relations))

    def test_a_type_the_metamodel_does_not_have(self) -> None:
        with pytest.raises(ValidationError, match="element_types"):
            ElementQuerySchema.model_validate(payload(element_types=["flux_capacitor"]))

    def test_a_contradiction_passes_the_schema_and_fails_in_the_domain(self) -> None:
        schema = ElementQuerySchema.model_validate(payload(root_only=True, package_id=str(uuid4())))

        with pytest.raises(ValueError, match="root_only"):
            schema.to_domain()
