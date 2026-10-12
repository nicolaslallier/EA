"""Publishing and reopening, against a real PostgreSQL (docs/adr/0074)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import work
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, EntityKind, package_version_view
from ea.domain.baselines import BaselineLimits
from ea.domain.errors import (
    BaselineTooLargeError,
    PackageAlreadyFrozenError,
    PackageFrozenError,
    PackageNotFoundError,
    PackageNotFrozenError,
)
from ea.domain.package_versions import Bump, PackageVersion, publication
from ea.repositories.package_store import PostgresPackageRepository
from tests.integration.test_package_freeze_triggers import a_package, an_element

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def an_event(action: AuditAction, entity_id: UUID, version: PackageVersion) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=NOW,
        actor_sub="approver-1",
        actor_name="approver",
        action=action,
        entity_kind=EntityKind.PACKAGE,
        entity_id=entity_id,
        before=None,
        after=package_version_view(version),
        request_id="r-1",
        channel=AuditChannel.HTTP,
        correlation=None,
    )


async def publish_it(
    repo: PostgresPackageRepository,
    package_id: UUID,
    *,
    bump: Bump = Bump.MINOR,
    notes: str = "notes",
    adr: str | None = None,
    now: datetime = NOW,
) -> PackageVersion:
    return await repo.publish(
        package_id,
        publication=publication(bump=bump, notes=notes, adr=adr),
        by_sub="approver-1",
        by="approver",
        now=now,
        audit=lambda stored: [an_event(AuditAction.PACKAGE_PUBLISHED, package_id, stored)],
    )


async def reopen_it(repo: PostgresPackageRepository, package_id: UUID) -> PackageVersion:
    return await repo.reopen(
        package_id,
        reason="coquille",
        by_sub="approver-1",
        by="approver",
        now=NOW,
        audit=lambda stored: [an_event(AuditAction.PACKAGE_REOPENED, package_id, stored)],
    )


@pytest.fixture
def repo(engine_at_head: AsyncEngine) -> PostgresPackageRepository:
    return PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))


async def test_publishing_freezes_the_subtree_takes_a_baseline_and_writes_one_event(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        inside = await an_element(connection, "inside", child)

    version = await publish_it(repo, root, notes='« un »\n"deux" — é')

    assert version.version == "v1.0.0"
    assert version.notes == '« un »\n"deux" — é'
    assert version.element_count == 1
    assert version.publish_audit_event_id is not None
    assert await repo.in_force() == {root: "v1.0.0"}
    async with engine_at_head.connect() as connection:
        scope = (
            await connection.execute(
                text("SELECT scope_package_id, scope_path FROM baselines WHERE id = :b"),
                {"b": version.baseline_id},
            )
        ).one()
        events = (
            await connection.execute(
                text("SELECT id, action FROM audit_events WHERE entity_id = :p"), {"p": root}
            )
        ).all()
    assert tuple(scope) == (root, "Finance")
    assert [(e[0], e[1]) for e in events] == [(version.publish_audit_event_id, "package.published")]
    with pytest.raises(PackageFrozenError):
        async with work(async_sessionmaker(engine_at_head)) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": inside}
            )


async def test_an_empty_package_can_be_published(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Vide", None)
    version = await publish_it(repo, root)
    assert (version.element_count, version.relationship_count, version.box_count) == (0, 0, 0)
    assert await repo.in_force() == {root: "v1.0.0"}


async def test_the_next_version_follows_the_latest_even_after_a_reopening(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root)
    closed = await reopen_it(repo, root)
    assert closed.reopened_at is not None and closed.reopen_audit_event_id is not None
    assert await repo.in_force() == {}
    second = await publish_it(
        repo, root, bump=Bump.MAJOR, adr="ADR-0042", now=NOW + timedelta(days=1)
    )
    assert second.version == "v2.0.0"
    assert [v.version for v in await repo.versions(root)] == [second.version, "v1.0.0"]


async def test_a_package_already_published_or_under_one_cannot_be_published(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
    await publish_it(repo, root)
    for target in (root, child):
        with pytest.raises(PackageAlreadyFrozenError):
            await publish_it(repo, target)
        with pytest.raises(PackageAlreadyFrozenError):
            await repo.preview_publication(target, bump=Bump.MINOR)


async def test_publishing_a_parent_whose_child_is_already_published_is_allowed(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
    await publish_it(repo, child)
    await publish_it(repo, root)
    assert await repo.in_force() == {root: "v1.0.0", child: "v1.0.0"}
    await reopen_it(repo, root)
    assert await repo.in_force() == {child: "v1.0.0"}


async def test_reopening_a_package_under_a_published_ancestor_still_leaves_it_frozen(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        inside = await an_element(connection, "inside", child)
    await publish_it(repo, child)
    await publish_it(repo, root)
    await reopen_it(repo, child)
    with pytest.raises(PackageFrozenError) as raised:
        async with work(async_sessionmaker(engine_at_head)) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": inside}
            )
    assert raised.value.refusal is not None and raised.value.refusal.package_id == root


async def test_reopening_what_is_not_published_or_does_not_exist(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    with pytest.raises(PackageNotFrozenError):
        await reopen_it(repo, root)
    with pytest.raises(PackageNotFoundError):
        await reopen_it(repo, uuid4())
    with pytest.raises(PackageNotFoundError):
        await publish_it(repo, uuid4())


async def test_a_subtree_past_the_ceiling_is_refused_whole_and_nothing_is_written(
    engine_at_head: AsyncEngine,
) -> None:
    repo = PostgresPackageRepository(
        async_sessionmaker(engine_at_head, expire_on_commit=False),
        limits=BaselineLimits(elements=1),
    )
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await an_element(connection, "one", root)
        await an_element(connection, "two", root)
    with pytest.raises(BaselineTooLargeError):
        await publish_it(repo, root)
    async with engine_at_head.connect() as connection:
        left = (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM baselines)"
                    " + (SELECT count(*) FROM package_versions)"
                )
            )
        ).scalar_one()
    assert left == 0


async def test_two_simultaneous_publications_of_one_package_let_exactly_one_through(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    results = await asyncio.gather(
        publish_it(repo, root), publish_it(repo, root), return_exceptions=True
    )
    published = [r for r in results if isinstance(r, PackageVersion)]
    refused = [r for r in results if isinstance(r, PackageAlreadyFrozenError)]
    assert (len(published), len(refused)) == (1, 1), results


async def test_a_write_racing_a_publication_is_either_in_the_baseline_or_refused(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    """The freeze and the copy see the same state: no write can slip in between."""
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        inside = await an_element(connection, "inside", root)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)

    async def write() -> str:
        try:
            async with work(sessions) as session:
                await session.execute(
                    text("UPDATE elements SET description = 'late' WHERE id = :id"),
                    {"id": inside},
                )
        except PackageFrozenError:
            return "refused"
        return "written"

    outcome, version = await asyncio.gather(write(), publish_it(repo, root))
    async with engine_at_head.connect() as connection:
        copied = (
            await connection.execute(
                text("SELECT description FROM baseline_elements WHERE baseline_id = :b"),
                {"b": version.baseline_id},
            )
        ).scalar_one()
    if outcome == "written":
        assert copied == "late"  # the write committed first, so the baseline holds it
    else:
        assert copied == ""
