"""The quality run against a real PostgreSQL: the links it reads are the stored ones."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.quality import RuleCode
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from tests.support_analysis import a_stored_link

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_orphans_are_found_from_the_stored_links(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    api = await graph_service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")
    loner = await graph_service.create_element(element_type=E.NODE, name="Loner")
    await graph_service.connect(relationship_type=R.SERVING, source_id=api.id, target_id=process.id)

    page = await AnalysisService(graph_service, graph_repository).check(code=RuleCode.ORPHAN)

    assert [f.element_id for f in page.findings] == [loner.id]


async def test_a_link_stored_before_the_rules_changed_is_reported_not_deleted(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    data = await graph_service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
    process = await graph_service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")
    illegal = a_stored_link(R.SERVING, data, process)
    await graph_repository.add_relationship(illegal, audit=())

    page = await AnalysisService(graph_service, graph_repository).check(
        code=RuleCode.INCONSISTENT_RELATIONSHIP
    )

    assert [(f.element_id, f.related_id) for f in page.findings] == [(data.id, process.id)]
    assert await graph_repository.get_relationship(illegal.id) is not None


async def test_the_run_is_the_same_twice(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    for name in ("Facturation client", "Facturation clients"):
        await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)
    analysis = AnalysisService(graph_service, graph_repository)

    assert await analysis.check() == await analysis.check()
