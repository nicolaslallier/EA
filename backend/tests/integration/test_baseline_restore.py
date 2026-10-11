"""Restoring a baseline against a real PostgreSQL: all of it, or none of it (docs/adr/0045)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, AuditEvent, AuditFilter
from ea.domain.baselines import RestorePlan
from ea.domain.errors import RestoreBlockedError, RestoreConflictError
from ea.domain.model import Element
from ea.repositories.baseline_store import PostgresBaselineRepository
from tests.integration.test_baseline_store import Model, a_baseline, an_event, created

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


def restored(plan: RestorePlan) -> tuple[AuditEvent, ...]:
    return (an_event(AuditAction.BASELINE_RESTORED, None, at=LATER),)


async def restore(model: Model, baseline_id: UUID, *, keep_new: bool = False) -> RestorePlan:
    plan = await model.baselines.restore_plan(baseline_id, keep_new=keep_new)
    await model.baselines.apply_restore(
        baseline_id, keep_new=keep_new, expected=plan, now=LATER, audit=restored
    )
    return plan


async def events(model: Model) -> list[AuditAction]:
    return [e.action for e in (await model.trail.page(AuditFilter())).events]


async def test_a_restoration_recreates_reverts_deletes_and_leaves_one_trace(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)

    await model.graph.delete_element(model.right.id, audit=())  # takes the link too
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    await model.packages.move_elements([model.left.id], None, audit=())
    await model.diagrams.replace_layout(model.diagram.id, [], now=LATER, audit=())
    fresh = await model.graph.add_element(
        Element.create(element_type=E.NODE, name="fresh", now=LATER), audit=()
    )

    await restore(model, baseline.id)

    back = await model.graph.get_element(model.right.id)
    assert back is not None and back.name == "b"  # recreated under its original id
    left = await model.graph.get_element(model.left.id)
    assert left is not None and (left.name, left.package_id) == ("a", model.apps.id)
    assert await model.graph.get_element(fresh.id) is None
    assert await model.graph.get_relationship(model.link.id) is not None
    assert [(n.element_id, n.x) for n in await model.diagrams.nodes_of(model.diagram.id)] == [
        (model.left.id, 5.0)
    ]
    assert await events(model) == [AuditAction.BASELINE_RESTORED, AuditAction.BASELINE_CREATED]


async def test_a_vanished_package_sends_the_element_to_the_root_and_violates_nothing(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.packages.delete(model.apps.id, into=None, audit=())
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )

    plan = await restore(model, baseline.id)

    left = await model.graph.get_element(model.left.id)
    assert left is not None and (left.name, left.package_id) == ("a", None)
    assert any("no longer exists" in warning for warning in plan.warnings)
    assert await model.packages.get(model.apps.id) is None  # never recreated


async def test_a_conflict_in_the_middle_rolls_everything_back_and_names_the_culprit(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.delete_element(model.right.id, audit=())
    # Someone took the name while the baseline waited: recreating `b` must fail whole.
    squatter = await model.graph.add_element(
        Element.create(element_type=E.NODE, name="b", now=LATER), audit=()
    )
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )

    plan = await model.baselines.restore_plan(baseline.id, keep_new=True)
    with pytest.raises(RestoreConflictError, match="'b'"):
        await model.baselines.apply_restore(
            baseline.id, keep_new=True, expected=plan, now=LATER, audit=restored
        )

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "renamed"  # the update rolled back with the rest
    assert await model.graph.get_element(model.right.id) is None
    assert await model.graph.get_element(squatter.id) is not None
    assert await events(model) == [AuditAction.BASELINE_CREATED]  # no partial trace


async def test_deleting_by_restoration_takes_documents_and_boxes_like_a_delete(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    empty = await model.baselines.create(a_baseline("empty"), audit=created)
    await model.seed()

    plan = await model.baselines.restore_plan(empty.id, keep_new=False)
    assert (plan.documents_lost, plan.boxes_lost, plan.cascaded_relationships) == (1, 1, 1)
    await restore(model, empty.id)

    assert await model.graph.get_element(model.left.id) is None
    assert await model.diagrams.nodes_of(model.diagram.id) == ()
    assert await model.documents.list_for_element(model.left.id) == ()
    assert await model.diagrams.get(model.diagram.id) is not None  # the view survives


async def test_a_plan_read_before_the_model_moved_is_refused_not_applied(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)

    late = await model.graph.add_element(
        Element.create(element_type=E.NODE, name="late", now=LATER), audit=()
    )
    with pytest.raises(RestoreConflictError, match="since the plan"):
        await model.baselines.apply_restore(
            baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )

    assert await model.graph.get_element(late.id) is not None
    assert await events(model) == [AuditAction.BASELINE_CREATED]


async def test_an_element_protected_after_the_plan_was_read_stops_the_application(
    engine_at_head: AsyncEngine,
) -> None:
    protected: set[UUID] = set()

    async def read_under_the_transaction(session: AsyncSession) -> frozenset[UUID]:
        return frozenset(protected)

    sessions = create_session_factory(engine_at_head)
    model = Model(engine_at_head)
    model.baselines = PostgresBaselineRepository(sessions, protected=read_under_the_transaction)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)
    assert not plan.is_blocked

    protected.add(model.left.id)  # locked between the dry run and the application
    with pytest.raises(RestoreBlockedError, match="renamed"):
        await model.baselines.apply_restore(
            baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "renamed"
    assert await events(model) == [AuditAction.BASELINE_CREATED]


async def test_a_concurrent_write_to_a_row_the_plan_touches_is_a_replayable_conflict(
    engine_at_head: AsyncEngine,
) -> None:
    """SERIALIZABLE: a write committed after our snapshot, to a row we then update, is 40001."""
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)

    async def someone_else_writes_meanwhile(session: AsyncSession) -> frozenset[UUID]:
        async with engine_at_head.begin() as other:
            # Static statement, bound value: the concurrent writer of this scenario.
            await other.execute(
                text("UPDATE elements SET description = 'concurrent' WHERE id = :id"),
                {"id": model.left.id},
            )
        return frozenset()

    racing = PostgresBaselineRepository(
        create_session_factory(engine_at_head), protected=someone_else_writes_meanwhile
    )
    with pytest.raises(RestoreConflictError, match="during the restoration"):
        await racing.apply_restore(
            baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "renamed"
    assert await events(model) == [AuditAction.BASELINE_CREATED]
    # The conflict is replayable: read the plan again and it goes through.
    again = await model.baselines.restore_plan(baseline.id, keep_new=False)
    await model.baselines.apply_restore(
        baseline.id, keep_new=False, expected=again, now=LATER, audit=restored
    )
    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "a"
