"""Templates against a real PostgreSQL: jsonb round trip, unique name, atomic build."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType
from ea.domain.diagram_templates import MAX_TEMPLATES, Binding, DiagramTemplate, parse_body
from ea.domain.errors import DuplicateElementError, DuplicateTemplateError, TemplateLimitError
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.template_store import PostgresTemplateRepository
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from ea.services.templates import TemplateService
from tests.unit.test_template_service import BODY

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def a_template(name: str = "Modèle") -> DiagramTemplate:
    return DiagramTemplate(uuid4(), name, "é", parse_body(BODY), NOW, NOW)


@pytest.fixture
def templates(engine_at_head: AsyncEngine) -> PostgresTemplateRepository:
    return PostgresTemplateRepository(create_session_factory(engine_at_head))


async def test_a_template_survives_the_round_trip(templates: PostgresTemplateRepository) -> None:
    stored = await templates.add(a_template(), audit=())

    assert await templates.get(stored.id) == stored


async def test_a_name_is_unique_and_the_store_stays_usable(
    templates: PostgresTemplateRepository,
) -> None:
    await templates.add(a_template("A"), audit=())

    with pytest.raises(DuplicateTemplateError):
        await templates.add(a_template("A"), audit=())
    await templates.add(a_template("B"), audit=())

    assert [t.name for t in await templates.list_all()] == ["A", "B"]


async def test_save_and_delete(templates: PostgresTemplateRepository) -> None:
    stored = await templates.add(a_template(), audit=())

    renamed = await templates.save(
        DiagramTemplate(stored.id, "Autre", "", stored.body, NOW, NOW), audit=()
    )

    read = await templates.get(stored.id)
    assert read is not None and read.name == "Autre"
    assert await templates.delete(renamed.id, audit=()) is True
    assert await templates.delete(renamed.id, audit=()) is False


async def test_an_instantiation_that_fails_half_way_leaves_nothing_behind(
    engine_at_head: AsyncEngine, templates: PostgresTemplateRepository
) -> None:
    sessions = create_session_factory(engine_at_head)
    architecture = ArchitectureService(PostgresArchitectureRepository(sessions))
    diagrams = DiagramService(PostgresDiagramRepository(sessions), architecture)
    service = TemplateService(templates, architecture, diagrams)
    template = await service.create_template(name="T", body=BODY)
    # The second slot's element already exists under that name: the build fails after the
    # first element and the diagram were written.
    await architecture.create_element(element_type=ElementType.NODE, name="srv-pris")
    before = len(await architecture.list_elements(ElementFilter()))

    with pytest.raises(DuplicateElementError):
        await service.instantiate(
            template.id,
            name="Vue atomique",
            bindings=[Binding("app", create_name="Neuve"), Binding("host", create_name="srv-pris")],
        )

    assert len(await architecture.list_elements(ElementFilter())) == before
    assert await diagrams.list_diagrams() == ()


async def test_two_concurrent_saves_at_the_edge_cannot_both_get_in(
    templates: PostgresTemplateRepository,
) -> None:
    for i in range(MAX_TEMPLATES - 1):
        await templates.add(a_template(f"T{i}"), audit=())

    outcomes = await asyncio.gather(
        templates.add(a_template("last-a"), audit=()),
        templates.add(a_template("last-b"), audit=()),
        return_exceptions=True,
    )

    assert sum(isinstance(o, TemplateLimitError) for o in outcomes) == 1
    assert len(await templates.list_all()) == MAX_TEMPLATES
