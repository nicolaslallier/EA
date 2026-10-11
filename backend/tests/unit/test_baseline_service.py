"""The baseline use cases against doubled stores."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from ea.core.logging import request_id
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.baselines import LOGGED_BLOCKED
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import (
    BaselineNotFoundError,
    DuplicateBaselineError,
    ElementNotFoundError,
    NotAuthorisedError,
    RestoreBlockedError,
    RestoreConflictError,
)
from ea.services.architecture import ArchitectureService
from ea.services.baselines import BaselineService, RestoreOutcome
from ea.services.caller import acting_as
from tests.conftest import (
    FIXED_NOW,
    InMemoryAuditLog,
    InMemoryBaselines,
    InMemoryDiagrams,
    InMemoryDocuments,
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
