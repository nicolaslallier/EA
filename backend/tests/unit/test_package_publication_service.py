"""Publishing and reopening, as the service decides them."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.audit import AuditAction
from ea.domain.errors import (
    AdrRequiredError,
    InvalidPublicationError,
    NotAuthorisedError,
    PackageAlreadyFrozenError,
    PackageNotFoundError,
    PackageNotFrozenError,
)
from ea.domain.package_versions import Bump
from ea.services.caller import acting_as
from ea.services.packages import PackageService
from tests.conftest import InMemoryPackages, a_reader, an_approver, an_editor

pytestmark = pytest.mark.asyncio


async def test_the_default_is_a_dry_run_that_changes_nothing(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        outcome = await package_service.publish(root.package.id, bump=Bump.MINOR, notes="Première")
    assert outcome.published is None
    assert outcome.preview.version == "v1.0.0"
    assert packages.published == []


async def test_publishing_for_real_writes_a_version_and_the_events_of_it_and_of_its_baseline(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    before = len(packages.audit_log.events)
    with acting_as(an_approver()):
        outcome = await package_service.publish(
            root.package.id, bump=Bump.MINOR, notes="Première", dry_run=False
        )
    assert outcome.published is not None
    assert outcome.published.version == "v1.0.0"
    events = packages.audit_log.events[before:]
    assert [e.action for e in events] == [
        AuditAction.PACKAGE_PUBLISHED,
        AuditAction.BASELINE_CREATED,
    ]
    assert outcome.published.publish_audit_event_id == events[0].id
    created = events[1]
    assert created.entity_id == outcome.published.baseline_id
    assert created.after is not None
    assert created.after["scope_package_id"] == str(root.package.id)
    assert created.after["scope_path"] == "Finance"
    assert {"id", "elements", "relationships", "boxes"} <= set(created.after)
    assert "Première" not in repr(created.after)
    assert events[0].actor_sub == "approver-1"
    assert "Première" not in repr(events[0].after)


@pytest.mark.parametrize("caller", [an_editor, a_reader])
async def test_only_an_approver_publishes_or_reopens(
    package_service: PackageService, caller: object
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(caller()):  # type: ignore[operator]
        with pytest.raises(NotAuthorisedError):
            await package_service.publish(
                root.package.id, bump=Bump.MINOR, notes="n", dry_run=False
            )
        with pytest.raises(NotAuthorisedError):
            await package_service.reopen(root.package.id, reason="r")


async def test_the_words_are_checked_before_anything_is_read_or_written(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        with pytest.raises(InvalidPublicationError):
            await package_service.publish(root.package.id, bump=Bump.MINOR, notes=" ")
        with pytest.raises(AdrRequiredError):
            await package_service.publish(root.package.id, bump=Bump.MAJOR, notes="n")
        with pytest.raises(PackageNotFoundError):
            await package_service.publish(uuid4(), bump=Bump.MINOR, notes="n")
    assert packages.published == []


async def test_reopening_closes_the_version_and_leaves_its_own_event(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
        closed = await package_service.reopen(root.package.id, reason="coquille")
        with pytest.raises(PackageNotFrozenError):
            await package_service.reopen(root.package.id, reason="encore")
    assert closed.reopen_reason == "coquille"
    last = packages.audit_log.events[-1]
    assert last.action is AuditAction.PACKAGE_REOPENED
    assert "coquille" not in repr(last.after)


async def test_a_package_under_a_published_one_is_read_as_frozen_and_offers_nothing(
    package_service: PackageService,
) -> None:
    root = await package_service.create(name="Finance")
    child = await package_service.create(name="Budget", parent_id=root.package.id)
    other = await package_service.create(name="RH")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
        read_child = await package_service.get(child.package.id)
        read_root = await package_service.get(root.package.id)
        read_other = await package_service.get(other.package.id)
        with pytest.raises(PackageAlreadyFrozenError):
            await package_service.publish(child.package.id, bump=Bump.MINOR, notes="n")
    assert read_root.own_version == "v1.0.0" and read_root.actions == ("reopen",)
    assert read_child.own_version is None and read_child.actions == ()
    assert read_child.frozen_by is not None and read_child.frozen_by.path == "Finance"
    assert read_other.frozen_by is None and read_other.actions == ("publish",)


async def test_versions_are_listed_for_any_reader(package_service: PackageService) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
    with acting_as(a_reader()):
        assert [v.version for v in await package_service.versions(root.package.id)] == ["v1.0.0"]
