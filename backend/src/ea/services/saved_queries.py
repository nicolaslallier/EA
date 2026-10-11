"""Use cases over the saved queries — named, shared filters of the catalogue.

Shared on purpose: any reader may run one, any editor may change one, and the
author is only recorded — from the caller, never from a body. A run is the
only use case that needs another store: the package a query names is looked up
*then*, so a package deleted since leaves the query intact and says which one
went stale (`UnknownPackageError`) instead of silently answering nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.audit import AuditAction, saved_query_view
from ea.domain.errors import SavedQueryNotFoundError, UnknownPackageError
from ea.domain.ports import ElementFilter
from ea.domain.query import ElementQuery, QueryResult
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.services.audit import AuditTrail
from ea.services.caller import logged, require_caller, require_editor

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from ea.domain.audit import AuditEvent
    from ea.domain.ports import PackageRepository, SavedQueryRepository
    from ea.services.architecture import ArchitectureService

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _not_found(query_id: UUID) -> SavedQueryNotFoundError:
    return SavedQueryNotFoundError(f"no saved query with id {query_id}")


class SavedQueryService:
    def __init__(
        self,
        repository: SavedQueryRepository,
        packages: PackageRepository | None,
        architecture: ArchitectureService,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._packages = packages
        self._architecture = architecture
        self._now = clock
        self._trail = AuditTrail(clock)

    async def list_queries(self) -> tuple[SavedQuerySummary, ...]:
        require_caller()
        return await self._repository.list_all()

    async def get(self, query_id: UUID) -> SavedQuery:
        require_caller()
        saved = await self._repository.get(query_id)
        if saved is None:
            raise _not_found(query_id)
        return saved

    async def create(self, *, name: str, query: ElementQuery, description: str = "") -> SavedQuery:
        caller = require_editor()
        saved = SavedQuery.create(
            name=name,
            query=query,
            created_by=caller.username,
            description=description,
            now=self._now(),
        )

        def created(stored: SavedQuery) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.SAVED_QUERY_CREATED,
                    entity_id=stored.id,
                    after=saved_query_view(stored),
                ),
            )

        stored = await self._repository.add(saved, audit=created)
        self._audit("query saved: %r", stored, "query_saved")
        return stored

    async def update(
        self,
        query_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        query: ElementQuery | None = None,
    ) -> SavedQuery:
        """Rename, describe or replace the filter. The author is never rewritten."""
        require_editor()
        current = await self.get(query_id)
        revised = current.revise(name=name, description=description, query=query, now=self._now())

        def events(before: Mapping[str, Any]) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.SAVED_QUERY_UPDATED,
                    entity_id=query_id,
                    before=before,
                    after=saved_query_view(revised),
                ),
            )

        stored = await self._repository.save(revised, audit=events)
        self._audit("query updated: %r", stored, "query_updated")
        return stored

    async def delete(self, query_id: UUID) -> None:
        """Delete the query — not the elements it finds."""
        require_editor()

        def events(before: Mapping[str, Any]) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.SAVED_QUERY_DELETED, entity_id=query_id, before=before
                ),
            )

        if not await self._repository.delete(query_id, audit=events):
            raise _not_found(query_id)
        logger.info(
            "saved query deleted",
            extra={"action": "query_deleted", "query_id": str(query_id)},
        )

    async def run(self, query_id: UUID, *, limit: int = 50, offset: int = 0) -> QueryResult:
        """Run a saved query, one page at a time (the page is the caller's, not the stored one)."""
        require_caller()
        saved = await self.get(query_id)
        wanted = saved.query.package_id
        if wanted is not None and (
            self._packages is None or await self._packages.get(wanted) is None
        ):
            msg = f"the query {saved.name!r} names the package {wanted}, which no longer exists"
            raise UnknownPackageError(msg)
        criteria = ElementFilter.from_query(replace(saved.query, limit=limit, offset=offset))
        elements = await self._architecture.list_elements(criteria)
        return QueryResult(
            elements=elements, total=await self._architecture.count_elements(criteria)
        )

    @staticmethod
    def _audit(message: str, saved: SavedQuery, action: str) -> None:
        logger.info(
            message, logged(saved.name), extra={"action": action, "query_id": str(saved.id)}
        )
