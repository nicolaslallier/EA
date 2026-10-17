"""Publishing and reopening, against a real PostgreSQL (docs/adr/0074)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import work
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, EntityKind, package_version_view
from ea.domain.baselines import BaselineLimits
from ea.domain.errors import (
    BaselineInUseError,
    BaselineTooLargeError,
    LiveViewInSubtreeError,
    PackageAlreadyFrozenError,
    PackageFrozenError,
    PackageHasVersionsError,
    PackageNotFoundError,
    PackageNotFrozenError,
)
from ea.domain.package_versions import Bump, PackageVersion, publication
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.services.baselines import BaselineService
from ea.services.packages import PackageService
from tests.integration.test_package_freeze_triggers import a_diagram, a_package, an_element

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
        audit=lambda stored: [
            an_event(AuditAction.PACKAGE_PUBLISHED, package_id, stored),
            # Like the service: the scoped baseline's own event rides in the same transaction.
            replace(
                an_event(AuditAction.BASELINE_CREATED, stored.baseline_id, stored),
                entity_kind=EntityKind.BASELINE,
                after={"id": str(stored.baseline_id), "scope_package_id": str(package_id)},
            ),
        ],
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
    second = await publish_it(repo, root, bump=Bump.MAJOR, adr="ADR-0042")
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


async def test_a_clock_that_stepped_back_still_numbers_the_versions_in_order(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root, now=NOW)
    await reopen_it(repo, root)
    second = await publish_it(repo, root, now=NOW - timedelta(days=1))
    await reopen_it(repo, root)
    third = await publish_it(repo, root, now=NOW - timedelta(days=2))
    assert (second.version, third.version) == ("v1.1.0", "v1.2.0")
    assert [v.version for v in await repo.versions(root)] == ["v1.2.0", "v1.1.0", "v1.0.0"]


async def test_the_scoped_baseline_is_named_after_its_own_random_id(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    """Names are unique: a name an editor can guess is a way to refuse an approver."""
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        for guess in (f"Finance v1.0.0 [{str(root)[:8]}]", f"Finance v1.0.0 [{root}]"):
            await connection.execute(
                text(
                    "INSERT INTO baselines (id, name, description, created_by_sub, created_by,"
                    " created_at) VALUES (:id, :name, '', 'x', 'x', :at)"
                ),
                {"id": uuid4(), "name": guess, "at": NOW},
            )
    version = await publish_it(repo, root)
    async with engine_at_head.connect() as connection:
        name = (
            await connection.execute(
                text("SELECT name FROM baselines WHERE id = :b"), {"b": version.baseline_id}
            )
        ).scalar_one()
    assert str(version.baseline_id) in name


async def test_a_subtree_holding_a_live_view_cannot_be_published(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        other = await a_package(connection, "Source", None)
        await a_diagram(connection, "drawn", child)  # by hand: no block
        frozen = await a_diagram(connection, "frozen", child)
        live = await a_diagram(connection, "live", child)
        for diagram, generation in ((frozen, "frozen"), (live, "live")):
            await connection.execute(
                text("UPDATE diagrams SET generation = :g, source_package_id = :s WHERE id = :id"),
                {"g": generation, "s": other, "id": diagram},
            )
    with pytest.raises(LiveViewInSubtreeError):
        await repo.preview_publication(root, bump=Bump.MINOR)
    with pytest.raises(LiveViewInSubtreeError):
        await publish_it(repo, root)
    async with engine_at_head.connect() as connection:
        written = (
            await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM baselines)"
                    " + (SELECT count(*) FROM package_versions)"
                )
            )
        ).scalar_one()
    assert written == 0
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE diagrams SET generation = 'frozen' WHERE id = :id"), {"id": live}
        )
    assert (await publish_it(repo, root)).version == "v1.0.0"


async def test_a_package_that_was_ever_published_cannot_be_deleted(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root)
    await reopen_it(repo, root)
    with pytest.raises(PackageHasVersionsError):
        await repo.delete(root, into=None, audit=())
    assert await repo.get(root) is not None


async def test_a_baseline_that_is_the_copy_of_a_version_cannot_be_deleted(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    version = await publish_it(repo, root)
    await reopen_it(repo, root)
    baselines = PostgresBaselineRepository(async_sessionmaker(engine_at_head))
    with pytest.raises(BaselineInUseError):
        await baselines.delete(version.baseline_id, audit=())
    assert await baselines.get(version.baseline_id) is not None


async def test_a_restore_planned_before_a_publication_cannot_write_into_the_frozen_subtree(
    engine_at_head: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restore's snapshot predates the freeze: it must wait, not slip a write past it."""
    from ea.repositories import baseline_store
    from tests.integration.test_baseline_restore import LATER, restored
    from tests.integration.test_baseline_store import Model, a_baseline, created

    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)
    repo = PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))
    root = next(p.id for p in await model.packages.list_all() if p.name == "Core")

    planned, proceed = asyncio.Event(), asyncio.Event()
    real_write = baseline_store._write

    async def slow_write(*args: object, **kwargs: object) -> None:
        planned.set()
        await proceed.wait()
        await real_write(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(baseline_store, "_write", slow_write)
    restoring = asyncio.ensure_future(
        model.baselines.apply_restore(
            baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )
    )
    await asyncio.wait_for(planned.wait(), 10)
    publishing = asyncio.ensure_future(publish_it(repo, root))
    await asyncio.wait({publishing}, timeout=1)  # unfixed, it commits here; fixed, it waits
    proceed.set()
    outcomes = await asyncio.gather(restoring, publishing, return_exceptions=True)

    async with engine_at_head.connect() as connection:
        frozen = (
            (
                await connection.execute(
                    text(
                        "SELECT name FROM baseline_elements WHERE baseline_id = ("
                        "SELECT baseline_id FROM package_versions WHERE package_id = :p)"
                    ),
                    {"p": root},
                )
            )
            .scalars()
            .all()
        )
        live = (
            await connection.execute(
                text("SELECT name FROM elements WHERE id = :id"), {"id": model.left.id}
            )
        ).scalar_one()
    published = [o for o in outcomes if isinstance(o, PackageVersion)]
    assert len(published) == 1, outcomes
    # What was frozen is what the subtree holds: the restore either landed before the copy
    # or was refused after it; it never rewrote a subtree that had already been copied.
    assert frozen == [live]
    assert all(o is None or isinstance(o, PackageVersion | PackageFrozenError) for o in outcomes), (
        outcomes
    )


async def _async[T](value: T) -> T:
    return value


async def name_of(engine: AsyncEngine, element_id: UUID) -> str:
    async with engine.connect() as connection:
        found = await connection.execute(
            text("SELECT name FROM elements WHERE id = :i"), {"i": element_id}
        )
        return str(found.scalar_one())


async def rename(engine: AsyncEngine, element_id: UUID, name: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET name = :n WHERE id = :i"), {"n": name, "i": element_id}
        )


async def test_restoring_a_published_version_is_refused_until_it_is_reopened(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        finance = await a_package(connection, "Finance", None)
        await an_element(connection, "inside", finance)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    # Wired as main.py does: the real PackageService says what is frozen.
    baselines = BaselineService(
        PostgresBaselineRepository(sessions), freezes=PackageService(repo).freezes
    )
    version = await publish_it(repo, finance)

    outcome = await baselines.restore(version.baseline_id)
    assert outcome.plan.frozen_by is not None
    assert outcome.plan.frozen_by.version == version.version and outcome.plan.is_blocked
    with pytest.raises(PackageFrozenError):
        await baselines.restore(version.baseline_id, dry_run=False, expected_digest=outcome.digest)

    await reopen_it(repo, finance)
    outcome = await baselines.restore(version.baseline_id)
    assert outcome.plan.frozen_by is None
    await baselines.restore(version.baseline_id, dry_run=False, expected_digest=outcome.digest)


async def test_the_triggers_still_refuse_when_the_prediction_is_bypassed(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    """The race: the service checked before the publication; the database is the authority."""
    async with engine_at_head.begin() as connection:
        finance = await a_package(connection, "Finance", None)
        inside = await an_element(connection, "inside", finance)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    stale = BaselineService(
        PostgresBaselineRepository(sessions), freezes=lambda: _async({})
    )  # predicts nothing
    first = await publish_it(repo, finance)  # v1.0.0 copies Finance as it was
    await reopen_it(repo, finance)
    await rename(engine_at_head, inside, "renamed")  # the plan back to v1.0.0 is not a no-op
    await publish_it(repo, finance)  # v1.1.0 in force

    outcome = await stale.restore(first.baseline_id)
    assert outcome.plan.elements_to_update  # there is something to write
    with pytest.raises(PackageFrozenError):
        await stale.restore(first.baseline_id, dry_run=False, expected_digest=outcome.digest)

    assert await name_of(engine_at_head, inside) == "renamed"


async def test_a_publication_nested_in_the_subtree_is_predicted_and_refused(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    """The scope is not published, its child is: the plan writes into the child."""
    async with engine_at_head.begin() as connection:
        finance = await a_package(connection, "Finance", None)
        budget = await a_package(connection, "Budget", finance)
        inside = await an_element(connection, "inside", budget)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    baselines = BaselineService(
        PostgresBaselineRepository(sessions), freezes=PackageService(repo).freezes
    )
    scoped_id = (await baselines.create(name="Finance", package_id=finance)).id
    await rename(engine_at_head, inside, "renamed")
    await publish_it(repo, budget)

    outcome = await baselines.restore(scoped_id)
    assert outcome.plan.frozen_by is not None and outcome.plan.is_blocked
    assert (outcome.plan.frozen_by.package_id, outcome.plan.frozen_by.version) == (
        budget,
        "v1.0.0",
    )
    with pytest.raises(PackageFrozenError):
        await baselines.restore(scoped_id, dry_run=False, expected_digest=outcome.digest)

    assert await name_of(engine_at_head, inside) == "renamed"
