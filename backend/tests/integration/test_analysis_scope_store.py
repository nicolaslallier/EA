"""Scoping an analysis by package, against a real PostgreSQL."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.analytics import UNSET_KEY, Dimension, Metric, group_elements, summarise
from ea.domain.archimate import ElementType as E
from ea.domain.ports import ElementFilter
from ea.domain.quality import RuleCode
from ea.main import build_scope_resolver
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from tests.integration.exchange_support import file_element, make_package

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_the_package_dimension_agrees_with_the_pure_reference(
    engine_at_head: AsyncEngine,
    graph_service: ArchitectureService,
    graph_repository: PostgresArchitectureRepository,
) -> None:
    sessions = create_session_factory(engine_at_head)
    one, other = await make_package(sessions, "One"), await make_package(sessions, "Other")
    a = await graph_service.create_element(element_type=E.NODE, name="a")
    b = await graph_service.create_element(element_type=E.NODE, name="b")
    await graph_service.create_element(element_type=E.NODE, name="unfiled")
    await file_element(sessions, a.id, one)
    await file_element(sessions, b.id, other)
    stored = await graph_service.list_elements(ElementFilter(limit=10))
    dims = (Dimension.parse("package"),)

    from_sql = summarise(await graph_repository.grouped(dims, None), dims, Metric.COUNT)
    from_python = summarise(group_elements(stored, dims), dims, Metric.COUNT)

    assert from_sql == from_python
    assert UNSET_KEY in {bucket.keys[0] for bucket in from_sql.buckets}


async def test_a_package_scope_narrows_the_checks_to_its_subtree(
    engine_at_head: AsyncEngine,
    graph_service: ArchitectureService,
    graph_repository: PostgresArchitectureRepository,
) -> None:
    sessions = create_session_factory(engine_at_head)
    top = await make_package(sessions, "Top")
    child = await make_package(sessions, "Child", top)
    inside = await graph_service.create_element(element_type=E.NODE, name="inside")
    deeper = await graph_service.create_element(element_type=E.NODE, name="deeper")
    await graph_service.create_element(element_type=E.NODE, name="outside")
    await file_element(sessions, inside.id, top)
    await file_element(sessions, deeper.id, child)
    app = FastAPI()
    app.state.architecture_service = graph_service
    analysis = AnalysisService(graph_service, graph_repository, scope_ids=build_scope_resolver(app))

    page = await analysis.check(code=RuleCode.ORPHAN, package_id=top)

    assert {f.element_name for f in page.findings} == {"inside", "deeper"}
