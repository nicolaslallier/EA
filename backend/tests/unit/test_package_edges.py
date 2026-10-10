"""The refusals of the package code that the happy-path suites do not reach."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI

from ea.api.dependencies import package_service_of
from ea.domain.diagrams import MAX_DIAGRAM_NAME_LENGTH, Diagram
from ea.domain.errors import PackageNotFoundError
from ea.domain.packages import UNSET, Package, path_of
from ea.services.packages import PackageService
from tests.conftest import InMemoryPackages

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_the_unset_marker_has_a_readable_repr() -> None:
    assert repr(UNSET) == "UNSET"


def test_a_path_stops_at_a_parent_that_is_gone() -> None:
    orphan = Package.create(name="Orpheline", parent_id=uuid4(), now=NOW)

    assert path_of(orphan, {orphan.id: orphan}) == "Orpheline"


def test_a_diagram_name_has_a_ceiling() -> None:
    with pytest.raises(ValueError, match=str(MAX_DIAGRAM_NAME_LENGTH)):
        Diagram.create(name="x" * (MAX_DIAGRAM_NAME_LENGTH + 1), now=NOW)


def test_an_application_without_a_package_service_says_so() -> None:
    with pytest.raises(RuntimeError, match="package service"):
        package_service_of(FastAPI())


@pytest.mark.asyncio
async def test_moving_under_an_unknown_parent_is_a_not_found(
    package_service: PackageService,
) -> None:
    created = await package_service.create(name="A")

    with pytest.raises(PackageNotFoundError):
        await package_service.update(created.package.id, parent_id=uuid4())


@pytest.mark.asyncio
async def test_a_package_deleted_between_the_read_and_the_write_is_a_not_found(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    created = await package_service.create(name="A")

    async def gone(package_id: object, *, into: object) -> bool:
        return False

    packages.delete = gone  # type: ignore[method-assign]
    with pytest.raises(PackageNotFoundError):
        await package_service.delete(created.package.id, move_contents_to=None)


def test_an_empty_view_and_an_element_layer_read_plainly() -> None:
    from ea.domain.archimate import ElementType
    from ea.domain.model import Element
    from ea.domain.ports import GraphView

    assert GraphView(elements=(), relationships=()).is_empty
    assert Element.create(element_type=ElementType.NODE, name="n", now=NOW).layer == "technology"


@pytest.mark.asyncio
async def test_a_diagram_deleted_between_the_read_and_the_layout_write_is_a_not_found(
    diagram_service: object, diagrams: object
) -> None:
    from ea.domain.errors import DiagramNotFoundError

    created = await diagram_service.create(name="Vente")  # type: ignore[attr-defined]

    async def gone(*_: object, **__: object) -> bool:
        return False

    diagrams.replace_layout = gone  # type: ignore[attr-defined]
    with pytest.raises(DiagramNotFoundError):
        await diagram_service.replace_layout(created.id, [])  # type: ignore[attr-defined]
