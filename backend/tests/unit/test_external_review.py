"""A reference is written under review like a document: not on a locked or approved element.

`WriteKind.EXTERNAL_REFERENCE` (docs/adr/0065): refused on a locked element and on an approved
one until an approver reopens it, never bumping the revision. Link, unlink and a refresh that
changes the cached title all pass the one guard; a refresh that changes nothing does not need it.
"""

from __future__ import annotations

import pytest

from ea.domain.approval import ApprovalState, WriteKind, guard_element_write
from ea.domain.archimate import ElementType
from ea.domain.errors import ElementLockedError
from ea.domain.external import ExternalKind, ExternalSnapshot, ExternalStatus
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.external import ExternalReferenceService
from tests.conftest import (
    FIXED_NOW,
    FakeExternalSystem,
    InMemoryRepository,
)

GITHUB = ExternalKind.GITHUB_ISSUE


def an_element(**over: object) -> Element:
    element = Element.create(element_type=ElementType.NODE, name="db", now=FIXED_NOW)
    from dataclasses import replace

    return replace(element, **over)  # type: ignore[arg-type]


class TestTheRule:
    def test_draft_and_in_review_accept_it(self) -> None:
        for state in (ApprovalState.DRAFT, ApprovalState.IN_REVIEW):
            guard_element_write(an_element(approval_state=state), WriteKind.EXTERNAL_REFERENCE)

    @pytest.mark.parametrize("state", [ApprovalState.APPROVED, ApprovalState.IN_REVIEW])
    def test_locked_refuses_everybody(self, state: ApprovalState) -> None:
        with pytest.raises(ElementLockedError):
            guard_element_write(
                an_element(approval_state=state, locked=True), WriteKind.EXTERNAL_REFERENCE
            )

    def test_approved_refuses_even_an_approver_until_it_is_reopened(self) -> None:
        from tests.conftest import an_approver

        with pytest.raises(ElementLockedError, match="reopen"):
            guard_element_write(
                an_element(approval_state=ApprovalState.APPROVED),
                WriteKind.EXTERNAL_REFERENCE,
                an_approver(),
            )


async def frozen(service: ArchitectureService, repository: InMemoryRepository, **state: object):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    element = await service.create_element(
        element_type=ElementType.APPLICATION_COMPONENT, name="Billing"
    )
    repository.elements[element.id] = replace(repository.elements[element.id], **state)  # type: ignore[arg-type]
    return element.id


@pytest.mark.asyncio
class TestTheService:
    @pytest.mark.parametrize(
        "state",
        [{"approval_state": ApprovalState.APPROVED}, {"locked": True}],
        ids=["approved", "locked"],
    )
    async def test_link_unlink_and_a_changing_refresh_are_refused_on_a_frozen_element(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        repository: InMemoryRepository,
        state: dict[str, object],
    ) -> None:
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Billing"
        )
        view = await external_service.link(element.id, kind=GITHUB, reference="acme/billing#12")
        from dataclasses import replace

        repository.elements[element.id] = replace(repository.elements[element.id], **state)  # type: ignore[arg-type]
        external_system.snapshots["acme/billing#12"] = ExternalSnapshot.of(
            "Renamed", ExternalStatus.CLOSED
        )
        before_revision = repository.elements[element.id].revision

        with pytest.raises(ElementLockedError):
            await external_service.link(element.id, kind=GITHUB, reference="acme/billing#13")
        with pytest.raises(ElementLockedError):
            await external_service.unlink(view.reference.id)
        with pytest.raises(ElementLockedError):
            await external_service.refresh(view.reference.id)

        assert [v.reference.title for v in await external_service.list_for_element(element.id)] == [
            "Fix invoice rounding"
        ]
        assert repository.elements[element.id].revision == before_revision

    async def test_a_refresh_that_changes_nothing_is_not_blocked_and_a_batch_skips_frozen_lines(
        self,
        external_service: ExternalReferenceService,
        external_system: FakeExternalSystem,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Billing"
        )
        await external_service.link(element.id, kind=GITHUB, reference="acme/billing#12")
        from dataclasses import replace

        repository.elements[element.id] = replace(repository.elements[element.id], locked=True)

        views = await external_service.refresh_element(element.id)  # nothing changes: no refusal
        assert len(views) == 1

        external_system.snapshots["acme/billing#12"] = ExternalSnapshot.of(
            "Renamed", ExternalStatus.CLOSED
        )
        views = await external_service.refresh_element(element.id)  # a change: skipped, no error

        assert [v.reference.title for v in views] == ["Fix invoice rounding"]
