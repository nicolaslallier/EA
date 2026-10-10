"""The saved-query use cases: who wrote it, who may change it, and what a run says."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    DuplicateSavedQueryError,
    InvalidQueryError,
    NotAuthorisedError,
    SavedQueryNotFoundError,
    UnknownPackageError,
)
from ea.domain.packages import Package
from ea.domain.query import ElementQuery
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.saved_queries import SavedQueryService
from tests.conftest import FIXED_NOW, InMemoryPackages, InMemorySavedQueries, a_reader, an_editor

pytestmark = pytest.mark.asyncio


class TestWritingAndReading:
    async def test_the_author_is_the_caller_not_a_parameter(
        self, saved_query_service: SavedQueryService
    ) -> None:
        with acting_as(an_editor(username="nicolas")):
            saved = await saved_query_service.create(name="Les noeuds", query=ElementQuery())

        assert saved.created_by == "nicolas"
        assert saved.created_at == FIXED_NOW

    async def test_a_taken_name_is_refused(self, saved_query_service: SavedQueryService) -> None:
        await saved_query_service.create(name="A", query=ElementQuery())

        with pytest.raises(DuplicateSavedQueryError):
            await saved_query_service.create(name="A", query=ElementQuery())

    async def test_any_editor_may_change_what_another_wrote_and_the_author_stays(
        self, saved_query_service: SavedQueryService
    ) -> None:
        with acting_as(an_editor(username="alice")):
            saved = await saved_query_service.create(name="A", query=ElementQuery())

        with acting_as(an_editor(username="bob")):
            updated = await saved_query_service.update(
                saved.id, name="B", query=ElementQuery(text="db")
            )

        assert (updated.name, updated.query.text, updated.created_by) == ("B", "db", "alice")

    async def test_an_omitted_field_is_kept(self, saved_query_service: SavedQueryService) -> None:
        saved = await saved_query_service.create(
            name="A", query=ElementQuery(text="db"), description="d"
        )

        updated = await saved_query_service.update(saved.id, description="neuf")

        assert (updated.name, updated.query.text, updated.description) == ("A", "db", "neuf")

    async def test_the_listing_gives_summaries_sorted_by_name(
        self, saved_query_service: SavedQueryService
    ) -> None:
        await saved_query_service.create(name="beta", query=ElementQuery())
        await saved_query_service.create(name="Alpha", query=ElementQuery())

        assert [s.name for s in await saved_query_service.list_queries()] == ["Alpha", "beta"]

    async def test_a_missing_query_is_a_not_found_to_read_change_or_delete(
        self, saved_query_service: SavedQueryService
    ) -> None:
        for call in (
            saved_query_service.get(uuid4()),
            saved_query_service.update(uuid4(), name="x"),
            saved_query_service.delete(uuid4()),
            saved_query_service.run(uuid4()),
        ):
            with pytest.raises(SavedQueryNotFoundError):
                await call

    async def test_delete_removes_the_query_and_only_the_query(
        self, saved_query_service: SavedQueryService, service: ArchitectureService
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        await saved_query_service.delete(saved.id)

        assert await saved_query_service.list_queries() == ()
        assert (await service.get_element(element.id)).name == "db-01"

    async def test_a_reader_reads_runs_and_lists_but_never_writes(
        self, saved_query_service: SavedQueryService
    ) -> None:
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        with acting_as(a_reader()):
            assert len(await saved_query_service.list_queries()) == 1
            assert (await saved_query_service.get(saved.id)).name == "A"
            await saved_query_service.run(saved.id)
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.create(name="B", query=ElementQuery())
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.update(saved.id, name="C")
            with pytest.raises(NotAuthorisedError):
                await saved_query_service.delete(saved.id)

    async def test_a_nul_in_the_name_is_a_value_error_not_a_driver_failure(
        self, saved_query_service: SavedQueryService
    ) -> None:
        with pytest.raises(ValueError, match="NUL"):
            await saved_query_service.create(name="a\x00b", query=ElementQuery())


class TestRunning:
    async def test_a_run_pages_over_the_stored_filter_and_counts_the_whole(
        self, saved_query_service: SavedQueryService, service: ArchitectureService
    ) -> None:
        for name in ("a", "b", "c"):
            await service.create_element(element_type=E.NODE, name=name)
        saved = await saved_query_service.create(name="Tous", query=ElementQuery(limit=200))

        result = await saved_query_service.run(saved.id, limit=2, offset=0)

        assert (len(result.elements), result.total) == (2, 3)

    async def test_a_page_outside_the_bounds_is_a_value_error(
        self, saved_query_service: SavedQueryService
    ) -> None:
        saved = await saved_query_service.create(name="A", query=ElementQuery())

        with pytest.raises(ValueError, match="limit"):
            await saved_query_service.run(saved.id, limit=0)

    async def test_a_package_that_is_gone_is_an_unknown_package_and_the_query_stays_editable(
        self,
        saved_query_service: SavedQueryService,
        packages: InMemoryPackages,
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        saved = await saved_query_service.create(
            name="Compta", query=ElementQuery(package_id=finance.id)
        )
        await packages.delete(finance.id, into=None)

        with pytest.raises(UnknownPackageError, match=str(finance.id)):
            await saved_query_service.run(saved.id)

        fixed = await saved_query_service.update(saved.id, query=ElementQuery(root_only=True))
        assert (await saved_query_service.run(fixed.id)).total == 0

    async def test_a_package_that_exists_runs(
        self, saved_query_service: SavedQueryService, packages: InMemoryPackages
    ) -> None:
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW))
        saved = await saved_query_service.create(
            name="Compta", query=ElementQuery(package_id=finance.id)
        )

        assert (await saved_query_service.run(saved.id)).total == 0

    async def test_an_unreadable_stored_query_is_an_invalid_query_and_does_not_hide_the_list(
        self, saved_query_service: SavedQueryService, saved_queries: InMemorySavedQueries
    ) -> None:
        good = await saved_query_service.create(name="Bonne", query=ElementQuery())
        broken = await saved_query_service.create(name="Cassée", query=ElementQuery())
        saved_queries.corrupt.add(broken.id)

        with pytest.raises(InvalidQueryError):
            await saved_query_service.run(broken.id)

        assert [s.name for s in await saved_query_service.list_queries()] == ["Bonne", "Cassée"]
        assert (await saved_query_service.run(good.id)).total == 0


class TestTheAuditTrail:
    async def test_each_write_leaves_one_line_with_the_id(
        self, saved_query_service: SavedQueryService, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="ea.services"):
            saved = await saved_query_service.create(name="A", query=ElementQuery())
            await saved_query_service.update(saved.id, name="B")
            await saved_query_service.delete(saved.id)

        lines = [r for r in caplog.records if getattr(r, "action", "").startswith("query_")]
        assert [r.action for r in lines] == ["query_saved", "query_updated", "query_deleted"]  # type: ignore[attr-defined]
        assert all(r.query_id == str(saved.id) for r in lines)  # type: ignore[attr-defined]
