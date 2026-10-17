"""The baseline use cases against doubled stores."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from ea.core.logging import request_id
from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.baselines import LOGGED_BLOCKED
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import (
    BaselineNotFoundError,
    BaselineScopedError,
    DuplicateBaselineError,
    ElementNotFoundError,
    NotAuthorisedError,
    PackageFrozenError,
    RestoreBlockedError,
    RestoreConflictError,
)
from ea.domain.model import Element, Relationship
from ea.domain.package_versions import FrozenBy
from ea.domain.packages import Package
from ea.services.architecture import ArchitectureService
from ea.services.baselines import BaselineService, RestoreOutcome
from ea.services.caller import acting_as
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemoryBaselines,
    InMemoryDiagrams,
    InMemoryDocuments,
    InMemoryRepository,
    a_reader,
)

pytestmark = pytest.mark.asyncio


async def apply(
    service: BaselineService, baseline_id: UUID, *, keep_new: bool | None = None
) -> RestoreOutcome:
    """What a careful caller does: read the plan, then apply exactly that plan."""
    read = await service.restore(baseline_id, keep_new=keep_new)
    return await service.restore(
        baseline_id, dry_run=False, expected_digest=read.digest, keep_new=keep_new
    )


class TestCapturingAndComparing:
    async def test_a_baseline_of_one_package_says_so(
        self, baseline_service: BaselineService, baseline_store: InMemoryBaselines
    ) -> None:
        assert baseline_store.packages is not None
        finance = Package.create(name="Finance", now=FIXED_NOW)
        await baseline_store.packages.add(finance, audit=lambda p: ())

        made = await baseline_service.create(name="Finance avant refonte", package_id=finance.id)

        assert baseline_store.created_with_scope[made.id] == finance.id
        assert (made.scope_package_id, made.scope_path) == (finance.id, "Finance")

    async def test_a_reader_cannot_capture_a_subtree(
        self, baseline_service: BaselineService
    ) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await baseline_service.create(name="x", package_id=uuid4())

    async def test_a_capture_is_listed_with_its_counts_and_audited(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        await service.create_element(element_type=E.NODE, name="a")
        audit_log.events.clear()

        baseline = await baseline_service.create(name="v1", description="before the move")

        assert baseline.element_count == 1
        assert [b.name for b in await baseline_service.list_baselines()] == ["v1"]
        (event,) = audit_log.events
        assert event.action is AuditAction.BASELINE_CREATED
        assert event.entity_id == baseline.id
        assert event.after is not None and event.after["elements"] == 1
        assert (event.actor_sub, event.at) == ("editor-1", FIXED_NOW)

    async def test_a_taken_name_a_missing_baseline_and_a_reader_are_refused_without_an_event(
        self, baseline_service: BaselineService, audit_log: InMemoryAuditLog
    ) -> None:
        await baseline_service.create(name="v1")
        audit_log.events.clear()

        with pytest.raises(DuplicateBaselineError):
            await baseline_service.create(name="v1")
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.get(uuid4())
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.delete(uuid4())
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await baseline_service.create(name="nope")
        with pytest.raises(ValueError, match="blank"):
            await baseline_service.create(name="  ")

        assert audit_log.events == []

    async def test_a_reader_lists_gets_and_compares(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="v1")

        with acting_as(a_reader()):
            assert [b.id for b in await baseline_service.list_baselines()] == [baseline.id]
            assert (await baseline_service.get(baseline.id)).name == "v1"
            assert (await baseline_service.diff(baseline.id)).is_empty

    async def test_the_diff_against_the_current_model_shows_what_moved(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        kept = await service.create_element(element_type=E.NODE, name="kept")
        baseline = await baseline_service.create(name="v1")
        await service.update_element(kept.id, name="renamed")
        await service.create_element(element_type=E.NODE, name="new")

        diff = await baseline_service.diff(baseline.id)

        assert [e.label for e in diff.elements_added] == ["new"]
        assert [m.label for m in diff.elements_modified] == ["renamed"]

    async def test_two_baselines_compare_with_each_other_and_a_missing_one_is_a_404(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        first = await baseline_service.create(name="v1")
        await service.create_element(element_type=E.NODE, name="later")
        second = await baseline_service.create(name="v2")

        diff = await baseline_service.diff(first.id, against=second.id)

        assert [e.label for e in diff.elements_added] == ["later"]
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.diff(first.id, against=uuid4())

    async def test_deleting_a_baseline_leaves_the_model_alone_and_says_so_in_the_trail(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        kept = await service.create_element(element_type=E.NODE, name="kept")
        baseline = await baseline_service.create(name="v1")
        audit_log.events.clear()

        await baseline_service.delete(baseline.id)

        assert (await service.get_element(kept.id)).name == "kept"
        (event,) = audit_log.events
        assert event.action is AuditAction.BASELINE_DELETED
        assert event.before is not None and event.before["name"] == "v1"


class TestRestoring:
    async def test_a_dry_run_reports_and_writes_nothing(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        before = (dict(baseline_store.graph.elements), list(audit_log.events))

        outcome = await baseline_service.restore(baseline.id)

        assert not outcome.applied
        assert [e.name for e in outcome.plan.elements_to_create] == ["gone"]
        assert (dict(baseline_store.graph.elements), list(audit_log.events)) == before
        with pytest.raises(ElementNotFoundError):
            await service.get_element(gone.id)

    async def test_applying_needs_the_plan_that_was_read_and_writes_nothing_otherwise(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        read = await baseline_service.restore(baseline.id)
        # The model moves after the plan was read: the caller confirmed another plan.
        await service.create_element(element_type=E.NODE, name="late")
        audit_log.events.clear()

        with pytest.raises(ValueError, match="expected_digest"):
            await baseline_service.restore(baseline.id, dry_run=False)
        with pytest.raises(RestoreConflictError, match="read the plan again"):
            await baseline_service.restore(baseline.id, dry_run=False, expected_digest=read.digest)

        assert audit_log.events == []
        with pytest.raises(ElementNotFoundError):
            await service.get_element(gone.id)

    async def test_a_restoration_puts_the_model_back_and_audits_it_as_one_story(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        edited = await service.create_element(element_type=E.NODE, name="billing")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        await service.update_element(edited.id, name="invoicing")
        fresh = await service.create_element(element_type=E.NODE, name="fresh")
        audit_log.events.clear()

        token = request_id.set("req-restore")
        try:
            outcome = await apply(baseline_service, baseline.id)
        finally:
            request_id.reset(token)

        assert outcome.applied
        assert (await service.get_element(gone.id)).name == "gone"
        assert (await service.get_element(edited.id)).name == "billing"
        with pytest.raises(ElementNotFoundError):
            await service.get_element(fresh.id)
        assert [e.action for e in audit_log.events] == [
            AuditAction.BASELINE_RESTORED,
            AuditAction.ELEMENT_CREATED,
            AuditAction.ELEMENT_UPDATED,
            AuditAction.ELEMENT_DELETED,
        ]
        assert {e.correlation for e in audit_log.events} == {f"restore:{baseline.id}"}
        assert {e.request_id for e in audit_log.events} == {"req-restore"}
        summary = audit_log.events[0].after
        assert summary is not None
        assert (summary["elements_created"], summary["elements_deleted"]) == (1, 1)

    async def test_keep_new_keeps_what_was_created_since(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="empty")
        fresh = await service.create_element(element_type=E.NODE, name="fresh")

        await apply(baseline_service, baseline.id, keep_new=True)

        assert (await service.get_element(fresh.id)).name == "fresh"

    async def test_a_conflict_changes_nothing_and_audits_nothing(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        props = {"ip_address": "10.0.0.1", "vrf": "default"}
        host = await service.create_element(element_type=E.NODE, name="h1", properties=props)
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(host.id)
        squatter = await service.create_element(element_type=E.NODE, name="h2", properties=props)
        audit_log.events.clear()

        with pytest.raises(RestoreConflictError, match="h1"):
            await apply(baseline_service, baseline.id, keep_new=True)

        assert audit_log.events == []
        assert (await service.get_element(squatter.id)).name == "h2"

    async def test_a_protected_element_blocks_the_application_but_not_the_dry_run(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
        audit_log: InMemoryAuditLog,
    ) -> None:
        edited = await service.create_element(element_type=E.NODE, name="billing")
        baseline = await baseline_service.create(name="v1")
        await service.update_element(edited.id, name="invoicing")
        baseline_store.protected.add(edited.id)
        audit_log.events.clear()

        plan = (await baseline_service.restore(baseline.id)).plan
        with pytest.raises(RestoreBlockedError, match="invoicing"):
            await apply(baseline_service, baseline.id)

        assert [e.id for e in plan.blocked] == [edited.id]
        assert (await service.get_element(edited.id)).name == "invoicing"
        assert audit_log.events == []

    async def test_a_refusal_leaves_a_bounded_structured_warning_and_no_event(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
        audit_log: InMemoryAuditLog,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        many = [
            await service.create_element(element_type=E.NODE, name=f"n{i}")
            for i in range(LOGGED_BLOCKED + 5)
        ]
        baseline = await baseline_service.create(name="v1")
        for element in many:
            await service.update_element(element.id, name=f"{element.name}-late")
            baseline_store.protected.add(element.id)
        audit_log.events.clear()

        with caplog.at_level("WARNING"), pytest.raises(RestoreBlockedError):
            await apply(baseline_service, baseline.id)

        (record,) = [r for r in caplog.records if getattr(r, "action", "") == "restore_blocked"]
        assert record.levelname == "WARNING"
        assert record.baseline_id == str(baseline.id)  # type: ignore[attr-defined]
        assert record.blocked_count == LOGGED_BLOCKED + 5  # type: ignore[attr-defined]
        assert len(record.blocked) == LOGGED_BLOCKED  # type: ignore[attr-defined]
        assert audit_log.events == []

    async def test_a_reader_may_not_even_ask_for_a_plan(
        self, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="v1")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await baseline_service.restore(baseline.id)

    async def test_an_unknown_baseline_is_a_404(self, baseline_service: BaselineService) -> None:
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.restore(uuid4())

    async def test_two_dry_runs_of_the_same_model_give_the_same_plan(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="v1")
        await service.create_element(element_type=E.NODE, name="late")

        first = (await baseline_service.restore(baseline.id)).plan
        second = (await baseline_service.restore(baseline.id)).plan

        assert first == second


class TestTheTrailSaysEverythingThatWentAndMoved:
    async def test_a_deleted_element_lists_what_went_with_it_exactly_like_a_plain_delete(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        documents: InMemoryDocuments,
        diagrams: InMemoryDiagrams,
        audit_log: InMemoryAuditLog,
    ) -> None:
        kept = await service.create_element(element_type=E.NODE, name="kept")
        baseline = await baseline_service.create(name="empty-ish")
        doomed = await service.create_element(element_type=E.NODE, name="doomed")
        tie = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=kept.id, target_id=doomed.id
        )
        note = Document.create(
            element_id=doomed.id, filename="a.md", content="# A\n", now=FIXED_NOW
        )
        documents.documents[note.id] = note
        view = Diagram.create(name="Vue", now=FIXED_NOW)
        diagrams.diagrams[view.id] = view
        diagrams.nodes[view.id] = (DiagramNode(doomed.id, 1, 2),)
        audit_log.events.clear()

        await apply(baseline_service, baseline.id)

        deleted = next(e for e in audit_log.events if e.action is AuditAction.ELEMENT_DELETED)
        assert deleted.before is not None
        assert deleted.before["cascaded"] == {
            "relationships": {
                "count": 1,
                "items": [{"id": str(tie.id), "relationship_type": "association"}],
            },
            "documents": {"count": 1, "ids": [str(note.id)]},
            "diagrams": {"count": 1, "ids": [str(view.id)]},
        }
        # The same shape as a plain delete, so one reader serves both.
        again = await service.create_element(element_type=E.NODE, name="again")
        audit_log.events.clear()
        await service.delete_element(again.id)
        plain = audit_log.events[0].before
        assert plain is not None and set(plain["cascaded"]) == set(deleted.before["cascaded"])

    async def test_the_boxes_a_restoration_moves_are_in_the_summary_with_their_old_place(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        diagrams: InMemoryDiagrams,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="a")
        view = Diagram.create(name="Vue", now=FIXED_NOW)
        diagrams.diagrams[view.id] = view
        diagrams.nodes[view.id] = (DiagramNode(element.id, 1, 2),)
        baseline = await baseline_service.create(name="v1")
        diagrams.nodes[view.id] = (DiagramNode(element.id, 9, 9),)
        audit_log.events.clear()

        await apply(baseline_service, baseline.id)

        (restored,) = audit_log.events
        assert restored.action is AuditAction.BASELINE_RESTORED
        assert restored.after is not None
        box = {"x": 1, "y": 2, "width": 132.0, "height": 46.0}
        assert restored.after["boxes_restored"] == {
            "count": 1,
            "items": [
                {
                    "diagram_id": str(view.id),
                    "element_id": str(element.id),
                    "before": {**box, "x": 9, "y": 9},
                    "after": box,
                }
            ],
        }


class TestApprovedAndLockedElements:
    async def freeze(
        self, repository: InMemoryRepository, element_id: UUID, *, locked: bool
    ) -> None:
        repository.elements[element_id] = replace(
            repository.elements[element_id],
            approval_state=ApprovalState.APPROVED,
            locked=locked,
            approved_by_sub="approver-1",
            approved_at=FIXED_NOW,
        )

    @pytest.mark.parametrize("locked", [False, True])
    async def test_an_approved_or_locked_element_blocks_the_application_not_the_dry_run(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
        locked: bool,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="billing")
        baseline = await baseline_service.create(name="v1")
        await service.update_element(element.id, name="invoicing")
        await self.freeze(repository, element.id, locked=locked)
        audit_log.events.clear()

        planned = await baseline_service.restore(baseline.id)
        with pytest.raises(RestoreBlockedError, match="invoicing"):
            await apply(baseline_service, baseline.id)

        assert [e.name for e in planned.plan.blocked] == ["invoicing"]
        assert repository.elements[element.id].name == "invoicing"
        assert audit_log.events == []


FINANCE = UUID("00000000-0000-0000-0000-00000000f1a0")
FROZEN = FrozenBy(FINANCE, "Finance", "v1.2.0")


async def _async[T](value: T) -> T:
    return value


async def a_scoped_baseline(
    baseline_service: BaselineService, baseline_store: InMemoryBaselines
) -> tuple[UUID, frozenset[UUID]]:
    """Finance, one element filed in it, and a baseline of that subtree."""
    assert baseline_store.packages is not None
    finance = replace(Package.create(name="Finance", now=FIXED_NOW), id=FINANCE)
    await baseline_store.packages.add(finance, audit=lambda p: ())
    inside = Element.create(element_type=E.NODE, name="ledger", now=FIXED_NOW)
    baseline_store.graph.elements[inside.id] = replace(inside, package_id=FINANCE)
    stored = await baseline_service.create(name="Finance v1", package_id=FINANCE)
    return stored.id, frozenset({inside.id})


@pytest.fixture
def lookups() -> list[str]:
    return []


@pytest.fixture
def frozen_service(baseline_store: InMemoryBaselines, lookups: list[str]) -> BaselineService:
    """The same store, behind a `FreezesLookup` that says Finance is published."""

    async def freezes() -> dict[UUID, FrozenBy]:
        lookups.append("freezes")
        return {FINANCE: FROZEN}

    return BaselineService(baseline_store, freezes=freezes, clock=lambda: FIXED_NOW)


class TestASubtreeBaselineIsComparedInItsScope:
    async def test_diff_against_the_model_reads_the_scope_and_what_the_baseline_knows(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
    ) -> None:
        scoped_id, known_ids = await a_scoped_baseline(baseline_service, baseline_store)

        assert (await baseline_service.diff(scoped_id)).is_empty
        assert baseline_store.current_calls[-1] == (FINANCE, known_ids)

    async def test_two_baselines_of_different_scopes_are_not_compared(
        self, baseline_service: BaselineService, baseline_store: InMemoryBaselines
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        ordinary = await baseline_service.create(name="whole")

        with pytest.raises(BaselineScopedError):
            await baseline_service.diff(ordinary.id, against=scoped_id)
        with pytest.raises(BaselineScopedError):
            await baseline_service.diff(scoped_id, against=ordinary.id)

    async def test_a_scoped_baseline_restores_as_a_dry_run_by_default(
        self, baseline_service: BaselineService, baseline_store: InMemoryBaselines
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        outcome = await baseline_service.restore(scoped_id)

        assert outcome.applied is False and outcome.digest
        assert outcome.plan.frozen_by is None and not outcome.plan.is_blocked

    async def test_it_deletes_what_appeared_inside_and_nothing_outside(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        inside = Element.create(element_type=E.NODE, name="new inside", now=FIXED_NOW)
        outside = Element.create(element_type=E.NODE, name="new at the root", now=FIXED_NOW)
        baseline_store.graph.elements[inside.id] = replace(inside, package_id=FINANCE)
        baseline_store.graph.elements[outside.id] = outside

        outcome = await apply(baseline_service, scoped_id)

        assert [e.id for e in outcome.plan.elements_to_delete] == [inside.id]
        assert outside.id in baseline_store.graph.elements
        assert inside.id not in baseline_store.graph.elements

    async def test_the_dry_run_is_logged_with_ids_and_counts_never_names(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        inside = Element.create(element_type=E.NODE, name="secret name", now=FIXED_NOW)
        baseline_store.graph.elements[inside.id] = replace(inside, package_id=FINANCE)

        with caplog.at_level(logging.INFO):
            await baseline_service.restore(scoped_id)

        (line,) = [
            r for r in caplog.records if getattr(r, "action", "") == "baseline_restore_preview"
        ]
        assert (line.baseline_id, line.elements_to_delete) == (str(scoped_id), 1)  # type: ignore[attr-defined]
        assert line.is_blocked is False  # type: ignore[attr-defined]
        assert "secret name" not in caplog.text

    async def test_an_element_older_than_the_capture_moved_in_since_is_kept(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        crm = Element.create(element_type=E.NODE, name="CRM", now=FIXED_NOW - timedelta(days=600))
        baseline_store.graph.elements[crm.id] = replace(crm, package_id=FINANCE)  # moved in

        outcome = await apply(baseline_service, scoped_id)

        assert outcome.plan.elements_to_delete == ()
        assert [e.id for e in outcome.plan.kept_moved_in] == [crm.id]
        assert crm.id in baseline_store.graph.elements

    async def test_a_protected_element_outside_blocks_a_deletion_that_cascades_to_it(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        inside = replace(
            Element.create(element_type=E.NODE, name="new inside", now=FIXED_NOW),
            package_id=FINANCE,
        )
        outside = Element.create(element_type=E.NODE, name="approved outside", now=FIXED_NOW)
        link = Relationship.between(R.ASSOCIATION, inside, outside, now=FIXED_NOW)
        baseline_store.graph.elements.update({inside.id: inside, outside.id: outside})
        baseline_store.graph.relationships[link.id] = link
        baseline_store.protected.add(outside.id)

        outcome = await baseline_service.restore(scoped_id)

        assert [e.id for e in outcome.plan.blocked] == [outside.id]
        assert outcome.plan.cascaded_relationships == 1

    async def test_a_baseline_whose_package_is_gone_is_diffed_but_not_restored(
        self,
        baseline_service: BaselineService,
        baseline_store: InMemoryBaselines,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(baseline_service, baseline_store)
        assert baseline_store.packages is not None
        del baseline_store.packages.packages[FINANCE]

        await baseline_service.diff(scoped_id)
        with pytest.raises(BaselineScopedError):
            await baseline_service.restore(scoped_id)


class TestAFrozenSubtreeIsPredictedNotOnlyRefused:
    async def test_the_dry_run_says_the_plan_is_blocked_and_by_what(
        self, frozen_service: BaselineService, baseline_store: InMemoryBaselines
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(frozen_service, baseline_store)
        outcome = await frozen_service.restore(scoped_id)

        assert outcome.plan.frozen_by == FROZEN
        assert outcome.plan.is_blocked and outcome.applied is False

    async def test_applying_is_refused_before_the_repository_is_asked_to_write(
        self,
        frozen_service: BaselineService,
        baseline_store: InMemoryBaselines,
        audit_log: InMemoryAuditLog,
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(frozen_service, baseline_store)
        digest = (await frozen_service.restore(scoped_id)).digest
        audit_log.events.clear()

        with pytest.raises(PackageFrozenError) as raised:
            await frozen_service.restore(scoped_id, dry_run=False, expected_digest=digest)

        assert baseline_store.applied == []
        assert audit_log.events == []
        assert "Finance" not in str(raised.value) and "v1.2.0" in str(raised.value)
        assert str(FINANCE) in str(raised.value)

    async def test_the_freeze_is_checked_before_the_digest(
        self, frozen_service: BaselineService, baseline_store: InMemoryBaselines
    ) -> None:
        scoped_id, _ = await a_scoped_baseline(frozen_service, baseline_store)
        with pytest.raises(PackageFrozenError):
            await frozen_service.restore(scoped_id, dry_run=False, expected_digest="stale")
        assert baseline_store.applied == []

    async def test_the_freeze_is_read_again_when_applying(
        self, baseline_store: InMemoryBaselines
    ) -> None:
        answers = iter([{}, {FINANCE: FROZEN}])  # published between the two calls
        service = BaselineService(baseline_store, freezes=lambda: _async(next(answers)))
        scoped_id, _ = await a_scoped_baseline(service, baseline_store)

        digest = (await service.restore(scoped_id)).digest
        with pytest.raises(PackageFrozenError):
            await service.restore(scoped_id, dry_run=False, expected_digest=digest)
        assert baseline_store.applied == []

    async def test_a_whole_model_baseline_never_asks_for_a_freeze(
        self, frozen_service: BaselineService, lookups: list[str]
    ) -> None:
        ordinary = await frozen_service.create(name="whole")

        assert (await frozen_service.restore(ordinary.id)).plan.frozen_by is None
        assert lookups == []


BUDGET = UUID("00000000-0000-0000-0000-00000000b0d6")
OTHER = UUID("00000000-0000-0000-0000-0000000007e4")


class TestEveryPackageThePlanWritesIsChecked:
    """Not only the scope: a nested publication, or a frozen package outside it."""

    async def test_a_published_child_inside_the_subtree_blocks_the_plan_that_writes_it(
        self, baseline_store: InMemoryBaselines
    ) -> None:
        child = FrozenBy(BUDGET, "Finance / Budget", "v1.0.0")
        service = BaselineService(
            baseline_store, freezes=lambda: _async({BUDGET: child}), clock=lambda: FIXED_NOW
        )
        assert baseline_store.packages is not None
        finance = replace(Package.create(name="Finance", now=FIXED_NOW), id=FINANCE)
        budget = replace(Package.create(name="Budget", now=FIXED_NOW), id=BUDGET, parent_id=FINANCE)
        await baseline_store.packages.add(finance, audit=lambda p: ())
        await baseline_store.packages.add(budget, audit=lambda p: ())
        planned = replace(
            Element.create(element_type=E.NODE, name="plan", now=FIXED_NOW), package_id=BUDGET
        )
        baseline_store.graph.elements[planned.id] = planned
        scoped_id = (await service.create(name="Finance v1", package_id=FINANCE)).id
        baseline_store.graph.elements[planned.id] = replace(planned, name="renamed")

        outcome = await service.restore(scoped_id)
        assert outcome.plan.frozen_by == child and outcome.plan.is_blocked
        with pytest.raises(PackageFrozenError):
            await service.restore(scoped_id, dry_run=False, expected_digest=outcome.digest)
        assert baseline_store.applied == []

    async def test_a_cascade_to_an_element_in_a_frozen_package_outside_is_predicted(
        self, baseline_store: InMemoryBaselines
    ) -> None:
        other = FrozenBy(OTHER, "Other", "v2.0.0")
        service = BaselineService(
            baseline_store, freezes=lambda: _async({OTHER: other}), clock=lambda: FIXED_NOW
        )
        scoped_id, _ = await a_scoped_baseline(service, baseline_store)
        assert baseline_store.packages is not None
        await baseline_store.packages.add(
            replace(Package.create(name="Other", now=FIXED_NOW), id=OTHER), audit=lambda p: ()
        )
        inside = replace(
            Element.create(element_type=E.NODE, name="new inside", now=FIXED_NOW),
            package_id=FINANCE,
        )
        outside = replace(
            Element.create(element_type=E.NODE, name="frozen outside", now=FIXED_NOW),
            package_id=OTHER,
        )
        crossing = Relationship.between(R.ASSOCIATION, inside, outside, now=FIXED_NOW)
        baseline_store.graph.elements.update({inside.id: inside, outside.id: outside})
        baseline_store.graph.relationships[crossing.id] = crossing

        outcome = await service.restore(scoped_id)

        assert outcome.plan.frozen_by == other and outcome.plan.is_blocked

    async def test_an_untouched_frozen_package_does_not_block(
        self, baseline_store: InMemoryBaselines
    ) -> None:
        service = BaselineService(
            baseline_store,
            freezes=lambda: _async({OTHER: FrozenBy(OTHER, "Other", "v2.0.0")}),
            clock=lambda: FIXED_NOW,
        )
        scoped_id, _ = await a_scoped_baseline(service, baseline_store)

        assert (await service.restore(scoped_id)).plan.frozen_by is None
