"""The adapter between generated views and the saved queries and packages they read.

A generated view reads its members from the filter that already exists
(`ElementQuery`, docs/adr/0043) - never from a copy of it. A saved query is run with
its stored filter with the view's own ceiling in place of the stored page size; a package is a
filter of its own (`ElementFilter` on `package_id`). Both ask the database for at most
`limit` rows and for a `COUNT`, never for the whole answer: a source of a million
elements costs one `LIMIT` and one count, and is refused by the caller.

This is the one module that knows how to ask: change it, and only it, if those names move.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ea.domain.generated_views import QuerySource, SourceMembers, ViewSource
from ea.domain.ports import ElementFilter
from ea.domain.query import QueryOrder

if TYPE_CHECKING:
    from ea.services.architecture import ArchitectureService
    from ea.services.packages import PackageService
    from ea.services.saved_queries import SavedQueryService


class QueriesAndPackages:
    """`ViewSourceResolver` over the saved queries and the package tree."""

    def __init__(
        self,
        architecture: ArchitectureService,
        saved_queries: SavedQueryService,
        packages: PackageService,
    ) -> None:
        self._architecture = architecture
        self._saved_queries = saved_queries
        self._packages = packages

    async def members(self, source: ViewSource, *, limit: int) -> SourceMembers:
        if isinstance(source, QuerySource):
            saved = await self._saved_queries.get(source.query_id)
            if saved.query.package_id is not None:
                await self._packages.get(saved.query.package_id)  # a vanished package: not found
            # The stored page size is the stored query's, not the view's: only the ceiling counts.
            criteria = replace(ElementFilter.from_query(saved.query), limit=limit, offset=0)
        else:
            await self._packages.get(source.package_id)  # a missing package is a not-found
            criteria = ElementFilter(
                package_id=source.package_id,
                include_subpackages=source.recursive,
                order=QueryOrder.NAME,
                limit=limit,
            )
        elements = await self._architecture.list_elements(criteria)
        return SourceMembers(
            tuple(e.id for e in elements), total=await self._architecture.count_elements(criteria)
        )
