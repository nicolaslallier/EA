"""The one seam with packages and saved queries: what a scope means to the catalogue."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI

from ea.domain.archimate import ElementType as E
from ea.domain.ports import ElementFilter
from ea.domain.quality import MAX_QUALITY_ELEMENTS
from ea.domain.query import ElementQuery
from ea.main import build_scope_resolver
from tests.support_analysis import an_element


class FakeCatalogue:
    def __init__(self, found: tuple[Any, ...] = ()) -> None:
        self.asked: list[ElementFilter] = []
        self._found = found

    async def list_elements(self, criteria: ElementFilter) -> tuple[Any, ...]:
        self.asked.append(criteria)
        return self._found


def an_app(catalogue: FakeCatalogue, saved: object | None = None) -> FastAPI:
    app = FastAPI()
    app.state.architecture_service = catalogue
    if saved is not None:
        app.state.saved_query_service = saved
    return app


@pytest.mark.asyncio
async def test_a_package_scope_is_the_package_and_its_descendants() -> None:
    inside = an_element(E.NODE, "inside")
    catalogue = FakeCatalogue((inside,))
    package = uuid4()

    ids = await build_scope_resolver(an_app(catalogue))(package, None)

    assert ids == (inside.id,)
    (criteria,) = catalogue.asked
    assert (criteria.package_id, criteria.include_subpackages) == (package, True)


@pytest.mark.asyncio
async def test_a_saved_query_scope_is_its_filter_with_the_run_ceiling_as_page() -> None:
    inside = an_element(E.NODE, "inside")
    catalogue = FakeCatalogue((inside,))
    query = ElementQuery(text="srv", limit=5, offset=40)

    async def get(query_id: Any) -> Any:
        return SimpleNamespace(query=query)

    ids = await build_scope_resolver(an_app(catalogue, SimpleNamespace(get=get)))(None, uuid4())

    assert ids == (inside.id,)
    (criteria,) = catalogue.asked
    assert criteria.search == "srv"
    # One element past the ceiling, from the start: the page the author chose is not the scope.
    assert (criteria.limit, criteria.offset) == (MAX_QUALITY_ELEMENTS + 1, 0)
