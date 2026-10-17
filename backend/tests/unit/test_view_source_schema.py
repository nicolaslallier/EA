"""`ViewSource`: a generated view names exactly one source, and reads back as the domain's."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ea.api.schemas import ViewSource
from ea.domain.generated_views import PackageSource, QuerySource


@pytest.mark.parametrize(
    "fields",
    [
        {"query_id": None, "package_id": None, "recursive": False},
        {"query_id": uuid4(), "package_id": uuid4(), "recursive": False},
        {"query_id": uuid4(), "package_id": None, "recursive": True},
    ],
    ids=["neither", "both", "recursive query"],
)
def test_anything_but_exactly_one_source_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ViewSource.model_validate(fields)


@pytest.mark.parametrize(
    "source",
    [QuerySource(uuid4()), PackageSource(uuid4(), recursive=True)],
    ids=["query", "package"],
)
def test_a_source_reads_back_as_it_was_given(source: QuerySource | PackageSource) -> None:
    assert ViewSource.of(source).placed() == source


def test_a_source_built_past_validation_with_no_side_still_refuses_to_place() -> None:
    hollow = ViewSource.model_construct(query_id=None, package_id=None, recursive=False)

    with pytest.raises(ValueError, match="saved query or a package"):
        hollow.placed()
