"""Restoring a baseline against a real PostgreSQL: all of it, or none of it (docs/adr/0047)."""

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
from ea.domain.diagram_objects import DiagramScene
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
    await model.diagrams.replace_layout(
        model.diagram.id, DiagramScene(), revision=1, now=LATER, audit=()
    )
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
    # The boxes moved under the editors' feet: their copy is stale, so their next save is a 409.
    diagram = await model.diagrams.get(model.diagram.id)
    assert diagram is not None and diagram.revision == 3


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


class TestRestoringOverUniqueness:
    """A restoration is judged by its end state: a name or an address that is free *after*
    it must not be refused because another element still holds it *during* it."""

    async def three(self, model: Model, names: tuple[str, str, str]) -> list[Element]:
        return [
            await model.graph.add_element(
                Element.create(element_type=E.NODE, name=name, now=NOW), audit=()
            )
            for name in names
        ]

    async def rename(self, model: Model, element: Element, name: str) -> None:
        await model.graph.update_element(element.id, lambda e: e.rename(name, now=LATER), audit=())

    async def names(self, model: Model, *elements: Element) -> list[str]:
        found = [await model.graph.get_element(e.id) for e in elements]
        return [e.name for e in found if e is not None]

    async def test_a_chain_of_renames_that_each_take_the_next_ones_old_name(
        self, engine_at_head: AsyncEngine
    ) -> None:
        model = Model(engine_at_head)
        elements = await self.three(model, ("Beta", "Gamma", "Other"))
        a, b = elements[0], elements[1]
        baseline = await model.baselines.create(a_baseline(), audit=created)
        await self.rename(model, b, "Beta2")  # free "Beta" for A's new name below
        await self.rename(model, a, "Alpha")
        await self.rename(model, b, "Beta")

        await restore(model, baseline.id)

        assert await self.names(model, a, b) == ["Beta", "Gamma"]

    async def test_a_pure_swap_and_a_cycle_of_three(self, engine_at_head: AsyncEngine) -> None:
        model = Model(engine_at_head)
        a, b, c = await self.three(model, ("a", "b", "c"))
        baseline = await model.baselines.create(a_baseline(), audit=created)
        # b -> a, c -> b, a -> c: every name is taken by the next one.
        await self.rename(model, a, "tmp")
        await self.rename(model, b, "a")
        await self.rename(model, c, "b")
        await self.rename(model, a, "c")

        await restore(model, baseline.id)

        assert await self.names(model, a, b, c) == ["a", "b", "c"]

    async def test_two_hosts_swap_their_addresses(self, engine_at_head: AsyncEngine) -> None:
        model = Model(engine_at_head)
        one = {"vrf": "default", "ip_address": "10.0.0.1"}
        two = {"vrf": "default", "ip_address": "10.0.0.2"}
        a = await model.graph.add_element(
            Element.create(element_type=E.NODE, name="h1", now=NOW, properties=one), audit=()
        )
        b = await model.graph.add_element(
            Element.create(element_type=E.NODE, name="h2", now=NOW, properties=two), audit=()
        )
        baseline = await model.baselines.create(a_baseline(), audit=created)
        spare = {"vrf": "default", "ip_address": "10.0.0.9"}
        for element, props in ((a, spare), (b, one), (a, two)):
            await model.graph.update_element(
                element.id, lambda e, p=props: e.with_properties(p, now=LATER), audit=()
            )

        await restore(model, baseline.id)

        found = [await model.graph.get_element(e.id) for e in (a, b)]
        assert [e.properties["ip_address"] for e in found if e] == ["10.0.0.1", "10.0.0.2"]

    async def test_two_networks_swap_their_prefixes(self, engine_at_head: AsyncEngine) -> None:
        model = Model(engine_at_head)
        nets = []
        for name, cidr in (("n1", "10.1.0.0/24"), ("n2", "10.2.0.0/24")):
            nets.append(
                await model.graph.add_element(
                    Element.create(
                        element_type=E.COMMUNICATION_NETWORK,
                        name=name,
                        now=NOW,
                        properties={"vrf": "default", "cidr": cidr},
                    ),
                    audit=(),
                )
            )
        baseline = await model.baselines.create(a_baseline(), audit=created)
        for element, cidr in (
            (nets[0], "10.9.0.0/24"),
            (nets[1], "10.1.0.0/24"),
            (nets[0], "10.2.0.0/24"),
        ):
            await model.graph.update_element(
                element.id,
                lambda e, c=cidr: e.with_properties({"vrf": "default", "cidr": c}, now=LATER),
                audit=(),
            )

        await restore(model, baseline.id)

        found = [await model.graph.get_element(e.id) for e in nets]
        assert [e.properties["cidr"] for e in found if e] == ["10.1.0.0/24", "10.2.0.0/24"]

    async def test_a_real_clash_says_that_replaying_the_same_plan_fails_again(
        self, engine_at_head: AsyncEngine
    ) -> None:
        model = Model(engine_at_head)
        await model.seed()
        baseline = await model.baselines.create(a_baseline(), audit=created)
        await model.graph.delete_element(model.right.id, audit=())
        await model.graph.add_element(
            Element.create(element_type=E.NODE, name="b", now=LATER), audit=()
        )
        plan = await model.baselines.restore_plan(baseline.id, keep_new=True)

        with pytest.raises(RestoreConflictError, match="same way"):
            await model.baselines.apply_restore(
                baseline.id, keep_new=True, expected=plan, now=LATER, audit=restored
            )


