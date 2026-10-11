"""A junction is an element carrying `junction_type` (docs/adr/0062)."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType
from ea.domain.errors import InvalidJunctionError
from ea.domain.junctions import validate_junction_properties


def test_a_junction_without_a_kind_is_an_and() -> None:
    assert validate_junction_properties(ElementType.JUNCTION, None) == {"junction_type": "and"}
    assert validate_junction_properties(ElementType.JUNCTION, {"x": "1"}) == {
        "x": "1",
        "junction_type": "and",
    }


def test_the_kind_is_stored_in_its_one_spelling() -> None:
    out = validate_junction_properties(ElementType.JUNCTION, {"junction_type": " OR "})
    assert out == {"junction_type": "or"}


def test_another_kind_is_refused_without_echoing_it() -> None:
    with pytest.raises(InvalidJunctionError) as raised:
        validate_junction_properties(ElementType.JUNCTION, {"junction_type": "xor-secret"})
    assert "secret" not in str(raised.value)


def test_other_types_pass_untouched() -> None:
    props = {"junction_type": "banana"}
    assert validate_junction_properties(ElementType.NODE, props) is props
    assert validate_junction_properties(ElementType.NODE, None) is None
