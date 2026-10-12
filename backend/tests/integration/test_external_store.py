"""The `external_references` table against a real PostgreSQL (docs/adr/0073)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, EntityKind
from ea.domain.errors import (
    DuplicateExternalReferenceError,
    ElementLockedError,
    ElementNotFoundError,
)
from ea.domain.external import ExternalKind, ExternalReference, ExternalSnapshot, ExternalStatus
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.external_store import PostgresExternalReferenceRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def event(action: AuditAction, entity_id: object) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=NOW,
        actor_sub="sub",
        actor_name="editor",
        action=action,
        entity_kind=EntityKind.EXTERNAL_REFERENCE,
        entity_id=entity_id,  # type: ignore[arg-type]
        before=None,
        after={"ok": True},
        request_id="r",
        channel=AuditChannel.HTTP,
    )


def reference(element_id: object, number: int = 12, **over: object) -> ExternalReference:
    fields: dict[str, object] = {
        "element_id": element_id,
        "kind": ExternalKind.GITHUB_ISSUE,
        "target": "main",
        "external_id": f"acme/billing#{number}",
        "url": f"https://github.test/acme/billing/issues/{number}",
        "snapshot": ExternalSnapshot.of("Fix", ExternalStatus.OPEN),
        "created_by": "editor",
        "now": NOW,
    }
    return ExternalReference.create(**{**fields, **over})  # type: ignore[arg-type]


async def make(engine: AsyncEngine) -> tuple[PostgresExternalReferenceRepository, object, object]:
    sessions = create_session_factory(engine)
    graph = PostgresArchitectureRepository(sessions)
    element = await graph.add_element(
        Element.create(element_type=E.APPLICATION_COMPONENT, name="Billing", now=NOW), audit=()
    )
    return PostgresExternalReferenceRepository(sessions), element.id, graph


async def test_a_reference_round_trips_with_its_cache_and_leaves_one_event(
    engine_at_head: AsyncEngine,
) -> None:
    store, element_id, _ = await make(engine_at_head)
    row = reference(element_id)

    await store.add(row, audit=(event(AuditAction.EXTERNAL_REFERENCE_LINKED, row.id),))

    assert await store.get(row.id) == row
    assert await store.list_for(element_id) == (row,)  # type: ignore[arg-type]
    async with engine_at_head.connect() as connection:
        count = await connection.scalar(
            text("select count(*) from audit_events where action = 'external_reference.linked'")
        )
    assert count == 1


async def test_the_same_object_twice_on_one_element_is_refused_by_the_constraint(
    engine_at_head: AsyncEngine,
) -> None:
    store, element_id, _ = await make(engine_at_head)
    await store.add(reference(element_id), audit=())

    with pytest.raises(DuplicateExternalReferenceError):
        await store.add(reference(element_id), audit=())


async def test_another_target_or_another_element_may_cite_it(engine_at_head: AsyncEngine) -> None:
    store, element_id, graph = await make(engine_at_head)
    other = await graph.add_element(  # type: ignore[attr-defined]
        Element.create(element_type=E.NODE, name="db", now=NOW), audit=()
    )
    await store.add(reference(element_id), audit=())
    await store.add(reference(element_id, target="mirror"), audit=())
    await store.add(reference(other.id), audit=())


async def test_a_reference_to_a_missing_element_is_a_not_found_not_a_driver_error(
    engine_at_head: AsyncEngine,
) -> None:
    store, _, _ = await make(engine_at_head)

    with pytest.raises(ElementNotFoundError):
        await store.add(reference(uuid4()), audit=())


async def test_deleting_the_element_takes_its_references(engine_at_head: AsyncEngine) -> None:
    store, element_id, graph = await make(engine_at_head)
    row = await store.add(reference(element_id), audit=())

    await graph.delete_element(element_id, audit=())  # type: ignore[attr-defined]

    assert await store.get(row.id) is None


async def test_the_database_itself_refuses_an_unknown_state_and_an_empty_kind(
    engine_at_head: AsyncEngine,
) -> None:
    _, element_id, _ = await make(engine_at_head)
    insert = text(
        "insert into external_references (id, element_id, kind, external_id, url, status, "
        "created_by, created_at) "
        "values (:id, :element, :kind, 'x', 'https://x', :status, 'u', now())"
    )
    for kind, status in (("github_issue", "weird"), ("", "open")):
        with pytest.raises(IntegrityError):
            async with engine_at_head.begin() as connection:
                await connection.execute(
                    insert, {"id": uuid4(), "element": element_id, "kind": kind, "status": status}
                )


async def test_a_snapshot_updates_the_cache_and_a_no_change_writes_no_event(
    engine_at_head: AsyncEngine,
) -> None:
    store, element_id, _ = await make(engine_at_head)
    row = await store.add(reference(element_id), audit=())
    later = NOW + timedelta(hours=30)
    changed = ExternalSnapshot.of("Fix (done)", ExternalStatus.CLOSED)

    updated = await store.update_snapshot(
        row.id,
        changed,
        now=later,
        audit=lambda before: (event(AuditAction.EXTERNAL_REFERENCE_REFRESHED, before.id),),
    )
    unchanged = await store.update_snapshot(row.id, changed, now=later, audit=lambda before: ())

    assert updated is not None and unchanged is not None
    assert (updated.title, updated.status, updated.synced_at) == (
        "Fix (done)",
        ExternalStatus.CLOSED,
        later,
    )
    assert await store.update_snapshot(uuid4(), changed, now=later, audit=lambda b: ()) is None
    async with engine_at_head.connect() as connection:
        count = await connection.scalar(
            text("select count(*) from audit_events where action = 'external_reference.refreshed'")
        )
    assert count == 1


async def test_the_stale_ones_come_oldest_first_and_only_of_a_resolved_kind(
    engine_at_head: AsyncEngine,
) -> None:
    store, element_id, _ = await make(engine_at_head)
    old = await store.add(reference(element_id, 1, now=NOW - timedelta(days=3)), audit=())
    never = await store.add(reference(element_id, 2, snapshot=None), audit=())
    await store.add(reference(element_id, 3), audit=())  # fresh
    await store.add(
        ExternalReference.create(
            element_id=element_id,  # type: ignore[arg-type]
            kind=ExternalKind.URL,
            target="",
            external_id="https://wiki.test/a",
            url="https://wiki.test/a",
            snapshot=None,
            created_by="e",
            now=NOW,
        ),
        audit=(),
    )

    stale = await store.list_stale(before=NOW - timedelta(hours=24), limit=10)

    assert [r.id for r in stale] == [never.id, old.id]  # never-synced first
    assert len(await store.list_stale(before=NOW - timedelta(hours=24), limit=1)) == 1


async def test_a_hostile_stored_row_still_reads_back(engine_at_head: AsyncEngine) -> None:
    store, element_id, _ = await make(engine_at_head)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "insert into external_references (id, element_id, kind, target, external_id, url, "
                "title, status, created_by, created_at) values (:id, :e, 'github_issue', 'main', "
                "'../../etc#x', 'javascript:alert(1)', '<script>', 'open', 'u', now())"
            ),
            {"id": uuid4(), "e": element_id},
        )

    (row,) = await store.list_for(element_id)  # type: ignore[arg-type]

    assert row.external_id == "../../etc#x"  # read as text; the connector re-validates it
    assert row.title == "<script>"


async def put_in_state(
    engine: AsyncEngine, element_id: object, state: str, *, locked: bool = False
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET approval_state = :s, locked = :l WHERE id = :id"),
            {"s": state, "l": locked, "id": element_id},
        )


async def revision_of(engine: AsyncEngine, element_id: object) -> int:
    async with engine.connect() as connection:
        return int(
            await connection.scalar(
                text("SELECT revision FROM elements WHERE id = :id"), {"id": element_id}
            )
            or 0
        )


@pytest.mark.parametrize(("state", "locked"), [("approved", False), ("approved", True)])
async def test_a_frozen_element_accepts_no_link_no_unlink_and_no_changing_refresh(
    engine_at_head: AsyncEngine, state: str, locked: bool
) -> None:
    store, element_id, _ = await make(engine_at_head)
    row = await store.add(reference(element_id), audit=())
    await put_in_state(engine_at_head, element_id, state, locked=locked)
    revision = await revision_of(engine_at_head, element_id)
    changed = ExternalSnapshot.of("Renamed", ExternalStatus.CLOSED)

    with pytest.raises(ElementLockedError):
        await store.add(reference(element_id, 13), audit=())
    with pytest.raises(ElementLockedError):
        await store.delete(row.id, audit=())
    with pytest.raises(ElementLockedError):
        await store.update_snapshot(row.id, changed, now=NOW, audit=lambda b: ())

    assert await store.get(row.id) == row
    # a refresh that changes nothing is only a clock: it goes through
    same = ExternalSnapshot.of(row.title, row.status)
    assert await store.update_snapshot(row.id, same, now=NOW, audit=lambda b: ()) is not None
    assert await revision_of(engine_at_head, element_id) == revision


async def test_a_reopened_element_accepts_references_again_without_a_new_revision(
    engine_at_head: AsyncEngine,
) -> None:
    store, element_id, _ = await make(engine_at_head)
    await put_in_state(engine_at_head, element_id, "in_review")
    before = await revision_of(engine_at_head, element_id)

    await store.add(reference(element_id), audit=())

    assert await revision_of(engine_at_head, element_id) == before
