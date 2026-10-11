"""Two refusals of the package code that the happy-path suites do not exercise."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.diagrams import MAX_DIAGRAM_NAME_LENGTH, Diagram
from ea.domain.errors import PackageNotFoundError
from ea.domain.packages import Package, path_of
from ea.services.packages import PackageService

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_a_path_stops_at_a_parent_that_is_gone() -> None:
    orphan = Package.create(name="Orpheline", parent_id=uuid4(), now=NOW)

    assert path_of(orphan, {orphan.id: orphan}) == "Orpheline"


def test_a_diagram_name_has_a_ceiling() -> None:
    with pytest.raises(ValueError, match=str(MAX_DIAGRAM_NAME_LENGTH)):
        Diagram.create(name="x" * (MAX_DIAGRAM_NAME_LENGTH + 1), now=NOW)


@pytest.mark.asyncio
async def test_moving_under_an_unknown_parent_is_a_not_found(
    package_service: PackageService,
) -> None:
    created = await package_service.create(name="A")

    with pytest.raises(PackageNotFoundError):
        await package_service.update(created.package.id, parent_id=uuid4())
