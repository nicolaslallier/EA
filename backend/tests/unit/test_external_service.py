"""The use cases over external references — rules, refusals, and who may do what.

Providers are `FakeExternalSystem`s: nothing here reaches a network, and `system.calls` is how
a test proves a request was — or was not — made (docs/adr/0062).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from ea.domain.archimate import ElementType
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    DuplicateExternalReferenceError,
    ElementNotFoundError,
    ExternalBusyError,
    ExternalReferenceNotFoundError,
    ExternalRefusedError,
    ExternalUnavailableError,
    ExternalWriteFailedError,
    InvalidExternalReferenceError,
    NotAuthenticatedError,
    NotAuthorisedError,
    UnknownExternalTargetError,
)
from ea.domain.external import (
    MAX_REFRESH_BATCH,
    ExternalKind,
    ExternalReference,
    ExternalSnapshot,
    ExternalStatus,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.external import ExternalReferenceService
from tests.conftest import (
    FIXED_NOW,
    FakeExternalSystem,
    InMemoryAuditLog,
    InMemoryExternalReferences,
    a_reader,
)

pytestmark = pytest.mark.asyncio

GITHUB = ExternalKind.GITHUB_ISSUE


async def an_element(service: ArchitectureService) -> UUID:
    return (
        await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name="Billing")
    ).id


async def cite(
    store: InMemoryExternalReferences, element_id: UUID, number: int, *, age_minutes: int = 0
) -> ExternalReference:
    when = FIXED_NOW - timedelta(minutes=age_minutes)
    return await store.add(
        ExternalReference.create(
            element_id=element_id,
            kind=GITHUB,
            target="main",
            external_id=f"acme/billing#{number}",
            url=f"https://github.test/acme/billing/issues/{number}",
            snapshot=ExternalSnapshot.of("old title", ExternalStatus.OPEN),
            created_by="editor",
            now=when,
        ),
        audit=(),
    )


class TestLinking:
    async def test_a_github_issue_is_resolved_stored_and_its_address_built_by_the_server(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)

        view = await external_service.link(
            element, kind=GITHUB, target="main", reference="Acme/Billing#12"
        )

        reference = view.reference
        assert (reference.external_id, reference.url, reference.target) == (
            "acme/billing#12",
            "https://github.test/acme/billing/issues/12",
            "main",
        )
        assert (reference.title, reference.status) == ("Fix invoice rounding", ExternalStatus.OPEN)
        assert (reference.created_by, reference.synced_at) == ("editor", FIXED_NOW)
        assert view.stale is False
        assert external_system.calls == ["acme/billing#12"]
        (event,) = [
            e for e in audit_log.events if e.action is AuditAction.EXTERNAL_REFERENCE_LINKED
        ]
        assert event.after is not None
        assert "Fix invoice rounding" not in str(event.after)  # a title is a third party's text
        assert event.after["title_length"] == len("Fix invoice rounding")

    async def test_the_target_may_be_left_out_when_one_target_serves_the_kind(
        self, external_service: ExternalReferenceService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        view = await external_service.link(element, kind=GITHUB, reference="acme/billing#12")

        assert view.reference.target == "main"

    async def test_an_undeclared_target_or_one_of_another_kind_is_refused_alike(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        for target in ("nope", "main-but-longer", ""):
            with pytest.raises(UnknownExternalTargetError):
                await external_service.link(
                    element, kind=GITHUB, target=target or "x", reference="acme/billing#12"
                )
        with pytest.raises(UnknownExternalTargetError):
            await external_service.link(
                element, kind=ExternalKind.JIRA_ISSUE, target="main", reference="PROJ-1"
            )

        assert external_system.calls == []

    async def test_no_target_is_inferred_when_two_could_serve(
        self,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
    ) -> None:
        two = ExternalReferenceService(
            external_references,
            service,
            {"a": FakeExternalSystem("a"), "b": FakeExternalSystem("b")},
            clock=lambda: FIXED_NOW,
        )
        element = await an_element(service)

        with pytest.raises(UnknownExternalTargetError, match="a, b"):
            await two.link(element, kind=GITHUB, reference="acme/billing#12")

    async def test_an_address_on_another_host_never_triggers_an_outbound_call(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        with pytest.raises(InvalidExternalReferenceError):
            await external_service.link(
                element, kind=GITHUB, reference="https://evil.test/acme/billing/issues/12"
            )

        assert external_system.calls == []
        assert await external_service.list_for_element(element) == ()

    async def test_a_free_link_is_stored_as_written_never_resolved_and_has_no_target(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        view = await external_service.link(
            element, kind=ExternalKind.URL, reference="https://wiki.test/runbook"
        )

        assert view.reference.url == "https://wiki.test/runbook"
        assert (view.reference.status, view.reference.synced_at) == (ExternalStatus.UNKNOWN, None)
        assert (view.reference.target, view.stale) == ("", False)
        assert external_system.calls == []
        with pytest.raises(InvalidExternalReferenceError):
            await external_service.link(
                element, kind=ExternalKind.URL, target="main", reference="https://wiki.test/x"
            )

    async def test_the_same_issue_in_any_spelling_is_a_duplicate_and_costs_one_request(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        await external_service.link(element, kind=GITHUB, reference="acme/billing#12")

        for again in (
            "ACME/Billing#12",
            " acme/billing#12 ",
            "https://github.test/acme/billing/issues/12",
        ):
            with pytest.raises(DuplicateExternalReferenceError):
                await external_service.link(element, kind=GITHUB, reference=again)

        assert external_system.calls == ["acme/billing#12"]
        assert len(await external_service.list_for_element(element)) == 1

    async def test_two_elements_may_cite_the_same_issue(
        self, external_service: ExternalReferenceService, service: ArchitectureService
    ) -> None:
        first, second = await an_element(service), await an_element(service)

        await external_service.link(first, kind=GITHUB, reference="acme/billing#12")
        await external_service.link(second, kind=GITHUB, reference="acme/billing#12")

    async def test_an_unreachable_provider_creates_nothing_and_leaves_no_event(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)
        before = len(audit_log.events)
        external_system.failure = ExternalUnavailableError("down")

        with pytest.raises(ExternalUnavailableError):
            await external_service.link(element, kind=GITHUB, reference="acme/billing#12")

        assert await external_service.list_for_element(element) == ()
        assert len(audit_log.events) == before

    async def test_an_issue_the_provider_does_not_know_creates_nothing(
        self, external_service: ExternalReferenceService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(ExternalRefusedError):
            await external_service.link(element, kind=GITHUB, reference="acme/billing#999")

        assert await external_service.list_for_element(element) == ()

    async def test_a_kind_with_no_active_target_is_a_refusal_not_a_crash(
        self, external_references: InMemoryExternalReferences, service: ArchitectureService
    ) -> None:
        bare = ExternalReferenceService(external_references, service, {}, clock=lambda: FIXED_NOW)
        element = await an_element(service)

        with pytest.raises(UnknownExternalTargetError):
            await bare.link(element, kind=GITHUB, reference="acme/billing#12")

    async def test_an_unknown_element_is_not_found(
        self, external_service: ExternalReferenceService
    ) -> None:
        with pytest.raises(ElementNotFoundError):
            await external_service.link(uuid4(), kind=GITHUB, reference="acme/billing#12")
        with pytest.raises(ElementNotFoundError):
            await external_service.list_for_element(uuid4())


class TestListing:
    async def test_listing_makes_no_outbound_call_and_flags_what_is_old(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        await cite(external_references, element, 1, age_minutes=60)
        await cite(external_references, element, 2, age_minutes=60 * 25)

        views = await external_service.list_for_element(element)

        assert sorted((v.reference.external_id, v.stale) for v in views) == [
            ("acme/billing#1", False),
            ("acme/billing#2", True),
        ]
        assert external_system.calls == []

    async def test_a_reader_may_list_and_see_the_targets_without_their_addresses(
        self,
        external_service: ExternalReferenceService,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        with acting_as(a_reader()):
            assert await external_service.list_for_element(element) == ()
            targets = await external_service.targets()

        assert [(t.name, t.kind, t.active) for t in targets] == [
            ("main", GITHUB, True),
            ("offline", ExternalKind.JIRA_ISSUE, False),
        ]
        assert all(not hasattr(t, "api_url") and not hasattr(t, "web_url") for t in targets)

    async def test_the_stale_ones_across_the_catalogue_come_oldest_first_and_bounded(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        await cite(external_references, element, 1, age_minutes=60 * 30)
        await cite(external_references, element, 2, age_minutes=60 * 50)
        await cite(external_references, element, 3, age_minutes=5)

        views = await external_service.list_stale(limit=10)

        assert [v.reference.external_id for v in views] == ["acme/billing#2", "acme/billing#1"]
        assert all(v.stale for v in views)
        assert len(await external_service.list_stale(limit=1)) == 1
        with pytest.raises(ValueError, match="limit"):
            await external_service.list_stale(limit=0)
        with pytest.raises(ValueError, match="limit"):
            await external_service.list_stale(limit=10_000)


class TestRefreshing:
    async def test_a_refresh_updates_the_cache_and_audits_only_a_change(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)
        stale = await cite(external_references, element, 12, age_minutes=60 * 30)
        external_system.snapshots["acme/billing#12"] = ExternalSnapshot.of(
            "Fix rounding", ExternalStatus.CLOSED
        )

        view = await external_service.refresh(stale.id)

        assert (view.reference.title, view.reference.status) == (
            "Fix rounding",
            ExternalStatus.CLOSED,
        )
        assert view.reference.synced_at == FIXED_NOW
        assert view.stale is False
        refreshed = [
            e for e in audit_log.events if e.action is AuditAction.EXTERNAL_REFERENCE_REFRESHED
        ]
        assert len(refreshed) == 1

        await external_service.refresh(stale.id)  # nothing changed: nothing to say

        assert (
            len(
                [
                    e
                    for e in audit_log.events
                    if e.action is AuditAction.EXTERNAL_REFERENCE_REFRESHED
                ]
            )
            == 1
        )

    async def test_an_unreachable_provider_returns_the_cached_line_marked_stale(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        recent = await cite(external_references, element, 12, age_minutes=5)
        external_system.failure = ExternalUnavailableError("down")

        view = await external_service.refresh(recent.id)

        assert view.stale is True
        assert view.reference == recent

    async def test_an_object_that_is_gone_becomes_unknown_and_keeps_its_title(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        gone = await cite(external_references, element, 404)

        view = await external_service.refresh(gone.id)

        assert (view.reference.title, view.reference.status) == (
            "old title",
            ExternalStatus.UNKNOWN,
        )
        assert await external_references.get(gone.id) is not None  # never deleted automatically

    async def test_a_target_removed_from_the_configuration_is_unavailable_not_an_error(
        self,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
    ) -> None:
        bare = ExternalReferenceService(external_references, service, {}, clock=lambda: FIXED_NOW)
        element = await an_element(service)
        row = await cite(external_references, element, 12, age_minutes=60 * 30)

        view = await bare.refresh(row.id)

        assert view.stale is True

    async def test_a_free_link_has_nothing_to_refresh(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        link = await external_service.link(
            element, kind=ExternalKind.URL, reference="https://wiki.test/a"
        )

        view = await external_service.refresh(link.reference.id)

        assert view.reference == link.reference
        assert external_system.calls == []

    async def test_refreshing_an_element_takes_the_oldest_first_up_to_the_batch(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        for number in range(1, MAX_REFRESH_BATCH + 6):
            external_system.snapshots[f"acme/billing#{number}"] = ExternalSnapshot.of(
                "new", ExternalStatus.OPEN
            )
            await cite(external_references, element, number, age_minutes=number)

        views = await external_service.refresh_element(element)

        assert len(external_system.calls) == MAX_REFRESH_BATCH
        assert external_system.calls[0] == f"acme/billing#{MAX_REFRESH_BATCH + 5}"  # the oldest
        assert len(views) == MAX_REFRESH_BATCH + 5

    async def test_refreshing_an_element_stops_asking_a_target_that_is_down(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        for number in (1, 2, 3):
            await cite(external_references, element, number, age_minutes=number)
        external_system.failure = ExternalUnavailableError("down")

        views = await external_service.refresh_element(element)

        assert len(external_system.calls) == 1
        assert all(v.stale for v in views)

    async def test_a_missing_reference_is_not_found(
        self, external_service: ExternalReferenceService
    ) -> None:
        with pytest.raises(ExternalReferenceNotFoundError):
            await external_service.refresh(uuid4())
        with pytest.raises(ElementNotFoundError):
            await external_service.refresh_element(uuid4())


class TestUnlinking:
    async def test_it_removes_the_link_and_says_so_in_the_trail(
        self,
        external_service: ExternalReferenceService,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)
        view = await external_service.link(element, kind=GITHUB, reference="acme/billing#12")

        await external_service.unlink(view.reference.id)

        assert await external_service.list_for_element(element) == ()
        assert audit_log.events[-1].action is AuditAction.EXTERNAL_REFERENCE_UNLINKED
        with pytest.raises(ExternalReferenceNotFoundError):
            await external_service.unlink(view.reference.id)


class TestWhoMayDoWhat:
    async def test_a_reader_cannot_link_unlink_or_refresh(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        row = await cite(external_references, element, 12)

        with acting_as(a_reader()):
            with pytest.raises(NotAuthorisedError):
                await external_service.link(element, kind=GITHUB, reference="acme/billing#13")
            with pytest.raises(NotAuthorisedError):
                await external_service.unlink(row.id)
            with pytest.raises(NotAuthorisedError):
                await external_service.refresh(row.id)
            with pytest.raises(NotAuthorisedError):
                await external_service.refresh_element(element)
            with pytest.raises(NotAuthorisedError):
                await external_service.open_issue(
                    element, target="main", scope="acme/billing", title="t", body="", dry_run=False
                )

        assert external_system.calls == []

    @pytest.mark.usefixtures("nobody_calling")
    async def test_nobody_is_not_authenticated(
        self, external_service: ExternalReferenceService
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await external_service.targets()


class TestAdmission:
    async def test_a_caller_cannot_hold_more_places_than_the_ceiling_allows(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        gate = asyncio.Event()
        external_system.gate = gate
        first = asyncio.create_task(
            external_service.link(element, kind=GITHUB, reference="acme/billing#12")
        )
        await asyncio.sleep(0)  # the first call is now waiting on the provider, holding a place

        with pytest.raises(ExternalBusyError):
            await external_service.link(element, kind=GITHUB, reference="acme/billing#13")

        gate.set()
        await first
        # The place is given back however the block ends: a refusal then a success is fine.
        await external_service.link(element, kind=GITHUB, reference="acme/billing#13")


class TestOpeningAnIssue:
    async def test_a_dry_run_shows_what_would_be_sent_and_sends_nothing(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)
        before = len(audit_log.events)

        plan = await external_service.open_issue(
            element, target="main", scope="Acme/Billing", title="Rounding is wrong", body="why"
        )

        assert plan.dry_run is True
        assert (plan.title, plan.body, plan.scope) == ("Rounding is wrong", "why", "acme/billing")
        assert plan.view is None
        assert external_system.created == []
        assert len(audit_log.events) == before

    async def test_for_real_it_opens_the_ticket_links_it_and_audits_without_the_text(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await an_element(service)

        done = await external_service.open_issue(
            element,
            target="main",
            scope="acme/billing",
            title="Rounding is wrong",
            body="secret details",
            dry_run=False,
        )

        assert external_system.created == [("acme/billing", "Rounding is wrong", "secret details")]
        assert done.view is not None
        assert done.view.reference.external_id == "acme/billing#101"
        linked = [e for e in audit_log.events if e.action is AuditAction.EXTERNAL_REFERENCE_LINKED]
        assert len(linked) == 1
        assert "secret details" not in str(linked[0].after)
        assert "Rounding is wrong" not in str(linked[0].after)

    async def test_a_target_that_is_not_writable_is_refused_even_for_a_dry_run(
        self,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
    ) -> None:
        readonly = FakeExternalSystem("main", writable=False)
        svc = ExternalReferenceService(
            external_references, service, {"main": readonly}, clock=lambda: FIXED_NOW
        )
        element = await an_element(service)

        with pytest.raises(UnknownExternalTargetError, match="writable"):
            await svc.open_issue(element, target="main", scope="acme/billing", title="t", body="")

    async def test_a_failed_write_is_not_retried_and_leaves_no_link(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        external_system.write_failure = ExternalWriteFailedError("refused")

        with pytest.raises(ExternalWriteFailedError):
            await external_service.open_issue(
                element, target="main", scope="acme/billing", title="t", body="", dry_run=False
            )

        assert external_system.create_attempts == 1
        assert await external_service.list_for_element(element) == ()

    async def test_a_ticket_that_cannot_be_linked_afterwards_is_reported_not_hidden(
        self,
        external_service: ExternalReferenceService,
        external_references: InMemoryExternalReferences,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)
        await cite(external_references, element, 101)  # the number the provider will return

        with pytest.raises(DuplicateExternalReferenceError):
            await external_service.open_issue(
                element, target="main", scope="acme/billing", title="t", body="", dry_run=False
            )

        assert external_system.created  # the ticket exists: the error is the caller's cue

    async def test_the_draft_is_bounded_before_anything_else(
        self, external_service: ExternalReferenceService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(InvalidExternalReferenceError):
            await external_service.open_issue(
                element, target="main", scope="acme/billing", title="  ", body=""
            )
        with pytest.raises(InvalidExternalReferenceError):
            await external_service.open_issue(
                element, target="main", scope="acme/billing", title="t", body="x" * 9000
            )


class TestWhatLeavesTheSystem:
    async def test_the_dry_run_lists_exactly_what_is_then_sent_and_no_catalogue_content_goes(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = (
            await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT,
                name="TOPSECRET-NAME",
                description="TOPSECRET-DESCRIPTION",
                documentation="TOPSECRET-DOC",
                properties={"owner": "TOPSECRET-OWNER"},
            )
        ).id
        asked = {"target": "main", "scope": "acme/billing", "title": "Rounding", "body": "why"}

        plan = await external_service.open_issue(element, **asked)
        await external_service.open_issue(element, **asked, dry_run=False)

        assert external_system.created == [(plan.scope, plan.title, plan.body)]
        assert "TOPSECRET" not in str(external_system.created)
        assert "TOPSECRET" not in str(plan)

    async def test_a_caller_who_may_not_write_sends_nothing_even_naming_a_real_target(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await external_service.open_issue(
                element, target="main", scope="acme/billing", title="t", body="", dry_run=False
            )

        assert external_system.created == [] and external_system.create_attempts == 0


class TestReadOnlyTargets:
    """A target is read-only unless the deployment said otherwise, and the service holds it."""

    def _service(
        self,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
        system: FakeExternalSystem,
    ) -> ExternalReferenceService:
        return ExternalReferenceService(
            external_references, service, {system.name: system}, clock=lambda: FIXED_NOW
        )

    @pytest.mark.parametrize("dry_run", [True, False])
    async def test_a_read_only_target_refuses_before_any_call_even_for_an_editor(
        self,
        external_references: InMemoryExternalReferences,
        service: ArchitectureService,
        dry_run: bool,
    ) -> None:
        readonly = FakeExternalSystem("main", writable=False)
        svc = self._service(external_references, service, readonly)
        element = await an_element(service)

        with pytest.raises(UnknownExternalTargetError, match="writable"):
            await svc.open_issue(
                element, target="main", scope="acme/billing", title="t", body="", dry_run=dry_run
            )

        assert readonly.create_attempts == 0
        assert readonly.calls == []

    @pytest.mark.parametrize("name", ["Main", " main", "main ", "MAIN", "ma\u0131n"])
    async def test_the_target_is_looked_up_as_the_server_spells_it_no_alias_no_folding(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        name: str,
    ) -> None:
        element = await an_element(service)

        with pytest.raises(UnknownExternalTargetError):
            await external_service.open_issue(
                element, target=name, scope="acme/billing", title="t", body="", dry_run=False
            )

        assert external_system.create_attempts == 0

    async def test_a_dry_run_reads_nothing_and_writes_nothing_at_the_provider(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        await external_service.open_issue(
            element, target="main", scope="acme/billing", title="t", body=""
        )

        assert external_system.calls == []
        assert external_system.create_attempts == 0

    async def test_a_scope_outside_the_list_is_refused_on_a_dry_run_too(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
    ) -> None:
        element = await an_element(service)

        with pytest.raises(ExternalRefusedError):
            await external_service.open_issue(
                element, target="main", scope="acme/payroll", title="t", body=""
            )

        assert external_system.create_attempts == 0


class TestLogs:
    async def test_the_logs_name_the_ids_and_never_the_identifier_or_the_title(
        self,
        external_service: ExternalReferenceService,
        service: ArchitectureService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        element = await an_element(service)

        with caplog.at_level(logging.DEBUG):
            await external_service.link(element, kind=GITHUB, reference="acme/billing#12")

        assert "acme/billing" not in caplog.text
        assert "Fix invoice rounding" not in caplog.text