async def test_the_journal_of_a_restoration_lets_one_rebuild_everything_that_disappeared(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.audit import EntityKind
    from ea.services.baselines import BaselineService

    model = Model(engine_at_head)
    empty = await model.baselines.create(a_baseline("empty"), audit=created)
    await model.seed()
    (note,) = await model.documents.list_for_element(model.left.id)
    service = BaselineService(model.baselines, clock=lambda: LATER)

    read = await service.restore(empty.id)
    await service.restore(empty.id, dry_run=False, expected_digest=read.digest)

    page = await model.trail.page(
        AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=model.left.id)
    )
    deleted = next(e for e in page.events if e.action is AuditAction.ELEMENT_DELETED)
    assert deleted.before is not None
    assert deleted.before["cascaded"] == {
        "relationships": {
            "count": 1,
            "items": [{"id": str(model.link.id), "relationship_type": "association"}],
        },
        "documents": {"count": 1, "ids": [str(note.id)]},
        "diagrams": {"count": 1, "ids": [str(model.diagram.id)]},
    }
    assert deleted.correlation == f"restore:{empty.id}"


# --- Review and locking (docs/adr/0048) ------------------------------------


async def freeze(engine: AsyncEngine, element_id: UUID, *, locked: bool = False) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE elements SET approval_state = 'approved', locked = :locked, "
                "approved_by_sub = 'approver-1', approved_at = now() WHERE id = :id"
            ),
            {"id": element_id, "locked": locked},
        )


async def test_the_real_repository_protects_approved_and_locked_elements_out_of_the_box(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )

    for locked in (False, True):
        await freeze(engine_at_head, model.left.id, locked=locked)
        plan = await model.baselines.restore_plan(baseline.id, keep_new=False)
        assert [e.id for e in plan.blocked] == [model.left.id], locked
        with pytest.raises(RestoreBlockedError, match="renamed"):
            await model.baselines.apply_restore(
                baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
            )

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "renamed"
    assert await events(model) == [AuditAction.BASELINE_CREATED]  # a refusal leaves no event


async def test_a_lock_put_after_the_dry_run_is_read_inside_the_transaction_and_stops_it(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    plan = await model.baselines.restore_plan(baseline.id, keep_new=False)
    assert not plan.is_blocked

    await freeze(engine_at_head, model.left.id, locked=True)  # after the dry run
    with pytest.raises(RestoreBlockedError):
        await model.baselines.apply_restore(
            baseline.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "renamed"


async def test_deleting_an_approved_element_by_restoration_is_blocked_too(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    empty = await model.baselines.create(a_baseline("empty"), audit=created)
    await model.seed()
    await freeze(engine_at_head, model.right.id)

    plan = await model.baselines.restore_plan(empty.id, keep_new=False)

    assert model.right.id in {e.id for e in plan.blocked}
    with pytest.raises(RestoreBlockedError):
        await model.baselines.apply_restore(
            empty.id, keep_new=False, expected=plan, now=LATER, audit=restored
        )
    assert await model.graph.get_element(model.right.id) is not None


async def test_an_element_in_review_is_restored_and_stays_in_review(
    engine_at_head: AsyncEngine,
) -> None:
    model = Model(engine_at_head)
    await model.seed()
    baseline = await model.baselines.create(a_baseline(), audit=created)
    await model.graph.update_element(
        model.left.id, lambda e: e.rename("renamed", now=LATER), audit=()
    )
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET approval_state = 'in_review' WHERE id = :id"),
            {"id": model.left.id},
        )
    before = await model.graph.get_element(model.left.id)
    assert before is not None

    await restore(model, baseline.id)

    left = await model.graph.get_element(model.left.id)
    assert left is not None and left.name == "a"
    assert left.approval_state.value == "in_review"
    assert left.revision == before.revision + 1  # a restoration is a change of content
