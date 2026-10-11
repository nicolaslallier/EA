"""Every door that writes properties refuses a bad validity date — PATCH included."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import InvalidLifespanError
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository

pytestmark = pytest.mark.asyncio


async def test_creating_with_a_partial_date_stores_nothing(
    service: ArchitectureService, repository: InMemoryRepository
) -> None:
    with pytest.raises(InvalidLifespanError):
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            properties={"valid_from": "2027-06"},
        )

    assert repository.elements == {}


async def test_creating_stores_the_canonical_dates(service: ArchitectureService) -> None:
    created = await service.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        properties={"valid_from": "20240301", "valid_until": ""},
    )

    assert dict(created.properties) == {"valid_from": "2024-03-01"}


async def test_patching_is_not_a_back_door(service: ArchitectureService) -> None:
    element = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

    with pytest.raises(InvalidLifespanError):
        await service.update_element(
            element.id, properties={"valid_from": "2027-01-02", "valid_until": "2027-01-01"}
        )

    assert dict((await service.get_element(element.id)).properties) == {}


async def test_an_update_that_leaves_properties_alone_checks_nothing(
    service: ArchitectureService,
) -> None:
    element = await service.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        properties={"valid_from": "2026-01-01"},
    )

    renamed = await service.update_element(element.id, name="Invoicing")

    assert dict(renamed.properties) == {"valid_from": "2026-01-01"}


async def test_completing_is_not_a_back_door_either(service: ArchitectureService) -> None:
    """The pipeline fills blanks through `complete_element`; it must not slip a bad date in."""
    element = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")

    with pytest.raises(InvalidLifespanError):
        await service.complete_element(element.id, properties={"valid_until": "2027-06"})

    assert dict((await service.get_element(element.id)).properties) == {}


async def test_completing_stores_the_canonical_date_and_never_overwrites(
    service: ArchitectureService,
) -> None:
    element = await service.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        properties={"valid_from": "2026-01-01"},
    )

    completed = await service.complete_element(
        element.id, properties={"valid_from": "2030-01-01", "valid_until": "20271231"}
    )

    assert dict(completed.properties) == {"valid_from": "2026-01-01", "valid_until": "2027-12-31"}


async def test_completing_cannot_end_before_what_is_already_there(
    service: ArchitectureService,
) -> None:
    element = await service.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        properties={"valid_from": "2026-06-01"},
    )

    with pytest.raises(InvalidLifespanError):
        await service.complete_element(element.id, properties={"valid_until": "2026-01-01"})


async def test_a_relationship_carries_a_lifespan_too(service: ArchitectureService) -> None:
    source = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="A")
    target = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="B")

    with pytest.raises(InvalidLifespanError):
        await service.connect(
            relationship_type=R.SERVING,
            source_id=source.id,
            target_id=target.id,
            properties={"valid_until": "bientôt"},
        )

    link = await service.connect(
        relationship_type=R.SERVING,
        source_id=source.id,
        target_id=target.id,
        properties={"valid_until": "20271231"},
    )
    assert dict(link.properties) == {"valid_until": "2027-12-31"}
