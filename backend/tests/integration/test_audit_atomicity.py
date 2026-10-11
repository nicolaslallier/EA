"""An event is in the database if and only if the write it describes is."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, AuditFilter, EntityKind
from ea.domain.errors import DuplicateElementError, ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def event_for(action: AuditAction, entity_id: object) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=NOW,
        actor_sub="editor-1",
        actor_name="editor",
        action=action,
        entity_kind=action.kind,
        entity_id=entity_id,  # type: ignore[arg-type]
        before=None,
        after={"x": 1},
        request_id="req",
        channel=AuditChannel.HTTP,
    )


def an_element(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


async def test_a_refused_write_leaves_no_event_and_a_deletion_leaves_its_trace(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    first, clone = an_element("db-01"), an_element("db-01")

    await graph.add_element(first, audit=(event_for(AuditAction.ELEMENT_CREATED, first.id),))
    with pytest.raises(DuplicateElementError):
        await graph.add_element(clone, audit=(event_for(AuditAction.ELEMENT_CREATED, clone.id),))
    ghost = Relationship.between(R.ASSOCIATION, first, an_element("other"), now=NOW)
    with pytest.raises(ElementNotFoundError):
        await graph.add_relationship(
            ghost, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, ghost.id),)
        )
    assert (
        await graph.delete_element(
            uuid4(), audit=(event_for(AuditAction.ELEMENT_DELETED, uuid4()),)
        )
        is False
    )

    only_the_first = await trail.page(AuditFilter())
    assert [e.entity_id for e in only_the_first.events] == [first.id]

    assert await graph.delete_element(
        first.id, audit=(event_for(AuditAction.ELEMENT_DELETED, first.id),)
    )
    history = await trail.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=first.id))
    assert [e.action for e in history.events] == [
        AuditAction.ELEMENT_DELETED,
        AuditAction.ELEMENT_CREATED,
    ]
    assert await graph.get_element(first.id) is None  # the element is gone, its story is not


async def test_a_save_and_a_link_write_their_events_with_them(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    left, right = an_element("a"), an_element("b")
    await graph.add_element(left)
    await graph.add_element(right)

    renamed = left.rename("a2", now=NOW)
    await graph.save_element(renamed, audit=(event_for(AuditAction.ELEMENT_UPDATED, left.id),))
    link = Relationship.between(R.ASSOCIATION, renamed, right, now=NOW)
    await graph.add_relationship(
        link, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, link.id),)
    )
    await graph.delete_relationship(
        link.id, audit=(event_for(AuditAction.RELATIONSHIP_DELETED, link.id),)
    )

    actions = [e.action for e in (await trail.page(AuditFilter())).events]
    assert sorted(a.value for a in actions) == [
        "element.updated",
        "relationship.created",
        "relationship.deleted",
    ]


async def test_attachments_of_names_what_the_foreign_keys_would_take(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.documents import Document
    from ea.repositories.diagram_store import PostgresDiagramRepository
    from ea.repositories.document_store import PostgresDocumentRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    element = await graph.add_element(an_element("db-01"))
    document = await PostgresDocumentRepository(sessions).add(
        Document.create(element_id=element.id, filename="a.md", content="# A\n", now=NOW)
    )
    diagrams = PostgresDiagramRepository(sessions)
    diagram = await diagrams.add(Diagram.create(name="Vue", now=NOW))
    await diagrams.replace_layout(diagram.id, [DiagramNode(element.id, 0, 0)], now=NOW)

    attached = await graph.attachments_of(element.id)

    assert attached.document_ids == (document.id,)
    assert attached.diagram_ids == (diagram.id,)
    assert (await graph.attachments_of(uuid4())).document_ids == ()
