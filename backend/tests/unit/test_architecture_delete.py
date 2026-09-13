"""Deleting an element when the second store refuses to follow.

An element lives in Neo4j and its markdown in PostgreSQL, and no transaction
spans the two (docs/adr/0017). The graph goes first, so the one failure left
to decide is the relational discard failing *after* the node is already gone.

That deletion cannot be taken back, and it is exactly what the caller asked
for: reporting a failure would tell them the element still exists when it does
not, and a retry would answer 404. So the delete succeeds, and the rows it
could not reach are logged at ERROR with the element's id — the only key that
lets someone find the orphans the ADR accepts, instead of discovering them by
counting.
"""

from __future__ import annotations

import logging
from uuid import UUID

import pytest

from ea.domain.archimate import ElementType as E
from ea.services.architecture import ArchitectureService
from tests.conftest import FIXED_NOW, InMemoryRepository

pytestmark = pytest.mark.asyncio


class RefusingAttachments:
    """A relational store that went away between the two deletions."""

    def __init__(self) -> None:
        self.asked_for: list[UUID] = []

    async def discard_for_element(self, element_id: UUID) -> int:
        self.asked_for.append(element_id)
        msg = "connection to PostgreSQL lost"
        raise ConnectionError(msg)


@pytest.fixture
def attachments() -> RefusingAttachments:
    return RefusingAttachments()


@pytest.fixture
def fragile_service(
    repository: InMemoryRepository, attachments: RefusingAttachments
) -> ArchitectureService:
    return ArchitectureService(repository, clock=lambda: FIXED_NOW, attachments=attachments)


async def test_the_element_is_deleted_even_when_its_documents_cannot_be(
    fragile_service: ArchitectureService,
    repository: InMemoryRepository,
    attachments: RefusingAttachments,
) -> None:
    """The graph deletion is irreversible and is what was asked: it is not a failure."""
    element = await fragile_service.create_element(element_type=E.NODE, name="db-01")

    await fragile_service.delete_element(element.id)

    assert element.id not in repository.elements
    assert attachments.asked_for == [element.id]


async def test_the_documents_left_behind_are_logged_as_an_error_with_the_element_id(
    fragile_service: ArchitectureService, caplog: pytest.LogCaptureFixture
) -> None:
    """The id in `extra=` is how the orphans get found — a message is not a key."""
    element = await fragile_service.create_element(element_type=E.NODE, name="db-01")
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="ea.services"):
        await fragile_service.delete_element(element.id)

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    (error,) = errors
    assert error.element_id == str(element.id)  # type: ignore[attr-defined]
    assert error.exc_info is not None, "the cause belongs in the log, not in a guess"


async def test_the_audit_line_still_says_the_element_is_gone(
    fragile_service: ArchitectureService, caplog: pytest.LogCaptureFixture
) -> None:
    """The element *was* deleted; the audit trail must not lose that write."""
    element = await fragile_service.create_element(element_type=E.NODE, name="db-01")
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="ea.services"):
        await fragile_service.delete_element(element.id)

    deleted = [record for record in caplog.records if getattr(record, "action", None) == "deleted"]
    assert [record.element_id for record in deleted] == [str(element.id)]  # type: ignore[attr-defined]


async def test_a_delete_that_discards_cleanly_logs_no_error(
    service: ArchitectureService, caplog: pytest.LogCaptureFixture
) -> None:
    """The ERROR line means orphans exist; it must never be written when none do."""
    element = await service.create_element(element_type=E.NODE, name="db-01")
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="ea.services"):
        await service.delete_element(element.id)

    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
